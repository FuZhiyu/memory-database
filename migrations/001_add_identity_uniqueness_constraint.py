#!/usr/bin/env python3
"""
Migration: Add unique constraint on (principal_id, platform, normalized)

This migration:
1. Detects duplicate identity claims per person per platform
2. Consolidates duplicates by keeping the most recent claim
3. Merges kind information if duplicates exist (rare edge case)
4. Adds the database-level uniqueness constraint

Note: With platform-level granularity, duplicates should be rare.
They would only occur from data errors or race conditions.

Run with: python migrations/001_add_identity_uniqueness_constraint.py
"""

import sys
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
PROJECT_ROOT = str(Path(__file__).parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import text
from rich.console import Console
from rich.table import Table

from src.database.connection import DatabaseManager, DatabaseSettings
from src.models.people import IdentityClaim

console = Console()


def find_duplicates(session):
    """Find duplicate identity claims for the same person on the same platform."""
    query = text("""
        SELECT
            principal_id,
            platform,
            normalized,
            COUNT(*) as count,
            ARRAY_AGG(id ORDER BY last_seen DESC) as claim_ids,
            ARRAY_AGG(kind) as kinds,
            ARRAY_AGG(last_seen ORDER BY last_seen DESC) as last_seens
        FROM identity_claim
        WHERE normalized IS NOT NULL
        GROUP BY principal_id, platform, normalized
        HAVING COUNT(*) > 1
        ORDER BY count DESC
    """)

    result = session.execute(query)
    return result.fetchall()


def consolidate_duplicate(session, principal_id, platform, normalized, claim_ids, kinds, last_seens):
    """
    Consolidate duplicate claims by:
    1. Keeping the most recent claim (first in claim_ids since ordered by last_seen DESC)
    2. Merging kind info if different (rare edge case - would be data error)
    3. Deleting older duplicates
    """
    # Keep the first (most recent) claim
    keep_id = claim_ids[0]
    delete_ids = claim_ids[1:]

    # Update the kept claim with merged metadata if needed
    claim = session.query(IdentityClaim).filter_by(id=keep_id).first()
    if claim:
        claim.extra = claim.extra or {}

        # If kinds differ (shouldn't happen, but handle it), record all kinds
        unique_kinds = list(dict.fromkeys(kinds))  # Preserve order, remove duplicates
        if len(unique_kinds) > 1:
            claim.extra['migrated_from_kinds'] = unique_kinds

        claim.extra['consolidated_duplicate_count'] = len(delete_ids)
        claim.extra['consolidated_at'] = datetime.now(timezone.utc).isoformat()

    # Delete duplicate claims
    for delete_id in delete_ids:
        dup_claim = session.query(IdentityClaim).filter_by(id=delete_id).first()
        if dup_claim:
            session.delete(dup_claim)

    return len(delete_ids)


def add_unique_constraint(session):
    """Add the unique constraint to the database."""
    constraint_query = text("""
        ALTER TABLE identity_claim
        ADD CONSTRAINT uq_identity_per_platform
        UNIQUE (principal_id, platform, normalized)
    """)

    session.execute(constraint_query)


def check_constraint_exists(session):
    """Check if the constraint already exists."""
    query = text("""
        SELECT constraint_name
        FROM information_schema.table_constraints
        WHERE table_name = 'identity_claim'
        AND constraint_name = 'uq_identity_per_platform'
    """)

    result = session.execute(query)
    return result.fetchone() is not None


def main():
    """Run the migration."""
    console.print("[bold blue]Identity Claim Uniqueness Migration[/bold blue]\n")

    try:
        settings = DatabaseSettings()
        db_manager = DatabaseManager(settings)

        with db_manager.get_session() as session:
            # Check if constraint already exists
            if check_constraint_exists(session):
                console.print("[yellow]⚠ Constraint already exists. Skipping migration.[/yellow]")
                return

            # Find duplicates
            console.print("[blue]1. Scanning for duplicate identity claims...[/blue]")
            duplicates = find_duplicates(session)

            if not duplicates:
                console.print("[green]✓ No duplicates found![/green]\n")
            else:
                console.print(f"[yellow]Found {len(duplicates)} sets of duplicates[/yellow]\n")

                # Display duplicates in a table
                table = Table(title="Duplicate Identity Claims")
                table.add_column("Person ID", style="cyan")
                table.add_column("Platform", style="magenta")
                table.add_column("Normalized", style="green")
                table.add_column("Count", style="red")
                table.add_column("Kinds", style="yellow")

                for dup in duplicates[:10]:  # Show first 10
                    table.add_row(
                        dup.principal_id[:12] + "...",
                        dup.platform,
                        dup.normalized[:30],
                        str(dup.count),
                        ", ".join(set(dup.kinds))
                    )

                if len(duplicates) > 10:
                    table.add_row("...", "...", "...", "...", "...")

                console.print(table)
                console.print()

                # Consolidate duplicates
                console.print("[blue]2. Consolidating duplicates...[/blue]")
                total_removed = 0

                for dup in duplicates:
                    removed = consolidate_duplicate(
                        session,
                        dup.principal_id,
                        dup.platform,
                        dup.normalized,
                        dup.claim_ids,
                        dup.kinds,
                        dup.last_seens
                    )
                    total_removed += removed

                session.commit()
                console.print(f"[green]✓ Consolidated {len(duplicates)} duplicate sets[/green]")
                console.print(f"[green]✓ Removed {total_removed} duplicate claims[/green]\n")

            # Add constraint
            console.print("[blue]3. Adding uniqueness constraint...[/blue]")
            add_unique_constraint(session)
            session.commit()
            console.print("[green]✓ Constraint added successfully![/green]\n")

            console.print("[bold green]Migration completed successfully! ✓[/bold green]")

    except Exception as e:
        console.print(f"[bold red]✗ Migration failed: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
