"""
Photos MCP tools for Julie Memory (macOS Photos via osxphotos)

Capabilities:
- Search photos by people labels, places, dates, labels, albums
- Return metadata including UUID, dates, location, people, labels, albums
- View photos inline by UUID (returns images for display in conversation)
- Export photos to disk (previews or originals)

Requirements:
- macOS Photos library on this machine
- Full Disk Access to the running process (e.g., Terminal / server binary)
- Python package: osxphotos (declared in pyproject)
"""

from __future__ import annotations

import base64
import json as _json
import datetime as _dt
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import structlog
from fastmcp.utilities.types import Image

from .server import mcp  # reuse the same FastMCP instance
from .server import db_manager  # database manager for fetching names
from memory_database.utils.identity_resolver import resolve_person_selector
from memory_database.models import IdentityClaim, Principal
from memory_database.utils.normalization import normalize_identity_value

logger = structlog.get_logger()

PHOTOS_AVAILABLE = False
PHOTOS_IMPORT_ERROR: Optional[str] = None

try:  # soft import so the rest of the server runs without Photos
    from osxphotos import PhotosDB, PhotoInfo  # type: ignore

    PHOTOS_AVAILABLE = True
except Exception as e:  # pragma: no cover - environment-dependent
    PHOTOS_AVAILABLE = False
    PHOTOS_IMPORT_ERROR = str(e)


# -------- Helpers ---------

def _parse_date(date_str: Optional[str], *, is_end: bool = False) -> Optional[_dt.datetime]:
    if not date_str:
        return None
    # Accept date or datetime; assume local timezone for date-only
    # Try parsing as date first (YYYY-MM-DD) to properly handle is_end flag
    try:
        d = _dt.date.fromisoformat(date_str)
        # For end boundaries given as a date without time, include the full day
        return _dt.datetime.combine(d, _dt.time.max if is_end else _dt.time.min)
    except Exception:
        pass
    # Try parsing as full datetime (with time component)
    try:
        return _dt.datetime.fromisoformat(date_str)
    except Exception:
        raise ValueError(f"Invalid date format: {date_str}; use YYYY-MM-DD or ISO 8601")


def _safe_photo_fields(p: "PhotoInfo") -> Dict[str, Any]:
    # Gather commonly useful fields, handling None safely
    loc = p.location  # (lat, lon) tuple or None
    lat, lon = (loc[0], loc[1]) if loc else (None, None)
    # place info: osxphotos provides place_name and place fields
    try:
        place = getattr(p, "place", None)  # may be a tuple of place hierarchy
    except Exception:
        place = None
    place_name = None
    if place:
        # p.place may be a tuple of names; fallback to display string
        try:
            place_name = ", ".join([x for x in place if x])
        except Exception:
            place_name = str(place)

    return {
        "uuid": p.uuid,
        "created": p.date.isoformat() if getattr(p, "date", None) else None,
        "modified": p.date_modified.isoformat() if getattr(p, "date_modified", None) else None,
        "is_edited": bool(getattr(p, "hasadjustments", False)),
        "media_type": getattr(p, "uti", None),
        "favorite": bool(getattr(p, "favorite", False)),
        "hidden": bool(getattr(p, "hidden", False)),
        "albums": list(getattr(p, "albums", [])),
        "keywords": list(getattr(p, "keywords", [])),
        "persons": list(getattr(p, "persons", [])),
        # persons_uuids may be filled in later if include_faces is requested
        "labels": list(getattr(p, "labels", [])),  # ML scene/object labels
        "lat": lat,
        "lon": lon,
        "place": place_name,
        "original_path": getattr(p, "path", None),
        "edited_path": getattr(p, "path_edited", None),
    }


def _require_photos() -> Tuple[bool, Optional[str]]:
    if not PHOTOS_AVAILABLE:
        return False, (
            "osxphotos not available. Install dependency and grant Full Disk Access. "
            f"Import error: {PHOTOS_IMPORT_ERROR}"
        )
    # Try opening the DB to surface FDA issues quickly
    try:
        _ = PhotosDB()
        return True, None
    except Exception as e:
        return False, (
            "Failed to open Photos library. Ensure this process has Full Disk Access "
            f"and Photos library exists. Error: {e}"
        )


# Tolerant parsers for common input mistakes (stringified arrays/objects, CSV, single strings)
def _parse_listish(value: Optional[Union[List[str], str]]) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(x) for x in value if isinstance(x, (str, int, float)) and str(x).strip()]
    if isinstance(value, str):
        s = value.strip()
        # Try JSON first
        try:
            loaded = _json.loads(s)
            if isinstance(loaded, list):
                return [str(x) for x in loaded if isinstance(x, (str, int, float)) and str(x).strip()]
            if isinstance(loaded, (str, int, float)) and str(loaded).strip():
                return [str(loaded)]
        except Exception:
            pass
        # Fallback: CSV or single token
        if "," in s:
            return [part.strip() for part in s.split(",") if part.strip()]
        if s:
            return [s]
    return None


def _parse_objectish(value: Optional[Union[Dict[str, Any], str]]) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        s = value.strip()
        try:
            loaded = _json.loads(s)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            # Not JSON; treat as a bare name/email/phone and wrap
            if s:
                # Heuristic: very simple patterns
                if "@" in s:
                    return {"email": s}
                if any(c.isdigit() for c in s):
                    return {"phone": s}
                return {"name": s}
    return None


# -------- MCP Tools ---------

@mcp.tool
def photos_search(
    people: Optional[Union[List[str], str]] = None,
    person_uuids: Optional[Union[List[str], str]] = None,
    uuids: Optional[Union[List[str], str]] = None,
    place: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    labels: Optional[Union[List[str], str]] = None,
    albums: Optional[Union[List[str], str]] = None,
    keywords: Optional[Union[List[str], str]] = None,
    limit: int = 50,
    include_faces: bool = False,
    # Unified selector (preferred)
    person: Optional[Union[Dict[str, Any], str]] = None,
    # Convenience aliases (optional; will be normalized to `person` if provided)
    person_id: Optional[str] = None,
    person_email: Optional[str] = None,
    person_phone: Optional[str] = None,
    person_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search Julie's macOS Photos library by people, location, date, content, and more.

    **PROACTIVE USAGE - Use this tool when the user asks about:**
    - Photos with specific people: "Show me photos of Sarah"
    - Photos from locations: "Photos from my trip to Paris"
    - Photos from time periods: "Photos from last summer"
    - Photos with specific content: "Photos of my cat"
    - Photos from albums: "Photos in my Vacation album"

    This tool searches Julie's macOS Photos library and can integrate with her contact
    database to resolve people by name, email, or phone number.

    **RECOMMENDED WORKFLOW:**
    1. Use photos_search() with person parameter - resolution happens automatically
    2. Pass person={"name": "..."} or {"email": "..."} or {"phone": "..."}
    3. Use view_photos() after getting UUIDs to display photos inline

    **Common usage patterns:**
    - User: "Show me photos of Sarah from last year"
      → photos_search(person={"name": "Sarah"}, date_from="2024-01-01", date_to="2024-12-31")
    - User: "Find photos from my Paris trip"
      → photos_search(place="Paris", date_from="2024-06-01", date_to="2024-06-30")
    - User: "Photos of my cat in the Pets album"
      → photos_search(labels=["cat"], albums=["Pets"])
    - User: "Recent photos with john@example.com"
      → photos_search(person={"email": "john@example.com"}, date_from="2024-01-01")

    **Search Parameters:**

    People filters (choose one or more):
    - people: List of person names from Photos People labels (fuzzy matched). Must be a JSON array of strings.
      Example: {"people": ["Ching-Tse Chen"]} (NOT {"people": "[\"Ching-Tse Chen\"]"}).
    - person: Structured selector {id?, email?, phone?, name?, username?, contact_id?}. Must be an object, not a JSON string.
      Example: {"person": {"id": "01ABC..."}} (NOT {"person": "{\\"id\\": \\\"01ABC...\\"}"}).
    - person_uuids: Photos-specific person UUIDs
    - Convenience aliases (optional): person_id, person_email, person_phone, person_name

    Content filters:
    - labels: ML-detected content (e.g., ["dog", "beach", "sunset"])
    - albums: Album names (e.g., ["Vacation", "Family"])
    - keywords: User-added keywords in Photos

    Location & Time:
    - place: Location name (e.g., "San Francisco", "Golden Gate Park")
    - date_from/date_to: ISO date strings (e.g., "2024-01-01" or "2024-01-01T10:00:00").
      If you pass date-only strings:
        - date_from uses start-of-day (00:00:00, local time)
        - date_to uses end-of-day (23:59:59.999999, local time)
      Example: date_from="2025-08-23", date_to="2025-08-23" matches the entire day on Aug 23.

    Other:
    - uuids: Filter to specific photo UUIDs
    - include_faces: Include face detection data with bounding boxes
    - limit: Maximum results (default: 50)

    Args:
        people: Names to match against Photos People labels
        person_uuids: Photos-specific person UUIDs
        uuids: Specific photo UUIDs to retrieve
        place: Location name (fuzzy matched)
        date_from: Start date (ISO format). Date-only→start-of-day local time.
        date_to: End date (ISO format). Date-only→end-of-day local time.
        labels: ML scene/object labels that must all be present
        albums: Album names to search within
        keywords: User-assigned keywords
        limit: Maximum number of results to return (default: 50)
        include_faces: Include face detection data with bounding boxes
        person: Structured selector to resolve via contact database
        person_id/person_email/person_phone/person_name: Convenience alternatives to build `person`

    Returns:
        Dictionary containing:
        - photos: List of photo metadata with UUID, dates, location, people, labels, paths
        - total_found: Number of results returned
        - criteria: Search criteria that were applied
        - error: Error message if search failed (optional)

    Photo metadata includes:
    - uuid: Unique photo identifier (use with view_photos or photos_export)
    - created/modified: ISO timestamp strings
    - persons: List of people names from Photos
    - labels: ML-detected content labels
    - albums: Albums containing this photo
    - lat/lon: GPS coordinates if available
    - place: Location name if available
    - original_path/edited_path: File paths (may be None if in iCloud)
    - faces: Face detection data (only if include_faces=True)
    """
    ok, err = _require_photos()
    if not ok:
        return {"error": err, "photos": [], "total_found": 0}

    # Normalize tolerant inputs up-front
    people = _parse_listish(people)
    person_uuids = _parse_listish(person_uuids)
    uuids = _parse_listish(uuids)
    labels = _parse_listish(labels)
    albums = _parse_listish(albums)
    keywords = _parse_listish(keywords)
    person = _parse_objectish(person)

    # Fold convenience aliases into `person` if present
    if person is None:
        person = {}
    if isinstance(person, dict):
        if person_id and "id" not in person:
            person["id"] = person_id
        if person_email and "email" not in person:
            person["email"] = person_email
        if person_phone and "phone" not in person:
            person["phone"] = person_phone
        if person_name and "name" not in person:
            person["name"] = person_name

    try:
        start = _parse_date(date_from)
        end = _parse_date(date_to, is_end=True)
    except ValueError as e:
        return {"error": str(e), "photos": [], "total_found": 0}

    try:
        db = PhotosDB()
        # Build filters using current osxphotos signature (persons, albums, keywords, from_date, to_date)
        query_kwargs: Dict[str, Any] = {}
        # Asset UUID filtering
        if uuids:
            query_kwargs["uuid"] = list(uuids)

        # People filters: names via direct input or identity resolution
        desired_names: List[str] = []

        # Unified person resolution and Photos linkage
        resolved_principal: Optional[Principal] = None
        person_resolution: Dict[str, Any] = {
            "status": "unresolved",
            "principal": None,
            "photos_link": None,
            "used_names": [],
            "candidates": [],
            "confidence": 0.0,
        }
        photos_person_uuid: Optional[str] = None

        if person:
            try:
                with db_manager.get_session() as _session:  # type: ignore[attr-defined]
                    resolved_principal = resolve_person_selector(_session, person)
                    if resolved_principal:
                        person_resolution["principal"] = {
                            "id": resolved_principal.id,
                            "display_name": resolved_principal.display_name,
                        }
                        # Check for Photos link (person_uuid)
                        claim = (
                            _session.query(IdentityClaim)
                            .filter(
                                IdentityClaim.principal_id == resolved_principal.id,
                                IdentityClaim.platform == "photos",
                                IdentityClaim.kind == "person_uuid",
                            )
                            .first()
                        )
                        if claim:
                            photos_person_uuid = claim.value
                            person_resolution["photos_link"] = {"person_uuid": claim.value}
                            person_resolution["status"] = "linked"
                        else:
                            # Build heuristic candidates from display_name + alias claims
                            candidate_names: List[str] = []
                            if getattr(resolved_principal, "display_name", None):
                                candidate_names.append(resolved_principal.display_name)
                            claims = list(getattr(resolved_principal, "identity_claims", []) or [])
                            if not claims:
                                claims = (
                                    _session.query(IdentityClaim)
                                    .filter(IdentityClaim.principal_id == resolved_principal.id)
                                    .all()
                                )
                            for c in claims:
                                if c.kind in ("display_name", "alias") and c.value:
                                    candidate_names.append(c.value)
                            desired_names.extend(sorted(set(candidate_names)))
                            person_resolution["candidates"] = sorted(set(candidate_names))
                            person_resolution["status"] = "heuristic"
            except Exception as _e:
                logger.warning("person resolution failed", error=str(_e))

        # If Photos link exists, translate person_uuid to the Photos People name
        if photos_person_uuid:
            try:
                pi_list = getattr(db, "person_info", []) or []
                uuid_to_name = {getattr(pi, "uuid", None): getattr(pi, "name", None) for pi in pi_list}
                linked_name = uuid_to_name.get(photos_person_uuid)
                if linked_name:
                    desired_names.append(linked_name)
                    person_resolution["used_names"] = [linked_name]
            except Exception:
                pass

        # 2) Include explicit people arg
        if people:
            try:
                known = getattr(db, "persons", []) or []
                expanded: List[str] = []
                for q in people:
                    ql = q.lower()
                    expanded.extend([n for n in known if ql in n.lower()])
                expanded = sorted(set(expanded)) or list(people)
                desired_names.extend(expanded)
            except Exception:
                desired_names.extend(list(people))

        # 3) If caller passed specific Photos person_uuids, map them to names
        
        if person_uuids:
            try:
                pi_list = getattr(db, "person_info", []) or []
                uuid_to_name = {getattr(pi, "uuid", None): getattr(pi, "name", None) for pi in pi_list}
                for pu in person_uuids:
                    name = uuid_to_name.get(pu)
                    if name:
                        desired_names.append(name)
            except Exception:
                pass

        if desired_names:
            query_kwargs["persons"] = sorted(set(desired_names))
        if albums:
            query_kwargs["albums"] = list(albums)
        if keywords:
            query_kwargs["keywords"] = list(keywords)
        if start:
            query_kwargs["from_date"] = start
        if end:
            query_kwargs["to_date"] = end

        photos = db.photos(**query_kwargs)  # type: ignore[arg-type]
        if limit and len(photos) > limit:
            photos = photos[:limit]

        # Apply additional in-Python filters for place and labels, not supported as query args in this osxphotos version
        def _match_place(p: "PhotoInfo") -> bool:
            if not place:
                return True
            try:
                pl = getattr(p, "place", None)
                if pl is None:
                    return False
                text = None
                # try to get a readable name if available
                name = getattr(pl, "name", None)
                if name:
                    text = str(name)
                else:
                    text = str(pl)
                return place.lower() in text.lower()
            except Exception:
                return False

        def _match_labels(p: "PhotoInfo") -> bool:
            if not labels:
                return True
            try:
                plabels = set(getattr(p, "labels", []) or [])
                return all(l in plabels for l in labels)
            except Exception:
                return False

        filtered = [p for p in photos if _match_place(p) and _match_labels(p)]
        if limit and len(filtered) > limit:
            filtered = filtered[:limit]

        results = []
        for p in filtered:
            item = _safe_photo_fields(p)
            if include_faces:
                faces = []
                person_uuids_set = set()
                try:
                    fi_list = getattr(p, "face_info", [])
                    for fi in fi_list:
                        try:
                            bbox = None
                            if hasattr(fi, "bbox") and fi.bbox:
                                bbox = [fi.bbox[0], fi.bbox[1], fi.bbox[2], fi.bbox[3]]
                            elif hasattr(fi, "center") and hasattr(fi, "width") and hasattr(fi, "height"):
                                cx, cy = fi.center if isinstance(fi.center, tuple) else (None, None)
                                w = getattr(fi, "width", None)
                                h = getattr(fi, "height", None)
                                if cx is not None and cy is not None and w and h:
                                    bbox = [max(0.0, cx - w / 2), max(0.0, cy - h / 2), w, h]
                            # Try to resolve person uuid from face's person_info, if named
                            person_uuid = None
                            try:
                                pi = getattr(fi, "person_info", None)
                                if pi is not None:
                                    person_uuid = getattr(pi, "uuid", None)
                                    if person_uuid:
                                        person_uuids_set.add(person_uuid)
                            except Exception:
                                pass

                            face_entry: Dict[str, Any] = {
                                "person": getattr(fi, "person", None),
                                "person_uuid": person_uuid,
                                "confidence": getattr(fi, "confidence", None),
                                "bbox": bbox,
                            }

                            # If this face has a person_uuid, try mapping back to a Principal
                            if person_uuid:
                                try:
                                    with db_manager.get_session() as _session:  # type: ignore[attr-defined]
                                        claim = (
                                            _session.query(IdentityClaim)
                                            .filter(
                                                IdentityClaim.platform == "photos",
                                                IdentityClaim.kind == "person_uuid",
                                                IdentityClaim.normalized == person_uuid.lower().strip(),
                                            )
                                            .first()
                                        )
                                        if claim:
                                            pr = _session.query(Principal).get(claim.principal_id)
                                            if pr:
                                                face_entry["principal_id"] = pr.id
                                                face_entry["principal_display_name"] = pr.display_name
                                except Exception:
                                    pass

                            faces.append(face_entry)
                        except Exception:
                            continue
                except Exception:
                    faces = []
                item["faces"] = faces
                if person_uuids_set:
                    item["person_uuids"] = sorted(list(person_uuids_set))
            results.append(item)

        # Report criteria we actually used
        crit = dict(query_kwargs)
        if place:
            crit["place_contains"] = place
        if labels:
            crit["labels_contains_all"] = labels
        # Attach person resolution context
        return {"photos": results, "total_found": len(results), "criteria": crit, "person_resolution": person_resolution}
    except Exception as e:
        logger.warning("photos_search failed", error=str(e))
        return {"error": f"search failed: {e}", "photos": [], "total_found": 0}


@mcp.tool
def photos_export(
    uuids: List[str],
    destination_dir: Optional[str] = None,
    use_preview: bool = True,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Export photos from Photos library to disk by UUID.

    **WHEN TO USE THIS TOOL:**
    Use photos_export when you need to:
    - Save photos to a specific location for external processing
    - Extract photos for backup or sharing outside of Photos
    - Get file paths for photos to use with other tools
    - Create a local copy of photos for analysis

    **DO NOT use this for viewing** - use view_photos() instead to display photos
    inline in the conversation without saving to disk.

    **WORKFLOW:**
    1. First use photos_search() to find photos and get their UUIDs
    2. Then use photos_export() to save them to disk
    3. Or use view_photos() to display them directly without exporting

    **Common usage patterns:**
    - User: "Export my vacation photos to Desktop"
      → photos_search(albums=["Vacation"]) → photos_export(uuids=[...], destination_dir="~/Desktop/vacation")
    - User: "Save photos from last month"
      → photos_search(date_from="2024-11-01", date_to="2024-11-30") → photos_export(uuids=[...])
    - User: "Get high-quality versions of these photos"
      → photos_export(uuids=[...], use_preview=False)

    **Preview vs Original:**
    - use_preview=True (default): Exports Photos-generated JPEG previews
      - Pros: Fast, small file size, works even if originals are in iCloud
      - Cons: Lower quality, no RAW formats
    - use_preview=False: Exports original files or edited versions
      - Pros: Full quality, preserves original format
      - Cons: Slower, larger files, requires originals downloaded from iCloud

    Args:
        uuids: List of photo UUIDs from photos_search results
        destination_dir: Target directory path. If not provided, creates a temp directory
        use_preview: Export Photos previews (fast, smaller) vs originals (slow, full quality)
        overwrite: Whether to overwrite existing files with same name

    Returns:
        Dictionary containing:
        - destination: Path to directory where photos were exported
        - exported_files: List of full file paths for successfully exported photos
        - failed: Dict mapping UUID to error message for failed exports
        - error: General error message if export completely failed

    Notes:
        - Filenames use format: {UUID}{extension}
        - If file exists and overwrite=False, appends counter: {UUID}_1{extension}
        - Creates destination directory if it doesn't exist
        - Photos in iCloud may fail to export if not downloaded locally
    """
    ok, err = _require_photos()
    if not ok:
        return {"error": err, "exported_files": [], "failed": {}, "destination": None}

    dest_dir = destination_dir or tempfile.mkdtemp(prefix="julie-photos-")
    os.makedirs(dest_dir, exist_ok=True)

    try:
        db = PhotosDB()
        photos = db.photos(uuid=uuids)  # type: ignore[arg-type]
        exported: List[str] = []
        failed: Dict[str, str] = {}

        for p in photos:
            try:
                source_path = None

                if use_preview:
                    # Use Photos-generated preview derivatives
                    derivatives = getattr(p, 'path_derivatives', None)
                    if derivatives and isinstance(derivatives, list):
                        jpeg_derivatives = [d for d in derivatives if d.lower().endswith(('.jpeg', '.jpg'))]
                        if jpeg_derivatives:
                            source_path = jpeg_derivatives[0]

                # Fallback to edited or original
                if not source_path:
                    source_path = getattr(p, 'path_edited', None) or getattr(p, 'path', None)

                if not source_path or not os.path.exists(source_path):
                    failed[p.uuid] = "No accessible file path found"
                    continue

                # Construct destination filename
                file_ext = os.path.splitext(source_path)[1]
                dest_filename = f"{p.uuid}{file_ext}"
                dest_path = os.path.join(dest_dir, dest_filename)

                # Check if file exists and overwrite settings
                if os.path.exists(dest_path) and not overwrite:
                    # Increment filename
                    base_name = p.uuid
                    counter = 1
                    while os.path.exists(dest_path):
                        dest_filename = f"{base_name}_{counter}{file_ext}"
                        dest_path = os.path.join(dest_dir, dest_filename)
                        counter += 1

                # Copy the file
                import shutil
                shutil.copy2(source_path, dest_path)
                exported.append(dest_path)

            except Exception as ex:  # continue others
                failed[p.uuid] = str(ex)

        return {
            "destination": dest_dir,
            "exported_files": exported,
            "failed": failed,
        }
    except Exception as e:
        logger.warning("photos_export failed", error=str(e))
        return {
            "error": f"export failed: {e}",
            "exported_files": [],
            "failed": {},
            "destination": dest_dir,
        }


@mcp.tool
def view_photos(
    uuids: List[str],
    use_preview: bool = True,
) -> List[Image]:
    """
    Display photos inline in the conversation by UUID.

    **PROACTIVE USAGE - Use this tool when the user wants to:**
    - See photos: "Show me photos of my trip"
    - View photos: "Let me see the photos from last week"
    - Look at photos: "Display photos with Sarah"
    - Review photos: "I want to see my recent photos"

    This is the PRIMARY tool for showing photos to the user. It displays photos
    directly in the conversation without saving them to disk.

    **RECOMMENDED WORKFLOW:**
    1. Use photos_search() to find photos and get their UUIDs
    2. Use view_photos() to display them inline in the conversation
    3. Limit to 5-10 photos at a time for best performance

    **Common usage patterns:**
    - User: "Show me photos from my vacation"
      → photos_search(albums=["Vacation"], limit=10) → view_photos(uuids=[...])
    - User: "Let me see photos with Sarah from last month"
      → photos_search(person={"name": "Sarah"}, date_from="2024-11-01") → view_photos(uuids=[...])
    - User: "Display my most recent photos"
      → photos_search(date_from="2024-12-01", limit=5) → view_photos(uuids=[...])

    **When to use view_photos vs photos_export:**
    - view_photos: For viewing in conversation (most common)
    - photos_export: For saving to disk for external use

    **Performance Considerations:**
    - Photos are limited to 1MB each for Claude Desktop compatibility
    - Preview mode (default) is recommended for faster loading
    - Consider limiting results to 5-10 photos per call
    - Photos over 1MB are automatically skipped with a warning

    **Preview vs Original:**
    - use_preview=True (default): Uses Photos-generated JPEG previews
      - Fast loading, smaller size, works with iCloud photos
      - Recommended for viewing in conversation
    - use_preview=False: Uses original or edited versions
      - Higher quality but slower and may be too large
      - May fail if originals are in iCloud and not downloaded

    Args:
        uuids: List of photo UUIDs from photos_search results
        use_preview: Use previews (fast, recommended) vs originals (slow, high quality)

    Returns:
        List of Image objects displayed inline in conversation. Photos that fail to
        load or exceed size limits are skipped silently (check logs for details).

    Notes:
        - Images are returned in the same order as input UUIDs
        - Failed photos are skipped without error to avoid interrupting display
        - Each image must be under 1MB for Claude Desktop compatibility
        - Empty list returned if Photos library is unavailable
    """
    ok, err = _require_photos()
    if not ok:
        logger.error("Photos not available", error=err)
        return []

    try:
        db = PhotosDB()
        photos = db.photos(uuid=uuids)  # type: ignore[arg-type]
        images: List[Image] = []

        for p in photos:
            try:
                file_path = None

                if use_preview:
                    # Use Photos-generated preview derivatives (faster, smaller)
                    # path_derivatives contains pre-generated JPEGs optimized by Photos
                    derivatives = getattr(p, 'path_derivatives', None)
                    if derivatives and isinstance(derivatives, list):
                        # Use the first derivative (typically the smaller preview)
                        # Derivatives are usually in order from smallest to largest
                        jpeg_derivatives = [d for d in derivatives if d.lower().endswith('.jpeg') or d.lower().endswith('.jpg')]
                        if jpeg_derivatives:
                            file_path = jpeg_derivatives[0]
                            logger.debug("Using derivative preview", path=file_path)

                # Fallback to edited or original path if no preview found
                if not file_path:
                    # Try edited version first, then original
                    file_path = getattr(p, 'path_edited', None) or getattr(p, 'path', None)
                    logger.debug("Using original/edited photo", path=file_path, uuid=p.uuid)

                if not file_path or not os.path.exists(file_path):
                    logger.warning("No accessible file found for photo", uuid=p.uuid, path=file_path)
                    continue

                # Read the file as bytes
                with open(file_path, 'rb') as f:
                    image_bytes = f.read()

                # Check size (warn if over 1MB for Claude Desktop)
                size_mb = len(image_bytes) / (1024 * 1024)
                if size_mb > 1.0:
                    logger.warning(
                        "Image exceeds 1MB limit for Claude Desktop",
                        uuid=p.uuid,
                        size_mb=round(size_mb, 2),
                        path=file_path
                    )
                    # Skip images over 1MB to avoid errors in Claude Desktop
                    continue

                # Determine format from file extension for FastMCP Image
                file_ext = os.path.splitext(file_path)[1].lower()
                # Map to format string expected by FastMCP (without 'image/' prefix)
                format_str = {
                    '.jpg': 'jpeg',
                    '.jpeg': 'jpeg',
                    '.png': 'png',
                    '.gif': 'gif',
                    '.webp': 'webp',
                    '.heic': 'jpeg',  # HEIC will be converted or we use JPEG derivative
                }.get(file_ext, 'jpeg')  # default to jpeg

                # Create Image object from bytes
                img = Image(data=image_bytes, format=format_str)
                images.append(img)

                logger.debug(
                    "Successfully loaded photo",
                    uuid=p.uuid,
                    size_kb=round(len(image_bytes) / 1024, 2),
                    format=format_str
                )

            except Exception as ex:
                logger.warning("Failed to view photo", uuid=p.uuid, error=str(ex))
                continue

        if not images:
            logger.warning("No photos successfully loaded", requested_count=len(uuids))

        return images

    except Exception as e:
        logger.error("view_photos failed", error=str(e))
        return []


# (CLI previously provided people linking.)

# (removed view_photos_with_paths per request)

@mcp.tool
def photos_get_person_link(
    person: Union[Dict[str, Any], str]
) -> Dict[str, Any]:
    """
    Get the Photos People link (person_uuid) for a person in the contact database.

    Args:
        person: Structured selector {id?, email?, phone?, name?, username?, contact_id?}

    Returns:
        { person: {id, display_name}, photos_link: {person_uuid?} }
    """
    ok, err = _require_photos()
    if not ok:
        return {"error": err}

    try:
        person = _parse_objectish(person) or {}
        _ = PhotosDB()  # Ensure Photos DB is accessible
        with db_manager.get_session() as session:  # type: ignore[attr-defined]
            principal = resolve_person_selector(session, person)
            if not principal:
                return {"error": "person not found"}
            claim = (
                session.query(IdentityClaim)
                .filter(
                    IdentityClaim.principal_id == principal.id,
                    IdentityClaim.platform == "photos",
                    IdentityClaim.kind == "person_uuid",
                )
                .first()
            )
            result = {
                "person": {"id": principal.id, "display_name": principal.display_name},
                "photos_link": None,
            }
            if claim:
                result["photos_link"] = {"person_uuid": claim.value}
            return result
    except Exception as e:
        logger.warning("photos_get_person_link failed", error=str(e))
        return {"error": str(e)}


@mcp.tool
def photos_link_person(
    person: Union[Dict[str, Any], str],
    photos_person_uuid: str,
    photos_person_label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Link a person in the contact database to a Photos People identifier.

    Writes a platform='photos', kind='person_uuid' identity claim for the person.
    Optionally records the Photos People label as an alias on platform='photos'.
    """
    ok, err = _require_photos()
    if not ok:
        return {"success": False, "error": err}

    try:
        db = PhotosDB()
        # Validate the provided Photos person UUID exists (best-effort)
        try:
            pi_list = getattr(db, "person_info", []) or []
            valid_uuids = {getattr(pi, "uuid", None) for pi in pi_list}
            if photos_person_uuid not in valid_uuids:
                return {"success": False, "error": "photos person_uuid not found in library"}
        except Exception:
            pass

        with db_manager.get_session() as session:  # type: ignore[attr-defined]
            person = _parse_objectish(person) or {}
            principal = resolve_person_selector(session, person)
            if not principal:
                return {"success": False, "error": "person not found"}

            # Use validated write path
            from .write_tools import add_contact_identity

            add_result = add_contact_identity(
                session,
                person_id=principal.id,
                kind="person_uuid",
                value=photos_person_uuid,
                platform="photos",
                confidence=0.95,
            )
            if not add_result.get("success"):
                return add_result

            if photos_person_label:
                _ = add_contact_identity(
                    session,
                    person_id=principal.id,
                    kind="alias",
                    value=photos_person_label,
                    platform="photos",
                    confidence=0.7,
                )

            return {
                "success": True,
                "person": {"id": principal.id, "display_name": principal.display_name},
                "photos_link": {"person_uuid": photos_person_uuid, "person_label": photos_person_label},
            }
    except Exception as e:
        logger.warning("photos_link_person failed", error=str(e))
        return {"success": False, "error": str(e)}


@mcp.tool
def photos_ingest_people_links(
    dry_run: bool = False,
    overwrite_conflicts: bool = False,
    max_candidates: int = 3,
) -> Dict[str, Any]:
    """
    Scan Photos People, resolve names to contacts, and write platform='photos' person_uuid links.

    Strategy per Photos person:
    1) Exact match Principal.display_name (case-insensitive)
    2) Exact match IdentityClaim(kind in ['display_name','alias']) normalized
    3) Fuzzy search by name; only link if a single unambiguous match

    Writes identity claim: platform='photos', kind='person_uuid', value=<photos_uuid>
    Also adds alias on platform='photos' with the Photos label for context.

    Args:
        dry_run: Do not write changes; just report what would happen
        overwrite_conflicts: If a different Principal is already linked to the same person_uuid, reassign link (use with caution)
        max_candidates: When fuzzy search returns multiple, include up to N in report
    """
    ok, err = _require_photos()
    if not ok:
        return {"success": False, "error": err}

    try:
        db = PhotosDB()
        # Build list of Photos People (uuid, name)
        people = []
        try:
            pi_list = getattr(db, "person_info", []) or []
            for pi in pi_list:
                try:
                    uuid = getattr(pi, "uuid", None)
                    name = getattr(pi, "name", None)
                except Exception:
                    uuid = None
                    name = None
                if uuid and name:
                    people.append({"uuid": uuid, "name": name})
        except Exception:
            # Fallback via db.persons if needed (names only)
            for name in getattr(db, "persons", []) or []:
                people.append({"uuid": None, "name": name})

        stats = {
            "scanned": len(people),
            "linked": 0,
            "already_linked": 0,
            "ambiguous": 0,
            "unmatched": 0,
            "conflicts": 0,
            "errors": 0,
            "items": [],
        }

        with db_manager.get_session() as session:  # type: ignore[attr-defined]
            for person_info in people:
                photos_uuid = person_info.get("uuid")
                label = person_info.get("name") or ""
                label_norm = normalize_identity_value(label, "alias") if label else ""

                record = {
                    "photos_uuid": photos_uuid,
                    "label": label,
                    "action": None,
                    "principal_id": None,
                    "principal_name": None,
                    "candidates": [],
                    "error": None,
                }

                try:
                    # Skip unnamed or placeholder labels
                    if not label or label.lower().strip() in {"unknown", "no name"}:
                        record["action"] = "skip_empty_label"
                        stats["items"].append(record)
                        continue

                    # 0) If we don't have a photos_uuid, skip linking but count as scanned
                    if not photos_uuid:
                        record["action"] = "skip_no_uuid"
                        stats["items"].append(record)
                        continue

                    # Normalize UUID for comparisons
                    norm_uuid = normalize_identity_value(photos_uuid, "person_uuid")

                    # Check if this photos_uuid is already linked to some Principal
                    existing_claim = (
                        session.query(IdentityClaim)
                        .filter(
                            IdentityClaim.platform == "photos",
                            IdentityClaim.kind == "person_uuid",
                            IdentityClaim.normalized == norm_uuid,
                        )
                        .first()
                    )

                    # Resolution path: exact display_name
                    principal = (
                        session.query(Principal)
                        .filter(Principal.display_name.ilike(label))
                        .first()
                    )

                    # Exact alias/display_name identity match
                    if not principal and label_norm:
                        claim_match = (
                            session.query(IdentityClaim)
                            .filter(
                                IdentityClaim.kind.in_(["display_name", "alias"]),
                                IdentityClaim.normalized == label_norm,
                            )
                            .first()
                        )
                        if claim_match:
                            principal = session.query(Principal).get(claim_match.principal_id)

                    # Fuzzy by name using our unified resolver
                    if not principal:
                        principal = resolve_person_selector(session, {"name": label})

                    # If still not found, try a fuzzy sweep returning multiple
                    candidates = []
                    if not principal:
                        from .queries import search_people_by_identity
                        candidates = search_people_by_identity(
                            session=session,
                            name=label,
                            fuzzy_match=True,
                            limit=max_candidates,
                        )
                        if len(candidates) == 1:
                            cid = candidates[0]["id"]
                            principal = session.query(Principal).get(cid)

                    if not principal:
                        record["action"] = "no_match"
                        record["candidates"] = [c.get("display_name") for c in candidates][:max_candidates]
                        stats["unmatched"] += 1
                        stats["items"].append(record)
                        continue

                    record["principal_id"] = principal.id
                    record["principal_name"] = principal.display_name

                    # Already linked to the same principal?
                    if existing_claim and existing_claim.principal_id == principal.id:
                        record["action"] = "already_linked"
                        stats["already_linked"] += 1
                        stats["items"].append(record)
                        continue

                    # Conflict: linked to different principal
                    if existing_claim and existing_claim.principal_id != principal.id:
                        if not overwrite_conflicts or dry_run:
                            record["action"] = "conflict"
                            stats["conflicts"] += 1
                            stats["items"].append(record)
                            continue
                        else:
                            # Reassign the claim to the matched principal
                            existing_claim.principal_id = principal.id
                            record["action"] = "reassigned_conflict"
                            if not dry_run:
                                session.commit()
                            stats["linked"] += 1
                            stats["items"].append(record)
                            continue

                    # Not linked yet → create new link
                    record["action"] = "link_created" if not dry_run else "would_link"
                    if not dry_run:
                        from .write_tools import add_contact_identity
                        res = add_contact_identity(
                            session,
                            person_id=principal.id,
                            kind="person_uuid",
                            value=photos_uuid,
                            platform="photos",
                            confidence=0.9,
                        )
                        if not res.get("success"):
                            # Could be duplicate race or validation issue
                            record["action"] = "link_failed"
                            record["error"] = res.get("error")
                        else:
                            # Add Photos label as alias for context (best-effort)
                            try:
                                if label:
                                    _ = add_contact_identity(
                                        session,
                                        person_id=principal.id,
                                        kind="alias",
                                        value=label,
                                        platform="photos",
                                        confidence=0.6,
                                    )
                            except Exception:
                                pass
                    if record["action"] == "link_created" or record["action"] == "would_link":
                        stats["linked"] += 1

                    stats["items"].append(record)

                except Exception as ex:
                    record["action"] = "error"
                    record["error"] = str(ex)
                    stats["errors"] += 1
                    stats["items"].append(record)

        return {"success": True, "summary": stats}
    except Exception as e:
        logger.warning("photos_ingest_people_links failed", error=str(e))
        return {"success": False, "error": str(e)}
