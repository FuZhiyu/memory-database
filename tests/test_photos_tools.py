import importlib
import os
import sys
from types import ModuleType

import pytest


class DummyMCP:
    def tool(self, func=None, /, **_kwargs):
        def decorator(f):
            return f

        return decorator if func is None else decorator(func)

    def resource(self, *_args, **_kwargs):
        def decorator(f):
            return f

        return decorator


def ensure_dummy_server_module(monkeypatch):
    """Inject a dummy memory_database.mcp_server.server module exposing `mcp` to avoid importing real server deps."""
    module_name = "memory_database.mcp_server.server"
    if module_name in sys.modules:
        return
    dummy = ModuleType(module_name)
    dummy.mcp = DummyMCP()
    # Provide a placeholder db_manager to satisfy imports; tests will patch as needed
    class _DB:
        def get_session(self):
            raise RuntimeError("db_manager.get_session should be patched in tests")
    dummy.db_manager = _DB()
    monkeypatch.setitem(sys.modules, module_name, dummy)


def import_photos_tools(monkeypatch):
    # Ensure environment variables required by DatabaseSettings are present if server accidentally loads
    monkeypatch.setenv("POSTGRES_HOST", os.getenv("POSTGRES_HOST", "localhost"))
    monkeypatch.setenv("POSTGRES_DB", os.getenv("POSTGRES_DB", "testdb"))
    monkeypatch.setenv("POSTGRES_USER", os.getenv("POSTGRES_USER", "postgres"))

    # Make sure we don't import the real server with DB/FastMCP
    ensure_dummy_server_module(monkeypatch)

    mod = importlib.import_module("memory_database.mcp_server.photos_tools")
    return mod


class FakePlace:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name


class FakeFaceInfo:
    def __init__(self, person=None, confidence=None, bbox=None, center=None, width=None, height=None, person_uuid=None):
        self.person = person
        self.confidence = confidence
        self.bbox = bbox
        self.center = center
        self.width = width
        self.height = height
        self._person_info_uuid = person_uuid

    @property
    def person_info(self):
        class P:
            pass

        p = P()
        setattr(p, "uuid", self._person_info_uuid)
        return p


class FakePhoto:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        # Set default attributes if not provided
        if not hasattr(self, 'path_derivatives'):
            self.path_derivatives = None
        if not hasattr(self, 'path_edited'):
            self.path_edited = None
        if not hasattr(self, 'path'):
            self.path = None


class FakePhotosDB:
    def __init__(self, persons=None, person_info=None, photos_list=None):
        self._persons = persons or []
        self._person_info = person_info or []
        self._photos = photos_list or []

    @property
    def persons(self):
        return list(self._persons)

    @property
    def person_info(self):
        return list(self._person_info)

    def photos(self, **_kwargs):
        return list(self._photos)


def test_photos_search_identity_resolution(monkeypatch):
    mod = import_photos_tools(monkeypatch)

    # Mock Photos side
    persons = ["Alice", "Bob"]
    photo = FakePhoto(
        uuid="u-100",
        date=__import__("datetime").datetime(2023, 5, 1),
        date_modified=__import__("datetime").datetime(2023, 5, 2),
        persons=["Alice"],
        labels=["cat"],
        location=None,
        place=FakePlace("Seattle, Washington"),
        path="/originals/u-100.jpg",
        path_edited=None,
    )
    fake_photos_db = FakePhotosDB(persons=persons, photos_list=[photo])
    monkeypatch.setattr(mod, "PHOTOS_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mod, "PhotosDB", lambda: fake_photos_db, raising=False)

    # Stub DB identity resolution and name fetching
    class FakeClaim:
        def __init__(self, kind, value):
            self.kind = kind
            self.value = value

    class FakePerson:
        def __init__(self, id, display_name, claims):
            self.id = id
            self.display_name = display_name
            self.identity_claims = claims

    class FakeSession:
        def __init__(self, person):
            self._person = person

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def query(self, model):
            class Q:
                def __init__(self, person):
                    self._person = person

                def get(self, _id):
                    return self._person

                def filter(self, *_args, **_kwargs):
                    return self

                def all(self):
                    return self._person.identity_claims

            return Q(self._person)

    class FakeDBManager:
        def __init__(self, person):
            self._person = person

        def get_session(self):
            return FakeSession(self._person)

    alice = FakePerson("P1", "Alice Johnson", [FakeClaim("alias", "Alice"), FakeClaim("email", "alice@example.com")])

    # Patch resolver and db_manager in module namespace
    monkeypatch.setattr(mod, "find_person_by_any_identity", lambda session, **kwargs: "P1", raising=False)
    monkeypatch.setattr(mod, "db_manager", FakeDBManager(alice), raising=False)

    # Now call with identity params instead of explicit people list
    res = mod.photos_search(person_email="alice@example.com", limit=5)
    assert res.get("error") is None
    assert res["total_found"] == 1
    assert res["photos"][0]["uuid"] == "u-100"


def test_photos_search_people_labels_place_faces(monkeypatch):
    mod = import_photos_tools(monkeypatch)

    # DB knows persons and returns a matching photo
    persons = ["Ann", "Annabelle", "Anna"]
    face = FakeFaceInfo(person="Annabelle", confidence=0.9, bbox=(0.1, 0.1, 0.3, 0.3), person_uuid="p-uuid-1")
    photo = FakePhoto(
        uuid="u-1",
        date=__import__("datetime").datetime(2022, 1, 1),
        date_modified=__import__("datetime").datetime(2022, 1, 2),
        hasadjustments=True,
        uti="public.jpeg",
        favorite=True,
        hidden=False,
        albums=["Holidays"],
        keywords=["sunny", "beach"],
        persons=["Annabelle"],
        labels=["cat", "tree"],
        location=(37.7749, -122.4194),
        place=FakePlace("San Francisco, California"),
        path="/originals/u-1.jpg",
        path_edited="/edits/u-1.jpg",
        face_info=[face],
    )
    fake_db = FakePhotosDB(persons=persons, photos_list=[photo])

    monkeypatch.setattr(mod, "PHOTOS_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mod, "PhotosDB", lambda: fake_db, raising=False)

    res = mod.photos_search(
        people=["Ann"],
        labels=["cat"],
        place="San",
        include_faces=True,
        limit=10,
    )
    assert "error" not in res
    assert res["total_found"] == 1
    p = res["photos"][0]
    assert p["uuid"] == "u-1"
    assert p["persons"] == ["Annabelle"]
    assert p["labels"] == ["cat", "tree"]
    assert p["place"].lower().startswith("san")
    assert p.get("faces") and p["faces"][0]["person"] == "Annabelle"
    assert p.get("person_uuids") == ["p-uuid-1"]


def test_photos_export_preview(tmp_path, monkeypatch):
    mod = import_photos_tools(monkeypatch)

    # Create temp preview files that FakePhoto will reference
    preview1 = tmp_path / "preview1.jpeg"
    preview1.write_bytes(b"fake image 1")
    preview2 = tmp_path / "preview2.jpeg"
    preview2.write_bytes(b"fake image 2")

    p1 = FakePhoto(uuid="e-1", path_derivatives=[str(preview1)], path=str(preview1))
    p2 = FakePhoto(uuid="e-2", path_derivatives=[str(preview2)], path=str(preview2))
    fake_db = FakePhotosDB(photos_list=[p1, p2])
    monkeypatch.setattr(mod, "PHOTOS_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mod, "PhotosDB", lambda: fake_db, raising=False)

    dest = tmp_path / "exports"
    result = mod.photos_export(uuids=["e-1", "e-2"], destination_dir=str(dest), use_preview=True)
    assert result["destination"] == str(dest)
    assert len(result["exported_files"]) == 2
    for path in result["exported_files"]:
        assert path.startswith(str(dest))
        assert os.path.exists(path)  # Files should actually be copied


def test_view_photos(tmp_path, monkeypatch):
    """Test view_photos returns Image objects with photo data."""
    mod = import_photos_tools(monkeypatch)

    # Create a minimal JPEG file (1x1 red pixel)
    jpeg_bytes = bytes([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
        0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
        0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xC0, 0x00,
        0x0B, 0x08, 0x00, 0x01, 0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4,
        0x00, 0x14, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xC4, 0x00, 0x14,
        0x10, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01,
        0x00, 0x00, 0x3F, 0x00, 0x7F, 0xFF, 0xD9
    ])

    # Create preview files
    preview1 = tmp_path / "preview1.jpeg"
    preview1.write_bytes(jpeg_bytes)
    preview2 = tmp_path / "preview2.jpeg"
    preview2.write_bytes(jpeg_bytes)

    # Create FakePhoto with derivatives
    p1 = FakePhoto(uuid="v-1", path_derivatives=[str(preview1)], path=str(preview1))
    p2 = FakePhoto(uuid="v-2", path_derivatives=[str(preview2)], path=str(preview2))
    fake_db = FakePhotosDB(photos_list=[p1, p2])

    monkeypatch.setattr(mod, "PHOTOS_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mod, "PhotosDB", lambda: fake_db, raising=False)

    # Call view_photos
    result = mod.view_photos(uuids=["v-1", "v-2"], use_preview=True)

    # Verify we got Image objects back
    assert isinstance(result, list)
    assert len(result) == 2

    # Check that each result is an Image object from FastMCP
    # Import Image type to verify instance
    from fastmcp.utilities.types import Image
    for img in result:
        assert isinstance(img, Image)
        assert hasattr(img, 'data')  # Image objects have data attribute
        assert img.data == jpeg_bytes  # Should contain our test JPEG bytes


def test_view_photos_returns_empty_on_error(monkeypatch):
    """Test view_photos returns empty list when photos unavailable."""
    mod = import_photos_tools(monkeypatch)

    monkeypatch.setattr(mod, "PHOTOS_AVAILABLE", False, raising=False)
    monkeypatch.setattr(mod, "PHOTOS_IMPORT_ERROR", "osxphotos not installed", raising=False)

    result = mod.view_photos(uuids=["test-uuid"])
    assert result == []
