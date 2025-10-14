# Database Migrations

This directory contains database schema migrations for the Memory Database system.

## Running Migrations

### Run all migrations
```bash
uv run python cli.py migrate
```

### Run a specific migration
```bash
uv run python cli.py migrate 001
```

### Run migration directly
```bash
python migrations/001_add_identity_uniqueness_constraint.py
```

## Available Migrations

### 001_add_identity_uniqueness_constraint.py

**Purpose**: Adds a unique constraint to enforce one identity claim per person per platform.

**What it does**:
1. Scans for duplicate identity claims (same person + platform + normalized value)
2. Consolidates duplicates by:
   - Keeping the most recent claim
   - Recording merged metadata if kinds differ (rare data error case)
   - Deleting older duplicate claims
3. Adds database constraint: `UNIQUE (principal_id, platform, normalized)`

**When to run**:
- After upgrading from a version without the constraint
- When you notice duplicate identity claims in the database

**Note**: With platform-level granularity, duplicates should be rare (only from data errors or race conditions)

**Safe to re-run**: Yes - checks if constraint exists and skips if already applied

## Creating New Migrations

Migrations should follow this naming pattern:
```
NNN_descriptive_name.py
```

Where `NNN` is a zero-padded sequential number (001, 002, etc.)

Each migration should:
1. Have a docstring explaining what it does
2. Implement a `main()` function
3. Handle idempotency (safe to run multiple times)
4. Use transactions for data modifications
5. Print clear progress and error messages using Rich console

Example template:
```python
#!/usr/bin/env python3
"""
Migration: Brief description

Details about what this migration does.
"""

from memory_database.database.connection import DatabaseManager, DatabaseSettings
from rich.console import Console

console = Console()

def main():
    console.print("[bold blue]Migration Name[/bold blue]\\n")

    try:
        settings = DatabaseSettings()
        db_manager = DatabaseManager(settings)

        with db_manager.get_session() as session:
            # Your migration code here
            pass

        console.print("[bold green]Migration completed! ✓[/bold green]")

    except Exception as e:
        console.print(f"[bold red]✗ Migration failed: {e}[/bold red]")
        raise

if __name__ == "__main__":
    main()
```

## Best Practices

1. **Test on a copy of production data** before running in production
2. **Backup your database** before running migrations
3. **Review the migration code** to understand what changes will be made
4. **Monitor the output** during migration execution
5. **Verify the results** after migration completes
