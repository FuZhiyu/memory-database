#!/usr/bin/env python3

import os
import sys
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import click
import structlog
from rich.console import Console
from rich.logging import RichHandler

from memory_database.database.connection import DatabaseManager, DatabaseSettings
from memory_database.models import IdentityClaim
from memory_database.utils.normalization import normalize_identity_value
from memory_database.ingestion.base import IngestionPipeline
from memory_database.ingestion.email import EmailIngestionSource
from memory_database.ingestion.imessage import iMessageIngestionSource, iMessageIncrementalPipeline


console = Console()

def setup_logging(level: str = "INFO"):
    """Set up structured logging with rich output."""
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(structlog, level.upper(), 20)  # 20 = INFO level
        ),
        logger_factory=structlog.WriteLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _ensure_imessage_bridge():
    """Ensure the Rust iMessage bridge extension is available, building it if needed."""
    try:
        import imessage_bridge  # type: ignore  # noqa: F401
        return
    except ImportError as original_error:
        console.print("[yellow]Building iMessage bridge extension...[/yellow]")

        result = subprocess.run(
            ["maturin", "develop"],
            cwd="imessage-bridge",
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            message_lines = [
                "Failed to build imessage_bridge extension.",
                "Ensure Rust (https://rustup.rs/) and `maturin` are installed.",
            ]
            if stderr:
                message_lines.append(f"maturin stderr: {stderr}")
            raise click.ClickException("\n".join(message_lines)) from original_error

        console.print("[green]✓ Extension built successfully[/green]")

        try:
            import imessage_bridge  # type: ignore  # noqa: F401
        except Exception as import_error:  # pragma: no cover - defensive logging
            raise click.ClickException(
                "imessage_bridge build completed but import still fails. "
                "Verify the active virtual environment matches the build target."
            ) from import_error


def _run_imessage_import(
    *,
    db_path: Optional[str],
    last_sync_timestamp: Optional[float],
    limit: Optional[int],
    known_contacts_only: bool,
    rewind_seconds: Optional[int],
) -> Dict[str, Any]:
    settings = DatabaseSettings()
    db_manager = DatabaseManager(settings)
    pipeline = iMessageIncrementalPipeline(db_manager)
    return pipeline.run_incremental_import(
        db_path=db_path,
        last_sync_timestamp=last_sync_timestamp,
        limit=limit,
        known_contacts_only=known_contacts_only,
        rewind_seconds=rewind_seconds,
    )


def _format_timestamp(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    dt = datetime.fromtimestamp(value, tz=timezone.utc)
    return dt.isoformat()


def _display_imessage_stats(stats: Dict[str, Any], known_contacts_only: bool) -> None:
    console.print("[green]✓ iMessage import completed![/green]")

    candidate = stats.get('candidate_messages', stats.get('total_processed', 0))
    console.print(f"Candidates evaluated: {candidate}")
    console.print(
        f"New messages stored: {stats.get('new_messages', 0)}"
        f" | Skipped existing: {stats.get('skipped_messages', 0)}"
    )

    if known_contacts_only:
        console.print(
            f"Skipped (unknown contacts): {stats.get('skipped_unknown_contacts', 0)}"
        )

    console.print(
        f"Principals: +{stats.get('new_principals', 0)} new, "
        f"{stats.get('linked_principals', 0)} linked"
    )
    console.print(f"New identities: {stats.get('new_identities', 0)}")
    console.print(
        f"Attachments stored: {stats.get('attachments_stored', 0)}"
        f" | Failed: {stats.get('attachments_failed', 0)}"
    )

    resume_ts = stats.get('last_sync_timestamp_used')
    query_start = stats.get('query_start_timestamp')
    processed_through = stats.get('processed_through_timestamp')

    if resume_ts == 0:
        resume_text = "0 (full import)"
    else:
        resume_text = _format_timestamp(resume_ts)

    if query_start == 0:
        query_text = "0 (full scan)"
    else:
        query_text = _format_timestamp(query_start)

    processed_text = _format_timestamp(processed_through)

    if resume_text or query_text or processed_text:
        console.print(
            "Resume checkpoint: "
            f"{resume_text or '—'} | Query start: {query_text or '—'} | "
            f"Processed through: {processed_text or '—'}"
        )

    auto_rowid = stats.get('auto_detected_last_rowid')
    if auto_rowid is not None:
        console.print(f"Detected checkpoint rowid: {auto_rowid}")

@click.group()
@click.option("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
def cli(log_level):
    """Messages RAG System - Personal Communications Indexer"""
    setup_logging(log_level)


@cli.command()
def init_db():
    """Initialize the database with required tables."""
    try:
        console.print("[blue]Initializing database...[/blue]")

        settings = DatabaseSettings()
        db_manager = DatabaseManager(settings)

        # Create all tables
        import memory_database.models  # Import all models
        db_manager.create_tables()

        console.print("[green]✓ Database initialized successfully![/green]")
        console.print(f"Database URL: {settings.redacted_database_url()}")

    except Exception as e:
        console.print(f"[red]✗ Failed to initialize database: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('migration_name', required=False)
def migrate(migration_name):
    """Run database migrations.

    If no migration name is provided, runs all pending migrations in order.
    """
    try:
        import importlib.util
        from pathlib import Path

        migrations_dir = Path(__file__).parent / 'migrations'

        if not migrations_dir.exists():
            console.print("[yellow]No migrations directory found.[/yellow]")
            return

        # Get all migration files
        migration_files = sorted(migrations_dir.glob('*.py'))
        migration_files = [f for f in migration_files if not f.name.startswith('__')]

        if not migration_files:
            console.print("[yellow]No migrations found.[/yellow]")
            return

        # Filter by name if provided
        if migration_name:
            migration_files = [f for f in migration_files if migration_name in f.stem]
            if not migration_files:
                console.print(f"[red]Migration '{migration_name}' not found.[/red]")
                sys.exit(1)

        # Run migrations
        for migration_file in migration_files:
            console.print(f"[blue]Running migration: {migration_file.stem}[/blue]")

            # Import and run the migration
            spec = importlib.util.spec_from_file_location(migration_file.stem, migration_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Call the main function if it exists
            if hasattr(module, 'main'):
                module.main()

            console.print()

    except Exception as e:
        console.print(f"[red]✗ Migration failed: {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        sys.exit(1)


@cli.command()
@click.option("--contacts-path", help="Path to contacts JSON file")
@click.option("--incremental", is_flag=True, default=True, help="Use incremental import (default)")
def import_contacts(contacts_path, incremental):
    """Import contacts from Google Contacts JSON file."""
    if not contacts_path:
        # Try default location
        contacts_path = "Sources/Contacts/contacts.json"
        if not Path(contacts_path).exists():
            console.print("[red]Error: No contacts file specified and default not found[/red]")
            console.print("Use --contacts-path to specify the contacts JSON file location")
            console.print("Expected format: Google Contacts export JSON")
            sys.exit(1)
    
    if not Path(contacts_path).exists():
        console.print(f"[red]Error: Contacts file does not exist: {contacts_path}[/red]")
        sys.exit(1)
    
    try:
        console.print("[blue]Starting contacts import...[/blue]")
        
        settings = DatabaseSettings()
        db_manager = DatabaseManager(settings)
        
        if incremental:
            from memory_database.ingestion.contacts import ContactsIncrementalPipeline
            pipeline = ContactsIncrementalPipeline(db_manager)
            pipeline.logger = structlog.get_logger().bind(component='contacts_pipeline')
            stats = pipeline.run_incremental_import(contacts_path)
            
            console.print("[green]✓ Incremental contacts import completed![/green]")
            console.print(f"Total processed: {stats['total_processed']}")
            console.print(f"New contacts: {stats['new_contacts']}")
            console.print(f"Updated contacts: {stats['updated_contacts']}")  
            console.print(f"Unchanged contacts: {stats['unchanged_contacts']}")
            console.print(f"New identities: {stats['new_identities']}")
            console.print(f"Updated identities: {stats['updated_identities']}")
        else:
            # Regular ingestion (not incremental)
            from memory_database.ingestion.base import IngestionPipeline
            from memory_database.ingestion.contacts import ContactsIngestionSource
            
            pipeline = IngestionPipeline(db_manager)
            contacts_source = ContactsIngestionSource(db_manager)
            pipeline.add_source(contacts_source)
            
            source_paths = {"contacts": contacts_path}
            pipeline.run_ingestion(source_paths)
            
            console.print("[green]✓ Contacts import completed![/green]")
        
    except Exception as e:
        console.print(f"[red]✗ Contacts import failed: {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        sys.exit(1)


@cli.command()
@click.option("--email-path", help="Path to email data (MBOX file or directory)")
@click.option("--dry-run", is_flag=True, help="Preview what would be ingested without saving")
def ingest(email_path, dry_run):
    """Ingest data from various sources."""
    if not any([email_path]):
        console.print("[red]Error: At least one data source must be specified[/red]")
        console.print("Use --email-path to specify email data location")
        sys.exit(1)
    
    try:
        console.print("[blue]Starting ingestion pipeline...[/blue]")
        
        if dry_run:
            console.print("[yellow]DRY RUN MODE - No data will be saved[/yellow]")
        
        settings = DatabaseSettings()
        db_manager = DatabaseManager(settings)
        pipeline = IngestionPipeline(db_manager)
        
        source_paths = {}
        
        if email_path:
            if not Path(email_path).exists():
                console.print(f"[red]Error: Email path does not exist: {email_path}[/red]")
                sys.exit(1)
            
            email_source = EmailIngestionSource(db_manager)
            pipeline.add_source(email_source)
            source_paths["email"] = email_path
            console.print(f"Added email source: {email_path}")
        
        if dry_run:
            console.print("[yellow]Would ingest from these sources:[/yellow]")
            for platform, path in source_paths.items():
                console.print(f"  - {platform}: {path}")
        else:
            pipeline.run_ingestion(source_paths)
            console.print("[green]✓ Ingestion completed![/green]")
        
    except Exception as e:
        console.print(f"[red]✗ Ingestion failed: {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        sys.exit(1)


@cli.command()
@click.option("--limit", default=10, help="Number of recent messages to show")
def recent(limit):
    """Show recent messages from the database."""
    try:
        settings = DatabaseSettings()
        db_manager = DatabaseManager(settings)
        
        with db_manager.get_session() as session:
            from memory_database.models import Message, Thread, Channel, PersonMessage, Principal
            
            recent_messages = session.query(Message).join(Thread).join(Channel)\
                .order_by(Message.sent_at.desc())\
                .limit(limit)\
                .all()
            
            if not recent_messages:
                console.print("[yellow]No messages found in database[/yellow]")
                return
            
            console.print(f"[blue]Last {len(recent_messages)} messages:[/blue]")
            console.print()
            
            for msg in recent_messages:
                # Get sender info
                sender_link = session.query(PersonMessage).filter_by(
                    message_id=msg.id, role='sender'
                ).first()
                
                sender_name = "Unknown"
                if sender_link:
                    principal = session.query(Principal).get(sender_link.principal_id)
                    sender_name = principal.display_name if principal else "Unknown"
                
                # Show message info
                console.print(f"[cyan]{msg.sent_at}[/cyan] - [green]{sender_name}[/green]")
                console.print(f"  Thread: {msg.thread.subject or 'No Subject'}")
                console.print(f"  Channel: {msg.thread.channel.name}")
                
                # Show truncated content
                content = msg.content[:100] + "..." if len(msg.content or "") > 100 else (msg.content or "")
                console.print(f"  Content: {content}")
                
                # Show attachments if any
                if msg.attachments:
                    console.print(f"  [yellow]Attachments ({len(msg.attachments)}):[/yellow]")
                    for att in msg.attachments:
                        console.print(f"    - {att.filename} ({att.mime_type}, {att.file_size:,} bytes)")
                        console.print(f"      Storage: {att.storage_method} at {att.stored_path}")
                
                console.print()
    
    except Exception as e:
        console.print(f"[red]✗ Failed to get recent messages: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.option("--db-path", help="Path to the iMessage SQLite database (default: system database)")
@click.option("--last-sync", type=float, help="Override resume timestamp (Unix seconds)")
@click.option("--limit", type=int, help="Limit the number of messages to evaluate")
@click.option(
    "--known-contacts-only/--all-contacts",
    default=True,
    show_default=True,
    help="Only import messages that involve known contacts",
)
@click.option(
    "--rewind-seconds",
    type=int,
    help="Seconds to rewind the resume checkpoint before querying (default: 30)",
)
def import_imessages(db_path, last_sync, limit, known_contacts_only, rewind_seconds):
    """Import iMessage data with manual control over resume parameters."""
    try:
        console.print("[blue]Starting iMessage import...[/blue]")
        _ensure_imessage_bridge()

        stats = _run_imessage_import(
            db_path=db_path,
            last_sync_timestamp=last_sync,
            limit=limit,
            known_contacts_only=known_contacts_only,
            rewind_seconds=rewind_seconds,
        )
        _display_imessage_stats(stats, known_contacts_only)

    except click.ClickException as exc:
        console.print(f"[red]✗ iMessage import failed: {exc}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗ iMessage import failed: {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        sys.exit(1)


@cli.command(name="sync-imessages")
@click.option("--db-path", help="Path to the iMessage SQLite database (default: system database)")
@click.option("--limit", type=int, help="Limit the number of messages to evaluate")
@click.option(
    "--known-contacts-only/--all-contacts",
    default=True,
    show_default=True,
    help="Only import messages that involve known contacts",
)
@click.option(
    "--rewind-seconds",
    type=int,
    help="Seconds to rewind the resume checkpoint before querying (default: 30)",
)
@click.option(
    "--force-full",
    is_flag=True,
    help="Ignore existing checkpoints and rescan the entire database",
)
def sync_imessages(db_path, limit, known_contacts_only, rewind_seconds, force_full):
    """Automatically resume incremental iMessage ingestion using the last stored checkpoint."""
    try:
        console.print("[blue]Syncing iMessage data...[/blue]")
        _ensure_imessage_bridge()

        last_sync = 0.0 if force_full else None
        stats = _run_imessage_import(
            db_path=db_path,
            last_sync_timestamp=last_sync,
            limit=limit,
            known_contacts_only=known_contacts_only,
            rewind_seconds=rewind_seconds,
        )
        _display_imessage_stats(stats, known_contacts_only)

    except click.ClickException as exc:
        console.print(f"[red]✗ iMessage sync failed: {exc}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]✗ iMessage sync failed: {e}[/red]")
        import traceback
        console.print(traceback.format_exc())
        sys.exit(1)


@cli.command(name="photos-import-people-links")
@click.option("--limit", type=int, default=None, help="Max number of Photos people to process")
@click.option("--overwrite/--no-overwrite", default=False, help="Overwrite existing links if a different principal is resolved")
@click.option("--dry-run/--no-dry-run", default=False, help="Report actions without writing to the database")
def photos_import_people_links(limit: int | None, overwrite: bool, dry_run: bool):
    """Import Photos People and link their person UUIDs as identity claims.

    - Treats macOS Photos as a platform: platform='photos'
    - Stores Photos person UUID as an IdentityClaim with kind='user_id' and value=<uuid>
    - Resolves which Principal to link by matching Photos person name to your contacts
    """
    try:
        # Import osxphotos lazily to avoid import-time failures when Photos is unavailable
        try:
            from osxphotos import PhotosDB  # type: ignore
        except Exception as e:
            raise click.ClickException(
                f"osxphotos not available or Photos not accessible: {e}.\n"
                "Install osxphotos and grant Full Disk Access to this process."
            )

        # Open Photos DB
        try:
            pdb = PhotosDB()
        except Exception as e:
            raise click.ClickException(
                f"Failed to open Photos library. Ensure Full Disk Access is granted. Error: {e}"
            )

        # Collect person entries (uuid, name)
        try:
            pi_list = getattr(pdb, "person_info", None)
            if pi_list is None:
                names = list(getattr(pdb, "persons", []) or [])
                people = [{"uuid": None, "name": n} for n in names]
            else:
                people = list(pi_list)
        except Exception:
            names = list(getattr(pdb, "persons", []) or [])
            people = [{"uuid": None, "name": n} for n in names]

        if limit is not None:
            people = people[: int(limit)]

        # Prepare DB
        settings = DatabaseSettings()
        db_manager = DatabaseManager(settings)

        stats = {
            "processed": 0,
            "linked_new": 0,
            "linked_existing": 0,
            "updated": 0,
            "unresolved": 0,
            "conflicts": 0,
            "errors": 0,
        }
        unresolved_details: list[dict] = []

        from memory_database.mcp_server.queries import find_person_by_any_identity

        with db_manager.get_session() as session:
            for item in people:
                stats["processed"] += 1
                try:
                    # Extract uuid/name from either dict-like or osxphotos object
                    uuid_val = getattr(item, "uuid", None)
                    if uuid_val is None and isinstance(item, dict):
                        uuid_val = item.get("uuid")
                    name_val = getattr(item, "name", None)
                    if name_val is None and isinstance(item, dict):
                        name_val = item.get("name")

                    if not name_val:
                        stats["unresolved"] += 1
                        unresolved_details.append({
                            "uuid": uuid_val,
                            "name": None,
                            "reason": "no-name",
                        })
                        continue

                    # Resolve principal by name
                    principal_id = find_person_by_any_identity(session=session, person_name=name_val)
                    if not principal_id:
                        stats["unresolved"] += 1
                        unresolved_details.append({
                            "uuid": uuid_val,
                            "name": name_val,
                            "reason": "no-principal-match",
                        })
                        continue

                    if not uuid_val:
                        stats["unresolved"] += 1
                        unresolved_details.append({
                            "uuid": None,
                            "name": name_val,
                            "reason": "no-uuid",
                        })
                        continue

                    normalized_uuid = normalize_identity_value(uuid_val, "user_id")

                    # Check for any existing claim for this UUID on any principal
                    existing_any = (
                        session.query(IdentityClaim)
                        .filter(
                            IdentityClaim.platform == "photos",
                            IdentityClaim.normalized == normalized_uuid,
                        )
                        .all()
                    )

                    if existing_any:
                        # Already linked to this principal?
                        same = [c for c in existing_any if c.principal_id == principal_id]
                        if same:
                            stats["linked_existing"] += 1
                            continue
                        # Different principal holds this UUID
                        if overwrite and not dry_run:
                            claim = existing_any[0]
                            claim.principal_id = principal_id
                            stats["updated"] += 1
                        else:
                            stats["conflicts"] += 1
                            unresolved_details.append({
                                "uuid": uuid_val,
                                "name": name_val,
                                "reason": "conflict-existing-on-different-principal",
                            })
                            continue
                    else:
                        if not dry_run:
                            claim = IdentityClaim(
                                principal_id=principal_id,
                                platform="photos",
                                kind="user_id",
                                value=uuid_val,
                                normalized=normalized_uuid,
                                confidence=0.98,
                                extra={"source": "photos", "person_name": name_val},
                            )
                            session.add(claim)
                        stats["linked_new"] += 1
                except Exception as ex:
                    stats["errors"] += 1
                    continue

        console.print("[green]✓ Photos people import complete[/green]")
        console.print(
            f"Processed: {stats['processed']} | New: {stats['linked_new']} | Existing: {stats['linked_existing']} | "
            f"Updated: {stats['updated']} | Conflicts: {stats['conflicts']} | Unresolved: {stats['unresolved']} | Errors: {stats['errors']}"
        )
        if stats["unresolved"]:
            console.print("\n[yellow]Unresolved entries:[/yellow]")
            shown = 0
            for item in unresolved_details:
                name = item.get("name") or "<no name>"
                uuid = item.get("uuid") or "<no uuid>"
                reason = item.get("reason") or "unknown"
                console.print(f" - {name} [{uuid}] — {reason}")
                shown += 1
                if shown >= 100:
                    console.print("... (truncated) ...")
                    break

    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Import failed: {e}")

@cli.command()
def status():
    """Show database status and statistics."""
    try:
        console.print("[blue]Database Status[/blue]")
        
        settings = DatabaseSettings()
        db_manager = DatabaseManager(settings)
        
        with db_manager.get_session() as session:
            from memory_database.models import Principal, IdentityClaim, Message, Channel, Thread, PersonMessage, MessageAttachment
            
            principal_count = session.query(Principal).count()
            identity_count = session.query(IdentityClaim).count()
            message_count = session.query(Message).count()
            channel_count = session.query(Channel).count()
            thread_count = session.query(Thread).count()
            person_message_count = session.query(PersonMessage).count()
            attachment_count = session.query(MessageAttachment).count()
            
            console.print(f"Principals (People): {principal_count}")
            console.print(f"Identity Claims: {identity_count}")
            console.print(f"Messages: {message_count}")
            console.print(f"Channels: {channel_count}")
            console.print(f"Threads: {thread_count}")
            console.print(f"Person-Message Links: {person_message_count}")
            console.print(f"Message Attachments: {attachment_count}")
        
        console.print(f"Database URL: {settings.redacted_database_url()}")
        
    except Exception as e:
        console.print(f"[red]✗ Failed to get status: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
