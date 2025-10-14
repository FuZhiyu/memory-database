# Memory Database

**A personal communication indexer and search system with MCP (Model Context Protocol) integration**

It's a vibe-coded project for personal use. Many features can be buggy.

Memory Database unifies your communication history across email, iMessage, and contacts into a searchable, people-centric database. It provides an MCP server that enables AI assistants like Claude to search your communications, find contacts, and access your photo library.

## Features

### 🔍 Unified Communication Search
- **Multi-platform ingestion**: Import from iMessage, email (MBOX/EML), and Google Contacts
- **People-centric organization**: All communications linked to canonical person records
- **Identity resolution**: Automatically connects emails, phone numbers, and usernames across platforms
- **Smart deduplication**: Prevents duplicate messages during incremental imports

### 📸 Photo Integration (macOS)
- **Search your Photos library**: Find photos by people, places, dates, and ML-detected labels
- **Contact linking**: Link Photos People to your contact database
- **Preview and export**: View photos inline or export to disk
- **Face detection**: Optional face recognition with principal linking

### 🤖 MCP Server
- **Contact search**: Find people by name, email, phone, or username with fuzzy matching
- **Message search**: Search communication history for specific people
- **Contact management**: Create contacts and manage identity claims
- **Photos integration**: Search and view photos directly through MCP tools

### 💾 Efficient Storage
- **Incremental sync**: Automatic checkpoint/resume for iMessage imports
- **APFS clones**: Zero-copy attachment storage using copy-on-write
- **PostgreSQL backend**: Robust storage with ULID primary keys

## Quick Start

### Prerequisites

- Python 3.9+
- [uv](https://astral.sh/uv/) package manager
- PostgreSQL 14+ with pgvector extension
- macOS (for iMessage and Photos integration)
- Rust toolchain (for iMessage bridge)

### Installation

1. **Install uv** (if not already installed):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

2. **Clone and set up**:
   ```bash
   cd memory-database

   # Create config from template
   cp .env.example .env
   # Edit .env with your PostgreSQL credentials

   # Install dependencies
   UV_PROJECT_ENVIRONMENT=~/.venv/memory-database uv sync
   ```

3. **Initialize database**:
   ```bash
   UV_PROJECT_ENVIRONMENT=~/.venv/memory-database uv run python -m memory_database.cli init-db
   ```

## Usage

### Import Your Data

**Import Google Contacts**:
```bash
uv run python -m memory_database.cli import-contacts --contacts-path path/to/contacts.json
```

**Sync iMessages** (incremental, with auto-checkpoint):
```bash
# First run: imports all messages
# Subsequent runs: only new messages
uv run python -m memory_database.cli sync-imessages
```

**Import Email** (MBOX/EML):
```bash
uv run python -m memory_database.cli ingest --email-path path/to/email.mbox
```

### Start the MCP Server

**Local (stdio for Claude Desktop)**:
```bash
uv run python run_mcp_server.py --transport stdio
```

**Remote (HTTP)**:
```bash
uv run python run_mcp_server.py --transport http --port 8766
```

### Claude Desktop Integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memory-database": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "$HOME/path/to/memory-database/run_mcp_server.py",
        "--transport", "stdio"
      ],
      "cwd": "$HOME/path/to/memory-database"
    }
  }
}
```

## MCP Tools

### Contact Search

**`search_person`** - Find people by any identifier:
```python
# By name (fuzzy matching)
search_person(name="John Doe", fuzzy_match=True)

# By email
search_person(email="john@example.com")

# By phone number
search_person(phone="+1-555-0123")
```

**`search_messages`** - Find communications with a person:
```python
# Search messages from a person
search_messages(
    person_id="01ABC123...",  # From search_person
    date_from="2024-01-01",
    content_contains="project"
)
```

### Contact Management

**`create_new_contact`** - Add a new person:
```python
create_new_contact(
    display_name="Jane Smith",
    identities=[{
        "kind": "email",
        "value": "jane@example.com",
        "platform": "manual"
    }],
    org="Company Name"
)
```

**`add_identity_to_contact`** - Link additional identifiers:
```python
add_identity_to_contact(
    person_id="01ABC123...",
    kind="phone",
    value="+1-555-0123",
    platform="manual"
)
```

### Photos Integration

**`photos_search`** - Find photos by multiple criteria:
```python
# By person (using contact database)
photos_search(
    person={"id": "01ABC123..."},
    date_from="2024-01-01",
    limit=10
)

# By Photos People name (fuzzy)
photos_search(people=["John Doe"], limit=10)

# By location
photos_search(place="San Francisco", limit=20)

# By ML labels
photos_search(labels=["dog", "beach"], limit=15)
```

**`view_photos`** - Display photos inline:
```python
# Show preview versions (fast, small)
view_photos(uuids=["ABC-123", "DEF-456"], preview=True)
```

**`photos_link_person`** - Link contact to Photos People:
```python
photos_link_person(
    person={"id": "01ABC123..."},
    photos_person_uuid="ABC-123-DEF",
    photos_person_label="John Doe"
)
```

**`photos_export`** - Export photos to disk:
```python
photos_export(
    uuids=["ABC-123"],
    destination="/path/to/export",
    use_preview=False  # Export full originals
)
```

## Automated Sync

Set up automatic iMessage syncing with cron:

```bash
# Edit crontab
crontab -e

# Add this line to sync every hour at the top of the hour
0 * * * * $HOME/path/to/memory-database/scripts/sync_imessages_cron.sh
```

The sync script:
- Automatically resumes from last checkpoint
- Includes a 30-second safety buffer to avoid missing messages
- Logs to `logs/imessage_sync_YYYYMMDD.log`

## Database Statistics

Check your database statistics:

```bash
uv run python -m memory_database.cli status
```

Example output:
```
Database Status:
  People: 1,234
  Identity Claims: 5,678
  Messages: 45,321
  Threads: 2,109
  Attachments: 3,456
```

View recent messages:
```bash
uv run python -m memory_database.cli recent --limit 10
```

## Configuration

### Environment Variables

Configure via `.env` file (copy from `.env.example`):

```bash
# PostgreSQL connection
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=memories_rag
POSTGRES_USER=your_username
POSTGRES_PASSWORD=your_password

# Optional: MCP HTTP authentication
MEMORY_DB_HTTP_TOKEN=your_secret_token
MEMORY_DB_HTTP_RESOURCE_URL=http://localhost:8766

# Optional: Performance tuning
MEMORY_DB_IMESSAGE_BATCH_SIZE=500
```

### Photos Access (macOS)

For Photos integration, grant **Full Disk Access** to:
- Terminal (if running from command line)
- Or your LaunchAgent service (if running as background service)

Go to: **System Settings** → **Privacy & Security** → **Full Disk Access**

## Architecture

### Data Model

**Principal** (Person)
- Canonical record representing a real person
- Contains: display_name, org, metadata
- ULID primary key for distributed scalability

**IdentityClaim**
- Links platform-specific identifiers to people
- Unique constraint: (principal_id, platform, normalized_value)
- Preserves provenance across platforms

**Message**
- Communication records with full content
- Links to Thread and Channel
- Deduplication via composite hash

**PersonMessage**
- Many-to-many link between people and messages
- Tracks roles: sender, to, cc, bcc

### Storage

**Attachments**:
- Stored in `~/Memories/attachments/YYYY/MM/`
- APFS clones for instant, zero-space copying
- Organized by date for easy browsing

**Database**:
- PostgreSQL with pgvector for future embedding support
- All IDs are ULIDs for distributed-friendly generation
- Timestamps in TIMESTAMPTZ for consistency

## Current Limitations

- **Email/iMessage ingestion**: Has known architectural issues with shared identities (e.g., team emails). Use with caution.
- **Identity resolution**: Manual only (no automatic merging of duplicate people yet)
- **Embeddings**: Not yet implemented
- **Web interface**: CLI and MCP only

See `docs/IDENTITY_CONSTRAINT_ISSUE.md` for technical details on the ingestion limitations.

## Development

See [AGENTS.md](AGENTS.md) for development setup, architecture details, and contribution guidelines.

## Support

For issues and questions, please open an issue on GitHub or consult the documentation in `docs/`.
