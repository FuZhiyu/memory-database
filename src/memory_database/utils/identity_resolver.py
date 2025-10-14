"""
Identity resolution utilities for finding and linking principals across platforms.

This module provides functions for resolving identities to existing principals
in the database, enabling deduplication and cross-platform identity linking.
"""

from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
import structlog

from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from memory_database.models import Principal, IdentityClaim
from memory_database.utils.normalization import normalize_identity_value

logger = structlog.get_logger()


def find_existing_principal(
    session: Session,
    identities: List[Dict[str, Any]],
    platforms: Optional[List[str]] = None
) -> Optional[Principal]:
    """
    Find an existing principal by exact match on normalized identities.
    
    Args:
        session: Database session
        identities: List of identity dicts with 'kind', 'value', 'normalized' keys
        platforms: Optional list of platforms to search within
        
    Returns:
        Existing Principal if found, None otherwise
    """
    if not identities:
        return None
    
    # Build query conditions for each identity
    conditions = []
    
    for identity in identities:
        normalized = identity.get('normalized') or normalize_identity_value(
            identity.get('value', ''),
            identity.get('kind', '')
        )
        
        if not normalized:
            continue
        
        # Build condition for this identity
        identity_condition = and_(
            IdentityClaim.kind == identity.get('kind'),
            IdentityClaim.normalized == normalized
        )
        
        # Add platform filter if specified
        if platforms:
            identity_condition = and_(
                identity_condition,
                IdentityClaim.platform.in_(platforms)
            )
        
        conditions.append(identity_condition)
    
    if not conditions:
        return None
    
    # Query for any matching identity claims
    query = session.query(IdentityClaim).filter(or_(*conditions))
    
    # Get all matching claims
    matching_claims = query.all()
    
    if not matching_claims:
        return None
    
    # Count matches per principal
    principal_matches = {}
    for claim in matching_claims:
        principal_id = claim.principal_id
        if principal_id not in principal_matches:
            principal_matches[principal_id] = {
                'count': 0,
                'confidence': 0.0,
                'claims': []
            }
        principal_matches[principal_id]['count'] += 1
        principal_matches[principal_id]['confidence'] += claim.confidence
        principal_matches[principal_id]['claims'].append(claim)
    
    # Find the principal with the most matches
    best_match = None
    best_score = 0
    
    for principal_id, match_info in principal_matches.items():
        # Score based on number of matches and confidence
        score = match_info['count'] * 1000 + match_info['confidence']
        
        if score > best_score:
            best_score = score
            best_match = principal_id
    
    if best_match:
        principal = session.query(Principal).get(best_match)
        
        logger.debug(
            "Found existing principal",
            principal_id=best_match,
            match_count=principal_matches[best_match]['count'],
            confidence=principal_matches[best_match]['confidence']
        )
        
        return principal
    
    return None


def link_or_create_principal(
    session: Session,
    identities: List[Dict[str, Any]],
    display_name: Optional[str] = None,
    platforms: Optional[List[str]] = None,
    extra: Optional[Dict] = None
) -> Tuple[Principal, bool]:
    """
    Either link to an existing principal or create a new one.
    
    Args:
        session: Database session
        identities: List of identity dicts to process
        display_name: Display name for new principal if created
        platforms: Platforms to search for existing principals
        extra: Extra data to store with new principal
        
    Returns:
        Tuple of (Principal, is_new) where is_new indicates if principal was created
    """
    # Try to find existing principal
    existing = find_existing_principal(session, identities, platforms)
    
    if existing:
        # Update existing principal's last seen info
        for claim in existing.identity_claims:
            claim.last_seen = datetime.now(timezone.utc)
        
        # Update display name if better one provided
        if display_name and (not existing.display_name or existing.display_name == 'Unknown'):
            existing.display_name = display_name
        
        return existing, False
    
    # Create new principal
    principal = Principal(
        display_name=display_name or 'Unknown',
        extra=extra or {}
    )
    session.add(principal)
    session.flush()  # Get the ID
    
    # Create identity claims for the new principal
    # Track (platform, normalized) combinations to avoid duplicates within the input list
    seen_combinations = set()

    for identity in identities:
        if not identity.get('value'):
            continue

        normalized = identity.get('normalized') or normalize_identity_value(
            identity['value'],
            identity.get('kind', '')
        )
        platform = identity.get('platform', 'unknown')

        # Check for duplicate within the identities list itself
        combination_key = (platform, normalized)
        if combination_key in seen_combinations:
            logger.warning(
                "Duplicate identity in input list, skipping",
                platform=platform,
                normalized=normalized
            )
            continue

        seen_combinations.add(combination_key)

        # Check if claim already exists in database (defensive)
        # This shouldn't happen since we're creating a new principal, but check anyway
        existing_claim = session.query(IdentityClaim).filter_by(
            principal_id=principal.id,
            platform=platform,
            normalized=normalized
        ).first()

        if existing_claim:
            # Update existing claim instead of creating duplicate
            existing_claim.value = identity['value']
            existing_claim.kind = identity.get('kind', 'unknown')
            existing_claim.confidence = identity.get('confidence', 1.0)
            existing_claim.last_seen = datetime.now(timezone.utc)
            existing_claim.extra = identity.get('extra', {})
        else:
            # Create new identity claim
            claim = IdentityClaim(
                principal_id=principal.id,
                platform=platform,
                kind=identity.get('kind', 'unknown'),
                value=identity['value'],
                normalized=normalized,
                confidence=identity.get('confidence', 1.0),
                first_seen=datetime.now(timezone.utc),
                last_seen=datetime.now(timezone.utc),
                extra=identity.get('extra', {})
            )
            session.add(claim)
    
    logger.info(
        "Created new principal",
        principal_id=principal.id,
        display_name=display_name,
        identity_count=len(identities)
    )
    
    return principal, True


def resolve_person_selector(
    session: Session,
    person: Optional[Dict[str, Any]]
) -> Optional[Principal]:
    """
    Resolve a flexible person selector into a Principal.

    The selector can include any of:
    - id: Principal ID (ULID)
    - email, phone, name (display_name), username, contact_id, memory_url

    Strategy:
    1) If id provided, fetch directly
    2) Otherwise try exact identity claims by kind in priority order
       (email, phone, username, contact_id, memory_url)
    3) Fallback to name-based lookup (display_name ilike, then identity claims)

    Returns Principal or None if not found.
    """
    if not person:
        return None

    # 1) Direct ID
    pid = person.get("id") if isinstance(person, dict) else None
    if pid:
        existing = session.query(Principal).get(pid)
        if existing:
            return existing

    # Helper to find by a single identity kind/value
    def _find_by_identity(kind: str, value: Optional[str]) -> Optional[Principal]:
        if not value:
            return None
        normalized = normalize_identity_value(value, kind)
        if not normalized:
            return None
        claim = (
            session.query(IdentityClaim)
            .filter(
                IdentityClaim.kind == kind,
                IdentityClaim.normalized == normalized,
            )
            .first()
        )
        if claim:
            return session.query(Principal).get(claim.principal_id)
        return None

    # 2) Try common identity kinds in order of reliability
    by_email = _find_by_identity("email", person.get("email"))
    if by_email:
        return by_email

    by_phone = _find_by_identity("phone", person.get("phone"))
    if by_phone:
        return by_phone

    by_username = _find_by_identity("username", person.get("username"))
    if by_username:
        return by_username

    by_contact_id = _find_by_identity("contact_id", person.get("contact_id"))
    if by_contact_id:
        return by_contact_id

    by_memory_url = _find_by_identity("memory_url", person.get("memory_url"))
    if by_memory_url:
        return by_memory_url

    # 3) Fallback to name matching
    name = person.get("name")
    if name:
        normalized_name = normalize_identity_value(name, "display_name")
        if normalized_name:
            # Try Principal.display_name first
            p = (
                session.query(Principal)
                .filter(Principal.display_name.ilike(f"%{name}%"))
                .first()
            )
            if p:
                return p

            # Try display_name identity claims with ilike
            claim = (
                session.query(IdentityClaim)
                .filter(
                    IdentityClaim.kind == "display_name",
                    IdentityClaim.normalized.ilike(f"%{normalized_name}%"),
                )
                .first()
            )
            if claim:
                return session.query(Principal).get(claim.principal_id)

    return None


def merge_principals(
    session: Session,
    source_principal_id: str,
    target_principal_id: str,
    actor: str = 'system',
    reason: Optional[str] = None
) -> Principal:
    """
    Merge one principal into another, moving all identity claims and relationships.
    
    Args:
        session: Database session
        source_principal_id: Principal to merge from (will be marked as merged)
        target_principal_id: Principal to merge into
        actor: Who is performing the merge
        reason: Optional reason for the merge
        
    Returns:
        The target principal after merge
    """
    from memory_database.models import PersonMessage, PersonMedia, PersonDocument, PersonEvent, ResolutionEvent
    
    source = session.query(Principal).get(source_principal_id)
    target = session.query(Principal).get(target_principal_id)
    
    if not source or not target:
        raise ValueError("Both source and target principals must exist")
    
    if source.id == target.id:
        raise ValueError("Cannot merge a principal with itself")
    
    logger.info(
        "Merging principals",
        source_id=source_principal_id,
        target_id=target_principal_id,
        actor=actor
    )
    
    # Move all identity claims
    for claim in source.identity_claims:
        claim.principal_id = target.id
    
    # Move all message links
    for link in source.message_links:
        # Check if target already has this link
        existing = session.query(PersonMessage).filter_by(
            principal_id=target.id,
            message_id=link.message_id,
            role=link.role
        ).first()
        
        if not existing:
            link.principal_id = target.id
        else:
            # Delete duplicate
            session.delete(link)
    
    # Move all media links
    for link in source.media_links:
        link.principal_id = target.id
    
    # Move all document links
    for link in source.document_links:
        link.principal_id = target.id
    
    # Move all events
    for event in source.events:
        event.principal_id = target.id
    
    # Update merged_from list on target
    if not target.merged_from:
        target.merged_from = []
    target.merged_from.append(source.id)
    
    # Also include any principals that were previously merged into source
    if source.merged_from:
        target.merged_from.extend(source.merged_from)
    
    # Create resolution event for audit
    resolution_event = ResolutionEvent(
        actor=actor,
        action='merge',
        from_principal=source.id,
        to_principal=target.id,
        reason=reason,
        score_snapshot={
            'source_claims': len(source.identity_claims),
            'target_claims': len(target.identity_claims)
        }
    )
    session.add(resolution_event)
    
    # Mark source as merged (don't delete to preserve history)
    source.extra['merged_into'] = target.id
    source.extra['merged_at'] = datetime.now(timezone.utc).isoformat()
    
    session.flush()
    
    logger.info(
        "Principal merge completed",
        source_id=source_principal_id,
        target_id=target_principal_id,
        claims_moved=len(source.identity_claims)
    )
    
    return target


def find_principals_by_identity(
    session: Session,
    kind: str,
    value: str,
    platform: Optional[str] = None
) -> List[Principal]:
    """
    Find all principals that have a specific identity claim.
    
    Args:
        session: Database session
        kind: Identity kind (email, phone, etc.)
        value: Raw identity value
        platform: Optional platform filter
        
    Returns:
        List of matching principals
    """
    normalized = normalize_identity_value(value, kind)
    
    query = session.query(IdentityClaim).filter(
        IdentityClaim.kind == kind,
        IdentityClaim.normalized == normalized
    )
    
    if platform:
        query = query.filter(IdentityClaim.platform == platform)
    
    claims = query.all()
    
    # Get unique principals
    principal_ids = set(claim.principal_id for claim in claims)
    principals = [
        session.query(Principal).get(pid)
        for pid in principal_ids
    ]
    
    return [p for p in principals if p is not None]
