# Memory Database Documentation

## Critical Issues

### 🚨 Identity Constraint Architectural Issue
**Status:** CRITICAL - DO NOT USE MESSAGE INGESTION
**File:** [IDENTITY_CONSTRAINT_ISSUE.md](./IDENTITY_CONSTRAINT_ISSUE.md)

The base ingestion pipeline has a fundamental flaw that causes incorrect message attribution when identities are shared across multiple people (team emails, shared phones, etc.).

**Impact:** Messages from shared identities get attributed to the wrong person.

**Affected Components:**
- ❌ Email ingestion
- ❌ iMessage ingestion
- ❌ Any message ingestion using `base.py`

**Safe Components:**
- ✅ Contacts import
- ✅ MCP operations
- ✅ Manual identity management

**Read the full issue:** [IDENTITY_CONSTRAINT_ISSUE.md](./IDENTITY_CONSTRAINT_ISSUE.md)

---

## Architecture Documentation

### Identity Uniqueness Constraint
**File:** [IDENTITY_UNIQUENESS.md](./IDENTITY_UNIQUENESS.md)

Complete reference for the database uniqueness constraint on identity claims:
- Constraint definition and rationale
- Implementation guidelines
- Code examples
- FAQs

**Key Point:** `UNIQUE (principal_id, platform, normalized)` allows different people to share the same identity (e.g., team@company.com) by design.

---

## Documentation Index

| Document | Purpose | Audience |
|----------|---------|----------|
| [IDENTITY_UNIQUENESS.md](./IDENTITY_UNIQUENESS.md) | How the identity constraint works | All developers |
| [IDENTITY_CONSTRAINT_ISSUE.md](./IDENTITY_CONSTRAINT_ISSUE.md) | Critical bug in base ingestion | **READ FIRST** before using message ingestion |

---

## Quick Links

- Main project README: `../CLAUDE.md`
- Database models: `../src/models/people.py`
- Identity resolver (correct approach): `../src/utils/identity_resolver.py`
- Base ingestion (HAS BUG): `../src/ingestion/base.py`
- Migration scripts: `../migrations/`
