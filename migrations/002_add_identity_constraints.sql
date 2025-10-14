-- Migration 002: Add critical identity claim constraints and indexes
-- 
-- This migration adds database-level constraints to enforce data integrity
-- for identity claims that was previously only enforced at application level.

-- Add unique constraint to prevent duplicate identity claims per person
-- This prevents race conditions from creating duplicate entries
ALTER TABLE identity_claim 
ADD CONSTRAINT identity_claim_unique_per_person 
UNIQUE (principal_id, kind, normalized);

-- Add optimized compound index for duplicate checks and lookups
-- This improves performance of the frequent duplicate checking queries
CREATE INDEX CONCURRENTLY IF NOT EXISTS identity_claim_compound_idx 
ON identity_claim (principal_id, kind, normalized);

-- Add comment to document the constraint
COMMENT ON CONSTRAINT identity_claim_unique_per_person ON identity_claim IS 
'Ensures each person can only have one identity claim per (kind, normalized_value) combination. Prevents application-level race conditions from creating duplicates.';

-- Verify no existing duplicates before applying constraint
-- This query should return 0 rows if data is clean
DO $$
DECLARE
    duplicate_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO duplicate_count
    FROM (
        SELECT principal_id, kind, normalized, COUNT(*)
        FROM identity_claim 
        GROUP BY principal_id, kind, normalized 
        HAVING COUNT(*) > 1
    ) duplicates;
    
    IF duplicate_count > 0 THEN
        RAISE WARNING 'Found % duplicate identity claims that need manual resolution before constraint can be applied', duplicate_count;
    ELSE
        RAISE NOTICE 'No duplicate identity claims found - constraint can be safely applied';
    END IF;
END
$$;