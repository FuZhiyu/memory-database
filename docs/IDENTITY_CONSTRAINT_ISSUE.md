# Critical Architectural Issue: Base Ingestion Identity Resolution

**Status:** 🚨 **CRITICAL - DO NOT USE FOR MESSAGE INGESTION**
**Affected Component:** `src/ingestion/base.py:_process_identity_claim()`
**Date Identified:** 2025-10-05
**Severity:** HIGH

---

## Executive Summary

The base ingestion pipeline (`base.py`) uses a **global 1:1 identity mapping strategy** that is incompatible with the database uniqueness constraint `UNIQUE (principal_id, platform, normalized)`. This causes **incorrect message attribution when identities are shared across multiple people**.

**Impact:**
- ❌ Messages from shared identities (team emails, shared phones) attributed to wrong person
- ❌ Cannot properly handle identity resolution for multi-person communications
- ❌ Creates data corruption in message attribution

**Affected Operations:**
- Email ingestion (uses `base.py`)
- iMessage ingestion (uses `base.py`)
- Any message ingestion using `IngestionPipeline`

**Safe Operations:**
- ✅ Contacts import (uses dedicated `contacts.py`)
- ✅ MCP operations (uses `write_tools.py`)
- ✅ Manual identity management

---

## The Problem Explained

### Current Implementation (BROKEN)

File: `src/ingestion/base.py`, function `_process_identity_claim()`

```python
def _process_identity_claim(self, session, identity_data: Dict[str, Any]):
    # BUG: Searches globally without principal_id!
    existing = session.query(IdentityClaim).filter_by(
        platform=identity_data['platform'],
        normalized=identity_data['normalized']
    ).first()  # Finds ANY claim from ANY person

    if existing:
        # Returns the FIRST person found with this identity
        return existing

    # Create new principal for new identity
    principal = Principal(display_name=identity_data['value'])
    # ...
```

**What this does:**
1. Query searches for identity **globally across all people**
2. If found, returns existing claim (from potentially wrong person)
3. Messages get linked to whoever was seen first with that identity

**Assumption:** "Each unique identity belongs to exactly one person globally"

### Database Constraint (ACTUAL DESIGN)

```sql
UNIQUE (principal_id, platform, normalized)
```

**What this allows:**
- ✅ Different people can share the same identity
- ✅ Alice and Bob can both have `team@company.com`
- ✅ Multiple people can use shared phone numbers

**Assumption:** "Identities can be shared; principal_id disambiguates"

### The Architectural Mismatch

```
┌─────────────────────────────────────────────────────────┐
│  Database Constraint Says:                              │
│  "Same identity can belong to MULTIPLE people"          │
│                                                          │
│  UNIQUE (principal_id, platform, normalized)            │
│                                                          │
│  Example (VALID):                                       │
│  - Principal A: {platform='email', norm='team@co.com'}  │
│  - Principal B: {platform='email', norm='team@co.com'}  │
└─────────────────────────────────────────────────────────┘
                              ❌ CONFLICTS WITH
┌─────────────────────────────────────────────────────────┐
│  base.py Code Assumes:                                  │
│  "Each identity belongs to ONE person globally"         │
│                                                          │
│  Query: WHERE platform=? AND normalized=?               │
│  (Missing principal_id!)                                │
│                                                          │
│  Result: Returns FIRST person found                     │
└─────────────────────────────────────────────────────────┘
```

---

## Real-World Example

### Scenario: Shared Team Email

**Setup:**
- Team email: `support@company.com`
- Alice sends a message from it at 9:00 AM
- Bob sends a message from it at 10:00 AM

**What Happens:**

```python
# 9:00 AM - Alice's message arrives
identity_data = {
    'platform': 'email',
    'normalized': 'support@company.com'
}

# Query finds: Nothing
# Creates: Principal A (Alice)
# Creates: Claim {principal_id=A, platform='email', normalized='support@company.com'}
# Result: ✅ Alice's message → Principal A (CORRECT)

# 10:00 AM - Bob's message arrives
identity_data = {
    'platform': 'email',
    'normalized': 'support@company.com'
}

# Query finds: Claim for Principal A (Alice's claim)
# Returns: Principal A
# Result: ❌ Bob's message → Principal A (WRONG! Should be Principal B)
```

**Database State After:**
```
Principals:
  - Principal A (created for Alice)

Identity Claims:
  - {id=1, principal_id=A, platform='email', normalized='support@company.com'}

Messages:
  - Message 1: from support@company.com → Principal A ✅
  - Message 2: from support@company.com → Principal A ❌ (Should be Principal B)
```

**The Problem:** Bob's message is incorrectly attributed to Alice!

---

## Why This Wasn't Caught Earlier

1. **Constraint was recently added** - Previously, duplicates might have been allowed
2. **Testing with unique identities** - Personal emails/phones rarely shared
3. **No multi-user message scenarios** - Testing focused on single-person communications
4. **Comment says "TODO"** - Line 207 in base.py explicitly notes "TODO: Implement proper identity resolution"

---

## Root Cause Analysis

The system has **two different identity resolution strategies**:

### Strategy 1: Simple Global Mapping (base.py)
**Philosophy:** One identity = one person forever
- First person to use an identity "owns" it
- All future messages from that identity → same person
- Simple, fast, but **wrong for shared identities**

**Used by:**
- `src/ingestion/base.py` (email, iMessage ingestion)

### Strategy 2: Multi-Signal Resolution (identity_resolver.py)
**Philosophy:** Identities can be shared; use multiple signals to resolve
- Considers multiple identity types
- Handles ambiguity and confidence scores
- Can link same identity to different people
- Correct, but **not used by message ingestion**

**Used by:**
- `src/utils/identity_resolver.py`
- Contacts import
- Manual operations

**The Issue:** Message ingestion uses Strategy 1 when it should use Strategy 2!

---

## Impact Assessment

### Critical Impact Scenarios

1. **Shared Email Accounts**
   - Team emails (`support@`, `info@`)
   - Catch-all addresses
   - Forwarded messages

2. **Reassigned Identities**
   - Phone numbers reassigned to new owners
   - Email addresses recycled after employee leaves
   - Usernames on platforms

3. **Temporary/Shared Devices**
   - Shared iMessage device
   - Borrowed phone

4. **Group Conversations**
   - Messages in group thread from shared account
   - Mailing list posts

### Data Corruption Examples

**Before Fix:**
```
Reality:
  Alice sends from personal@alice.com
  Bob sends from personal@bob.com
  Both send from team@company.com

Database:
  Principal A (Alice):
    - personal@alice.com
    - team@company.com ← Created first

  Messages from Bob at team@company.com → Principal A ❌
```

**After Fix (Proper Resolution):**
```
Database:
  Principal A (Alice):
    - personal@alice.com
    - team@company.com (confidence: 0.9)

  Principal B (Bob):
    - personal@bob.com
    - team@company.com (confidence: 0.9)

  Messages correctly attributed based on context ✅
```

---

## Why Other Code Paths Are Safe

### Contacts Import (`contacts.py`) - ✅ SAFE

**Why:** Each contact is a distinct person by definition
```python
# Contacts import knows the principal_id upfront
for contact in contacts:
    principal = get_or_create_principal(contact)

    for identity in contact.identities:
        # Checks with principal_id included
        existing = session.query(IdentityClaim).filter_by(
            principal_id=principal.id,  # ✅ Has principal_id
            platform='contacts',
            normalized=identity.normalized
        ).first()
```

### MCP Operations (`write_tools.py`) - ✅ SAFE

**Why:** User explicitly provides principal_id
```python
def add_contact_identity(person_id, kind, value, platform):
    # User specifies which person
    existing = session.query(IdentityClaim).filter_by(
        principal_id=person_id,  # ✅ Provided by user
        platform=platform,
        normalized=normalized
    ).first()
```

### Message Ingestion (`base.py`) - ❌ UNSAFE

**Why:** Doesn't know principal_id upfront - must resolve it
```python
# Message arrives with just the identity
message = {
    'from': 'support@company.com',
    'subject': 'Help request',
    # No principal_id - must figure out WHO this is!
}

# Current code assumes first person found = correct person ❌
```

---

## Resolution Options

### Option 1: Refactor to Use identity_resolver (RECOMMENDED)

**Approach:** Replace simple mapping with proper resolution

**Changes Required:**
```python
# In base.py, replace _process_identity_claim() with:

def _process_identity_claim(self, session, identity_data: Dict[str, Any]):
    """Use proper identity resolution instead of simple mapping."""
    from src.utils.identity_resolver import resolve_or_create_principal

    # Let the resolver handle ambiguity, confidence, etc.
    principal = resolve_or_create_principal(
        session=session,
        identities=[identity_data],
        display_name=identity_data['value'],
        platforms=[identity_data['platform']]
    )

    # Return the appropriate claim for this principal
    claim = session.query(IdentityClaim).filter_by(
        principal_id=principal.id,
        platform=identity_data['platform'],
        normalized=identity_data['normalized']
    ).first()

    return claim
```

**Pros:**
- ✅ Handles shared identities correctly
- ✅ Uses existing, tested code
- ✅ Maintains architectural consistency
- ✅ Future-proof for advanced resolution

**Cons:**
- ⚠️ More complex (but complexity is necessary)
- ⚠️ May create multiple principals for ambiguous cases (by design)
- ⚠️ Requires testing with real message data

**Estimated Effort:** 1-2 days (refactor + testing)

---

### Option 2: Change Constraint to Global Uniqueness

**Approach:** Make constraint match simple code assumption

**Changes Required:**
```sql
-- Remove current constraint
ALTER TABLE identity_claim DROP CONSTRAINT uq_identity_per_platform;

-- Add new global constraint
ALTER TABLE identity_claim
ADD CONSTRAINT uq_identity_global
UNIQUE (platform, normalized);
```

**Pros:**
- ✅ Code immediately works as-is
- ✅ Simple, fast queries
- ✅ No refactoring needed

**Cons:**
- ❌ **Cannot handle shared identities** (team emails, shared phones)
- ❌ **Cannot handle reassigned identities** (recycled phone numbers)
- ❌ Loses provenance granularity documented as key feature
- ❌ Breaks documented architecture design
- ❌ Makes system unsuitable for multi-person communications

**Risk Level:** HIGH - Fundamentally limits system capabilities

**Estimated Effort:** 1 day (migration + testing)

---

### Option 3: Add Application-Level Disambiguation

**Approach:** Keep simple code but add disambiguation logic

**Changes Required:**
```python
def _process_identity_claim(self, session, identity_data: Dict[str, Any]):
    # Find all claims with this identity
    existing_claims = session.query(IdentityClaim).filter_by(
        platform=identity_data['platform'],
        normalized=identity_data['normalized']
    ).all()

    if not existing_claims:
        # New identity - create new principal
        return create_new_principal(identity_data)

    if len(existing_claims) == 1:
        # Unambiguous - use existing principal
        return existing_claims[0]

    # AMBIGUOUS - need to decide
    # Option 3a: Prompt user
    # Option 3b: Use heuristics (message context, headers, etc.)
    # Option 3c: Create new principal and flag for manual merge
    return disambiguate_identity(existing_claims, message_context)
```

**Pros:**
- ✅ Handles both unique and shared identities
- ✅ Can be incremental (start simple, add heuristics)
- ✅ Preserves constraint design

**Cons:**
- ⚠️ Essentially reimplements `identity_resolver`
- ⚠️ Disambiguation logic complex and error-prone
- ⚠️ Requires message context analysis

**Estimated Effort:** 2-3 weeks (complex disambiguation logic)

---

## Recommendation

**Use Option 1: Refactor to Use identity_resolver**

**Rationale:**
1. The `identity_resolver` module **already exists** and handles this correctly
2. Maintains documented architectural design (platform-level provenance)
3. Handles edge cases: shared identities, ambiguity, confidence
4. Most future-proof for advanced features (AI-assisted resolution, etc.)
5. Aligns all code paths to use the same resolution strategy

**Implementation Plan:**

1. **Phase 1: Refactor `base.py`** (Day 1)
   - Replace `_process_identity_claim()` with `identity_resolver` calls
   - Update tests
   - Document changes

2. **Phase 2: Test with Real Data** (Day 2)
   - Run on sample email corpus
   - Verify principal creation/linking
   - Check for regression

3. **Phase 3: Migration** (If needed)
   - Script to re-process existing messages with new logic
   - Fix any mis-attributed messages

**Acceptance Criteria:**
- ✅ Shared identities create separate principals
- ✅ Messages correctly attributed
- ✅ No IntegrityErrors during ingestion
- ✅ All tests pass

---

## Temporary Workaround

Until fixed, **DO NOT USE** base ingestion pipeline for:
- Email ingestion with shared accounts
- iMessage ingestion with shared devices
- Any scenario with identity ambiguity

**Safe to use:**
- Single-user personal email
- Unique phone numbers
- No shared/reassigned identities

**Alternative:**
Use contacts import and MCP operations for now (both are safe).

---

## Testing Requirements

After implementing the fix, test these scenarios:

### Test Case 1: Shared Email
```
1. Import message from alice@personal.com
2. Import message from team@company.com (sender: Alice)
3. Import message from bob@personal.com
4. Import message from team@company.com (sender: Bob)

Expected:
- 2 principals created (Alice, Bob)
- Alice has: alice@personal.com, team@company.com
- Bob has: bob@personal.com, team@company.com
- Messages correctly attributed
```

### Test Case 2: Reassigned Phone
```
1. Import SMS from +1234567890 (original owner: Alice)
2. Wait (simulated time gap)
3. Import SMS from +1234567890 (new owner: Bob)

Expected:
- System recognizes potential reassignment
- Creates separate principals or flags for review
- No silent mis-attribution
```

### Test Case 3: Unique Identities
```
1. Import messages from unique personal emails
2. Verify single principal per unique identity (regression test)

Expected:
- Same behavior as before fix
- No duplicate principals for unique identities
```

---

## Related Files

- **Problem Code:** `src/ingestion/base.py:_process_identity_claim()`
- **Solution Code:** `src/utils/identity_resolver.py`
- **Constraint Definition:** `src/models/people.py`
- **Documentation:** `docs/IDENTITY_UNIQUENESS.md`
- **Migration:** `migrations/001_add_identity_uniqueness_constraint.py`

---

## References

- Original constraint design decision: `CLAUDE.md` (lines 121-148)
- Identity resolution module: `src/utils/identity_resolver.py`
- Database schema: `src/models/people.py`
- Code review findings: This document

---

## Status Updates

**2025-10-05:** Issue identified during code review after adding uniqueness constraint
- Severity: CRITICAL
- Status: DOCUMENTED, NOT FIXED
- Next step: Decide on resolution approach (recommend Option 1)
