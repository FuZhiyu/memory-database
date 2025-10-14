from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List

import pytest

from src.ingestion.base import IngestionPipeline


class StubIdentityClaim:
    """Lightweight stand-in for the SQLAlchemy model used in unit tests."""

    def __init__(self, **kwargs):
        now = datetime.now(timezone.utc)
        kwargs.setdefault("confidence", 1.0)
        kwargs.setdefault("first_seen", now)
        kwargs.setdefault("last_seen", now)
        kwargs.setdefault("extra", {})
        self.__dict__.update(kwargs)


class FakeFilter:
    """Minimal filter object to emulate SQLAlchemy's ``first`` call."""

    def __init__(self, claims: List[StubIdentityClaim], criteria: dict):
        self._claims = claims
        self._criteria = criteria

    def first(self):
        for claim in self._claims:
            if all(getattr(claim, key) == value for key, value in self._criteria.items()):
                return claim
        return None


class FakeQuery:
    def __init__(self, session, model):
        self._session = session
        self._model = model

    def filter_by(self, **criteria):
        if self._model is StubIdentityClaim:
            return FakeFilter(self._session.identity_claims, criteria)
        raise AssertionError("Unexpected model queried")


class FakeSession:
    """Simple in-memory stand-in for SQLAlchemy sessions used in tests."""

    def __init__(self):
        self.identity_claims: List[StubIdentityClaim] = []
        self.added_objects: List[object] = []
        self.flush_count = 0

    def query(self, model):
        return FakeQuery(self, model)

    def add(self, obj):
        self.added_objects.append(obj)
        if hasattr(obj, "principal_id") and obj not in self.identity_claims:
            self.identity_claims.append(obj)

    def flush(self):
        self.flush_count += 1


@pytest.fixture(autouse=True)
def stub_identity_claim(monkeypatch):
    from src.ingestion import base

    monkeypatch.setattr(base, "IdentityClaim", StubIdentityClaim)
    yield


@pytest.fixture
def pipeline():
    return IngestionPipeline(db_manager=None)


def test_process_identity_claim_updates_existing_claim(monkeypatch, pipeline):
    session = FakeSession()

    existing_claim = StubIdentityClaim(
        principal_id="principal-1",
        platform="email",
        kind="email",
        value="alice@example.com",
        normalized="alice@example.com",
        confidence=0.4,
        extra={"source": "contacts"},
    )
    existing_claim.last_seen = datetime(2024, 1, 1, tzinfo=timezone.utc)
    session.identity_claims.append(existing_claim)

    def fake_link(session_arg, identities, display_name=None, platforms=None, extra=None):
        assert identities[0]["normalized"] == "alice@example.com"
        return SimpleNamespace(id="principal-1"), False

    monkeypatch.setattr("src.ingestion.base.link_or_create_principal", fake_link)

    identity_data = {
        "platform": "email",
        "kind": "email",
        "value": "alice@example.com",
        "confidence": 0.9,
        "extra": {"ingested_from": "mbox"},
    }

    updated_claim = pipeline._process_identity_claim(session, identity_data)

    assert updated_claim is existing_claim
    assert pytest.approx(updated_claim.confidence) == 0.9
    assert updated_claim.extra["ingested_from"] == "mbox"
    assert updated_claim.last_seen > datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert session.added_objects == []


def test_process_identity_claim_creates_new_claim(monkeypatch, pipeline):
    session = FakeSession()

    def fake_link(session_arg, identities, display_name=None, platforms=None, extra=None):
        return SimpleNamespace(id="principal-42"), True

    monkeypatch.setattr("src.ingestion.base.link_or_create_principal", fake_link)

    identity_data = {
        "platform": "email",
        "kind": "email",
        "value": "new@example.com",
        "confidence": 0.6,
    }

    claim = pipeline._process_identity_claim(session, identity_data)

    assert hasattr(claim, "principal_id")
    assert claim.principal_id == "principal-42"
    assert claim.platform == "email"
    assert claim.normalized == "new@example.com"
    assert session.identity_claims == [claim]
    assert session.flush_count == 1


def test_process_identity_claim_allows_shared_identity(monkeypatch, pipeline):
    session = FakeSession()

    principals = [
        SimpleNamespace(id="principal-alice"),
        SimpleNamespace(id="principal-bob"),
    ]

    def fake_link(session_arg, identities, display_name=None, platforms=None, extra=None):
        if not principals:
            raise AssertionError("No more principals in queue")
        return principals.pop(0), True

    monkeypatch.setattr("src.ingestion.base.link_or_create_principal", fake_link)

    identity_data = {
        "platform": "email",
        "kind": "email",
        "value": "team@company.com",
        "confidence": 0.8,
    }

    first_claim = pipeline._process_identity_claim(session, identity_data)
    second_claim = pipeline._process_identity_claim(session, identity_data)

    assert first_claim.principal_id == "principal-alice"
    assert second_claim.principal_id == "principal-bob"
    assert first_claim.normalized == second_claim.normalized == "team@company.com"
    assert len(session.identity_claims) == 2
