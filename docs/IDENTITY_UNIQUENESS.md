# Identity Claim Uniqueness Constraint

## Overview

The Memory Database system enforces a **database-level uniqueness constraint** on identity claims to ensure data integrity while preserving provenance.

## The Constraint

```sql
UNIQUE (principal_id, platform, normalized)
```

**Constraint Name:** `uq_identity_per_platform`

## What This Means

Each person (Principal) can have **exactly one identity claim** per combination of:
- `principal_id` - The person this identity belongs to
- `platform` - Where this identity was observed (e.g., contacts, imessage, email)
- `normalized` - The cleaned/normalized value of the identity

## Examples

### ✅ ALLOWED

```python
# Same email from DIFFERENT platforms (preserves provenance)
Person A: {platform='contacts', kind='email', normalized='john@example.com'}
Person A: {platform='imessage', kind='email', normalized='john@example.com'}

# Different emails from SAME platform
Person A: {platform='contacts', kind='email', normalized='john@work.com'}
Person A: {platform='contacts', kind='email', normalized='john@personal.com'}

# Different kinds from same platform with different normalized values
Person A: {platform='contacts', kind='phone', normalized='+12345678901'}
Person A: {platform='contacts', kind='email', normalized='john@example.com'}
```

### ❌ BLOCKED (Database Error)

```python
# Same email TWICE from same platform
Person A: {platform='contacts', kind='email', normalized='john@example.com'}
Person A: {platform='contacts', kind='email', normalized='john@example.com'}  # ERROR!

# Same normalized value from same platform (even if kind differs - edge case)
Person A: {platform='contacts', kind='display_name', normalized='john@example.com'}
Person A: {platform='contacts', kind='email', normalized='john@example.com'}  # ERROR!
```

## Why Platform-Level Granularity?

This design choice provides several benefits:

1. **Provenance Tracking**: Preserves where each identity was observed
   - "Saw john@ex.com in Contacts on 2020-01-01"
   - "Saw john@ex.com in iMessage on 2023-05-15"
   - Different confidence scores, timestamps, metadata per source

2. **Richer Identity Resolution**: Multiple observations strengthen confidence
   - "Email verified across 3 platforms" vs "Email seen once"
   - Can weight by platform reliability

3. **Clear Data Lifecycle**: Each platform owns its claims
   - Deleting from Contacts doesn't affect iMessage claims
   - Re-syncing one platform doesn't touch others

4. **Temporal Analysis**: Track identity evolution per source
   - "When did this email first appear in my contacts?"
   - "When did we last exchange messages on iMessage?"

## Implementation Guidelines

### For All Code That Creates IdentityClaim Records

**REQUIRED**: Check for duplicates before insertion

```python
from src.models import IdentityClaim

# Before creating a new claim
existing = session.query(IdentityClaim).filter_by(
    principal_id=person_id,
    platform=platform,
    normalized=normalized_value
).first()

if existing:
    # Update last_seen or handle as needed
    existing.last_seen = datetime.now(timezone.utc)
else:
    # Create new claim
    claim = IdentityClaim(
        principal_id=person_id,
        platform=platform,
        kind=kind,
        value=value,
        normalized=normalized_value,
        ...
    )
    session.add(claim)
```

### For Ingestion Code

```python
# Good: Platform-specific ingestion
def ingest_contacts():
    platform = 'contacts'  # Platform is fixed for this ingestion source

    for contact in contacts_data:
        for identity in extract_identities(contact):
            # Check: (principal_id, 'contacts', normalized)
            existing = session.query(IdentityClaim).filter_by(
                principal_id=person.id,
                platform=platform,
                normalized=identity['normalized']
            ).first()

            if existing:
                existing.last_seen = datetime.now(timezone.utc)
            else:
                # Create new claim with platform='contacts'
                session.add(IdentityClaim(...))
```

### For MCP Write Operations

The `src/mcp_server/write_tools.py` module already implements proper duplicate checking. Use these functions:

```python
from memory_database.mcp_server.write_tools import add_contact_identity

# This handles duplicate checking automatically
result = add_contact_identity(
    session=session,
    person_id=person_id,
    kind='email',
    value='john@example.com',
    platform='manual',  # Specify the platform
    confidence=0.9
)

if not result['success']:
    # Handle duplicate error
    print(result['error'])
```

### For Updates

When updating claims, ensure the final state doesn't violate the constraint:

```python
# BAD: Could violate constraint if another claim exists
claim.platform = new_platform
claim.normalized = new_normalized
session.commit()  # Might fail!

# GOOD: Check for conflicts first
if claim.platform != new_platform or claim.normalized != new_normalized:
    existing = session.query(IdentityClaim).filter_by(
        principal_id=claim.principal_id,
        platform=new_platform,
        normalized=new_normalized
    ).filter(IdentityClaim.id != claim.id).first()

    if existing:
        raise ValueError(f"Update would create duplicate on {new_platform}")

    claim.platform = new_platform
    claim.normalized = new_normalized
    session.commit()
```

## Handling IntegrityError

If you hit a constraint violation:

```python
from sqlalchemy.exc import IntegrityError

try:
    session.add(claim)
    session.commit()
except IntegrityError as e:
    session.rollback()
    if 'uq_identity_per_platform' in str(e):
        # Handle duplicate claim
        logger.warning("Duplicate identity claim",
                      person=person_id,
                      platform=platform,
                      normalized=normalized)
    else:
        raise
```

## Migration History

- **001_add_identity_uniqueness_constraint.py**: Initial constraint added
  - Consolidated any existing duplicates
  - Added database constraint `uq_identity_per_platform`

## Schema Definition

See `src/models/people.py`:

```python
class IdentityClaim(Base):
    __tablename__ = "identity_claim"

    id = Column(String, primary_key=True)
    principal_id = Column(String, ForeignKey("principal.id"))
    platform = Column(Text, nullable=False)
    kind = Column(Text, nullable=False)
    value = Column(Text, nullable=False)
    normalized = Column(Text)

    __table_args__ = (
        UniqueConstraint('principal_id', 'platform', 'normalized',
                        name='uq_identity_per_platform'),
    )
```

## FAQs

**Q: Can the same email appear twice for the same person?**
A: Yes, if from different platforms. No, if from the same platform.

**Q: What if I need to change the platform of a claim?**
A: Check for duplicates with the target platform first, or use the `update_contact_identity()` function which handles this.

**Q: How do I deduplicate across platforms for display?**
A: In queries, GROUP BY (kind, normalized) to show unique values:
```sql
SELECT DISTINCT ON (kind, normalized) *
FROM identity_claim
WHERE principal_id = ?
```

**Q: Can I remove this constraint?**
A: No. Removing it would require a migration, updates to all write code, and changes to ingestion logic. The constraint is fundamental to the system design.

## Related Files

- Model definition: `src/models/people.py`
- Write operations: `src/mcp_server/write_tools.py`
- Ingestion: `src/ingestion/base.py`
- Migration: `migrations/001_add_identity_uniqueness_constraint.py`
- Architecture docs: `CLAUDE.md`
