from types import SimpleNamespace
from typing import List

import pytest

from src.ingestion.imessage import iMessageIngestionSource


class _DummyAttachmentManager:
    def __init__(self, *args, **kwargs):
        pass


@pytest.fixture
def imessage_source(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DB_ATTACHMENTS_ROOT", str(tmp_path))
    monkeypatch.setattr("src.ingestion.imessage.AttachmentManager", _DummyAttachmentManager)
    source = iMessageIngestionSource(db_manager=None)
    source.handle_cache = {}
    return source


def test_resolve_attachment_path_rejects_outside_root(imessage_source, tmp_path):
    guid = "ab99-1234"
    dir1, dir2 = "ab", "99"
    attachment_dir = tmp_path / dir1 / dir2 / guid
    attachment_dir.mkdir(parents=True)
    stored_file = attachment_dir / "photo.jpg"
    stored_file.write_text("data")

    resolved = imessage_source.resolve_attachment_path(guid, "photo.jpg")
    assert resolved == stored_file.resolve()

    outside = tmp_path.parent / "evil.jpg"
    outside.write_text("bad")

    rejected = imessage_source.resolve_attachment_path(guid, str(outside))
    assert rejected is None


def _make_message(rowid: int, guid: str, date: float) -> SimpleNamespace:
    return SimpleNamespace(
        rowid=rowid,
        guid=guid,
        text="hello",
        service="imessage",
        handle_id=None,
        subject=None,
        date=date,
        date_read=None,
        date_delivered=None,
        is_from_me=True,
        is_read=True,
        is_sent=True,
        is_delivered=True,
        cache_roomnames=None,
        group_title=None,
        associated_message_guid=None,
        associated_message_type=None,
        thread_originator_guid=None,
    )


class _StreamingDB:
    def __init__(self, messages: List[SimpleNamespace]):
        self.messages = messages
        self.calls: List[tuple[float, int]] = []

    def query_messages_after(self, timestamp: float, limit: int):
        self.calls.append((timestamp, limit))
        filtered = [msg for msg in self.messages if msg.date is not None and msg.date > timestamp]
        return filtered[:limit]

    def get_message_participants(self, _rowid: int):
        return []

    def get_message_attachments(self, _rowid: int):
        return []


def test_extract_raw_data_streams_in_batches(imessage_source):
    messages = [
        _make_message(1, "g1", 1.0),
        _make_message(2, "g2", 2.0),
        _make_message(3, "g3", 3.0),
    ]
    fake_db = _StreamingDB(messages)

    imessage_source.batch_size = 2
    imessage_source.imessage_db = fake_db

    results = list(imessage_source.extract_raw_data("custom"))

    assert len(results) == 3
    assert fake_db.calls == [(0.0, 2), (2.0, 2)]
