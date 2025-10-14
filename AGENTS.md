# AGENTS.md

**Development Guide for Memory Database**

This document is for AI coding agents (like Claude Code) and developers working on the Memory Database codebase. For user-facing documentation, see [README.md](README.md).

## Project Overview

Memory Database is a personal communications indexer and retrieval system that unifies email, iMessage, contacts, and other messaging platforms under a people-centric database model. It performs identity resolution to link communications across platforms to real-world individuals.

## Package Structure

Memory Database is a Python package in the unified Juju system:
- **Package Name**: `memory-database` (for pip/uv)
- **Import Name**: `memory_database` (in Python code)
- **Virtual Environment**: `~/.venv/memory-database` (for standalone development)
- **Master Environment**: `~/.venv/juju` (for integration with Juju system)

Directory structure:
```
memory-database/
├── pyproject.toml                    # Package configuration
├── .env.example / .env               # Environment configuration
├── .envrc.template / .envrc          # Direnv configuration
├── run_mcp_server.py                 # MCP server entry point
├── src/
│   └── memory_database/              # Main package
│       ├── __init__.py
│       ├── cli.py                    # CLI entry point
│       ├── database/                 # Database connection
│       │   └── connection.py
│       ├── models/                   # SQLAlchemy ORM models
│       │   ├── people.py             # Principal, IdentityClaim
│       │   ├── messages.py           # Message, Thread, Channel
│       │   ├── media.py              # MediaAsset, DocumentAsset
│       │   ├── chunks.py             # Chunk (for RAG)
│       │   └── attachments.py        # MessageAttachment
│       ├── ingestion/                # Data import pipelines
│       │   ├── base.py               # Abstract base classes
│       │   ├── contacts.py           # Google Contacts JSON
│       │   ├── email.py              # MBOX/EML ingestion
│       │   └── imessage.py           # iMessage with attachments
│       ├── mcp_server/               # MCP server implementation
│       │   ├── server.py             # FastMCP server & tools
│       │   ├── queries.py            # Database query functions
│       │   ├── write_tools.py        # Contact management tools
│       │   └── photos_tools.py       # Photos library integration
│       ├── storage/                  # File storage
│       │   └── attachment_manager.py # APFS clone storage
│       └── utils/                    # Utilities
│           ├── ulid.py               # ULID generation
│           ├── normalization.py      # Phone/email normalization
│           ├── identity_resolver.py  # Identity resolution logic
│           └── chinese.py            # Chinese name handling
├── imessage-bridge/                  # Rust extension for iMessage
│   ├── Cargo.toml
│   ├── pyproject.toml
│   └── src/lib.rs
├── migrations/                       # SQL schema migrations
│   ├── 001_initial_schema.sql
│   └── ...
├── tests/                            # Test suite
│   ├── conftest.py
│   ├── test_*.py
│   └── fixtures/
│       ├── test_imessage_sample.db   # Anonymized test database
│       └── extract_imessage_sample.py
├── docs/                             # Documentation
│   ├── IDENTITY_UNIQUENESS.md
│   └── IDENTITY_CONSTRAINT_ISSUE.md
└── scripts/                          # Automation scripts
    └── sync_imessages_cron.sh        # Cron job for iMessage sync
```

## Development Setup

### Standalone Module Development

```bash
# Set up isolated environment
cd $HOME/path/to/memory-database
export UV_PROJECT_ENVIRONMENT="$HOME/.venv/memory-database"

# Install all dependencies (including dev)
uv sync --dev

# Initialize database schema
uv run python -m memory_database.cli init-db

# Run migrations (for schema updates)
uv run python -m memory_database.cli migrate
```

### Integration with Master Juju Environment

```bash
# From the Juju root directory
cd $HOME/path/to/juju
export UV_PROJECT_ENVIRONMENT="$HOME/.venv/juju"

# Install memory-database in editable mode (done automatically via uv sync)
uv sync

# Verify installation
uv run python -c "import memory_database; print('✓ Import successful')"

# MCP server runs from master environment via LaunchAgent
# or use the juju CLI wrapper:
juju status
juju start --service memory-database
juju logs --service memory-database
```

**Important**: All commands should be prefixed with the appropriate `UV_PROJECT_ENVIRONMENT` setting:
- Standalone: `UV_PROJECT_ENVIRONMENT="$HOME/.venv/memory-database"`
- Juju system: `UV_PROJECT_ENVIRONMENT="$HOME/.venv/juju"`

### Direnv Setup (Optional but Recommended)

```bash
# Install direnv
# macOS: brew install direnv
# Linux: see https://direnv.net/docs/installation.html

# Copy template
cp .envrc.template .envrc

# Edit .envrc with your paths
# Then allow it
direnv allow
```

## Development Commands

### Data Ingestion

```bash
# Import Google Contacts
uv run python -m memory_database.cli import-contacts --contacts-path Sources/Contacts/contacts.json

# Ingest email (MBOX/EML)
uv run python -m memory_database.cli ingest --email-path /path/to/emails.mbox

# Sync iMessages (incremental)
uv run python -m memory_database.cli sync-imessages

# Manual iMessage import (advanced)
uv run python -m memory_database.cli import-imessages --all-contacts --last-sync 0
```

### MCP Server

```bash
# HTTP server (recommended for development)
uv run python run_mcp_server.py --transport http --port 8766

# Stdio (for Claude Desktop local mode)
uv run python run_mcp_server.py --transport stdio
```

### Testing

```bash
# Run full test suite
uv run pytest

# Run with coverage
uv run pytest --cov=src/memory_database --cov-report=html

# Test specific module
uv run pytest tests/test_imessage_real_sample.py -v

# Test MCP server functionality
uv run python test_mcp.py

# Comprehensive MCP tests with real data
uv run python test_mcp_real_data.py
```

### Code Quality

```bash
# Format code
uv run black src/ tests/

# Sort imports
uv run isort src/ tests/

# Type checking
uv run mypy src/

# Lint
uv run ruff check src/ tests/
```

### Rust Extension (iMessage Bridge)

```bash
# Build the extension (auto-built by CLI if missing)
cd imessage-bridge
UV_PROJECT_ENVIRONMENT="$HOME/.venv/juju" uv run maturin develop

# Or for standalone development
UV_PROJECT_ENVIRONMENT="$HOME/.venv/memory-database" uv run maturin develop

# Build release version
maturin build --release
```

## Architecture & Key Concepts

### People-Centric Data Model

All communications are linked to canonical `Principal` records representing real people. The system uses `IdentityClaim` records to map platform-specific identifiers (email, phone, username) to people.

**Core Entities**:

1. **Principal**: Canonical person record
   - `id` (ULID): Unique identifier
   - `display_name`: Primary name
   - `org`: Organization/company
   - `metadata`: Additional structured data

2. **IdentityClaim**: Platform-specific identifier
   - `id` (ULID): Unique identifier
   - `principal_id`: Link to person
   - `platform`: Source (contacts, imessage, email, manual, photos)
   - `kind`: Type (email, phone, display_name, username, contact_id, alias, person_uuid)
   - `value`: Original value
   - `normalized`: Normalized value for matching
   - `confidence`: 0.0-1.0 score
   - **Unique constraint**: `(principal_id, platform, normalized)`

3. **Message**: Communication record
   - `id` (ULID): Unique identifier
   - `thread_id`: Conversation thread
   - `content`: Message body
   - `timestamp`: When sent
   - `platform`: Source platform
   - Linked to people via `PersonMessage`

4. **PersonMessage**: Many-to-many link
   - Links people to messages with roles: sender, to, cc, bcc

### Database Conventions

- **Primary keys**: ULIDs for distributed scalability
- **Timestamps**: TIMESTAMPTZ for consistency
- **Foreign keys**: Pattern `{referenced_table}_id`
- **Many-to-many**: Pattern `{entity1}_{entity2}`
- **Normalization**: Emails lowercased, phones in E.164 format

### Code Patterns

- **Async/await**: For database operations where beneficial
- **Pydantic**: Settings and validation (DatabaseSettings)
- **Structlog**: Structured logging throughout
- **Type hints**: Full type coverage, validated with mypy
- **Abstract base classes**: Extensible ingestion pipeline

## Important Implementation Details

### Identity Uniqueness Constraint

**⚠️ CRITICAL**: The database enforces a unique constraint on identity claims:

```sql
UNIQUE (principal_id, platform, normalized)
```

**What this means**:
- Each person can have ONE identity claim per `(platform, normalized_value)` combination
- Same email from DIFFERENT platforms = separate claims (preserves provenance)
- Same email from SAME platform twice = DATABASE ERROR

**Examples**:
- ✅ `{platform='contacts', normalized='john@ex.com'}` + `{platform='imessage', normalized='john@ex.com'}` (different platforms)
- ✅ `{platform='contacts', normalized='john@work.com'}` + `{platform='contacts', normalized='john@personal.com'}` (different emails)
- ❌ `{platform='contacts', normalized='john@ex.com'}` twice (DUPLICATE ERROR)

**Why platform-level granularity**:
- Preserves full provenance: tracks where each identity was observed
- Richer evidence for identity resolution: "seen across 3 platforms" vs "seen once"
- Clear data lifecycle: each platform owns its claims independently
- Better confidence modeling: platform-specific confidence scores

**For developers**:
- 📖 **READ FIRST**: `docs/IDENTITY_UNIQUENESS.md` for complete implementation guide
- ALWAYS check for `(principal_id, platform, normalized)` duplicates before INSERT
- Use MCP write functions in `src/mcp_server/write_tools.py` (they handle this)
- For updates, ensure final state doesn't collide with existing claims

### Identity Resolution

The system normalizes identities before storage:

```python
# Phone numbers: E.164 format via Google's libphonenumber
from memory_database.utils.normalization import normalize_phone
phone = normalize_phone("+1 (555) 123-4567")  # → "+15551234567"

# Emails: Lowercase, whitespace trimmed
from memory_database.utils.normalization import normalize_email
email = normalize_email("John.Doe@EXAMPLE.com")  # → "john.doe@example.com"

# Chinese names: Automatic alias generation
# Input: 郑天行 → Creates aliases:
#   1. Chinese order (no space): 郑天行
#   2. English pinyin: Tianxing Zheng
```

Platform-specific normalization is in `src/utils/normalization.py`.

### Message Deduplication

Messages are deduplicated using composite hashes:
- Platform + thread + timestamp + sender + content hash
- Prevents duplicate imports during incremental updates
- Hash stored in `dedup_key` column

### Attachment Storage

**Location**: `~/Memories/attachments/YYYY/MM/`

**Storage method** (tried in order):
1. **APFS clone** (preferred): Instant copy, zero disk space
2. **Hard link** (fallback): Same file, multiple references
3. **Regular copy** (last resort): Only if filesystem doesn't support above

**Process**:
```python
from memory_database.storage.attachment_manager import AttachmentManager

manager = AttachmentManager()
stored_path = manager.store_attachment(
    source_path="/path/to/original.jpg",
    message_id="01ABC123...",
    filename="photo.jpg"
)
# Returns: ~/Memories/attachments/2024/10/01ABC123_photo.jpg
```

## Environment Configuration

### Required Variables

```bash
# PostgreSQL connection
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=memories_rag
POSTGRES_USER=your_username
POSTGRES_PASSWORD=your_password
LOG_LEVEL=INFO
```

### Optional Variables

```bash
# MCP HTTP server authentication
MEMORY_DB_HTTP_TOKEN=your_secret_token
MEMORY_DB_HTTP_RESOURCE_URL=http://localhost:8766

# Performance tuning
MEMORY_DB_IMESSAGE_BATCH_SIZE=500  # Messages per batch
MEMORY_DB_ATTACHMENTS_ROOT=~/Memories/attachments  # Override default
```

## Testing

### Test Structure

```
tests/
├── conftest.py                      # Pytest fixtures
├── test_imessage_real_sample.py    # iMessage ingestion tests
├── test_imessage_security.py       # Security & path validation
├── test_photos_tools.py            # Photos MCP tools
├── test_mcp_search.py              # MCP search functionality
├── test_mcp_core_functionality.py  # Core MCP tools
├── fixtures/
│   ├── test_imessage_sample.db     # Anonymized test database
│   └── extract_imessage_sample.py  # Script to generate test DB
└── mocks/
    └── mock_attachment_manager.py  # Mock for file operations
```

### Running Tests

```bash
# Full suite
uv run pytest

# With output
uv run pytest -v -s

# Specific test
uv run pytest tests/test_mcp_search.py::test_search_person -v

# Skip slow tests
uv run pytest -m "not slow"

# Generate coverage report
uv run pytest --cov=src/memory_database --cov-report=html
open htmlcov/index.html
```

### Test Database

The test fixture `tests/fixtures/test_imessage_sample.db` is an anonymized iMessage database:
- All message text replaced with generic placeholders
- Emails: `userN@example.com`
- Phones: `+1555000NNNN`
- No personal information

Regenerate with:
```bash
uv run python tests/fixtures/extract_imessage_sample.py --limit 50
```

## Known Issues & Limitations

### 🚨 Message Ingestion Architecture Issue

**Status**: Known bug, workaround available

**Problem**: The base ingestion pipeline assumes one identity = one person globally, but the database constraint allows shared identities. This causes incorrect message attribution for:
- Team emails (support@company.com)
- Shared phone numbers
- Generic usernames

**Workaround**: Use `--all-contacts` flag cautiously, or manually verify identity resolution.

**Fix**: Requires identity resolution engine (in progress).

**Details**: See `docs/IDENTITY_CONSTRAINT_ISSUE.md`

### Photos Integration

**macOS only**: Uses `osxphotos` library
**Requires**: Full Disk Access permission
**Limitation**: iCloud photos must be downloaded locally for full metadata

### Performance

**Large databases**: Query performance degrades with >100k messages
**Recommendation**: Add indexes for frequently queried columns
**Future**: Implement chunking and vector embeddings for better search

## MCP Server Details

### FastMCP Implementation

The MCP server uses FastMCP (not official MCP spec):
- Session-based protocol
- HTTP and stdio transports
- Bearer token authentication for HTTP

### Authentication

```python
# Optional token-based auth for HTTP
HTTP_AUTH_TOKEN = os.getenv("MEMORY_DB_HTTP_TOKEN")

# Requests must include:
# Authorization: Bearer <token>
```

### Tool Categories

1. **Contact search** (`search_person`)
2. **Message search** (`search_messages`)
3. **Contact management** (`create_new_contact`, `add_identity_to_contact`, etc.)
4. **Photos** (`photos_search`, `view_photos`, `photos_export`, `photos_link_person`)

### Error Handling

All MCP tools return structured responses:
```python
{
    "success": True,
    "data": {...},
    # OR
    "error": "Error message"
}
```

## Coding Conventions

### Imports

```python
# Standard library
import os
from pathlib import Path
from typing import Optional, List, Dict

# Third-party
import structlog
from sqlalchemy import select
from pydantic import BaseModel

# Local
from memory_database.models import Principal, IdentityClaim
from memory_database.utils.normalization import normalize_email
```

### Logging

```python
import structlog

logger = structlog.get_logger()

# Structured logging
logger.info(
    "Message imported",
    message_id=msg.id,
    platform="imessage",
    attachments=len(attachments)
)

# Errors with context
logger.error(
    "Failed to normalize phone",
    phone=raw_phone,
    error=str(e)
)
```

### Type Hints

```python
from typing import Optional, List, Dict, Any

def search_people(
    session: Session,
    *,
    email: Optional[str] = None,
    name: Optional[str] = None,
    fuzzy_match: bool = False,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Search for people by identity."""
    ...
```

### Error Handling

```python
try:
    result = risky_operation()
except SpecificError as e:
    logger.error("Operation failed", error=str(e), context=vars(e))
    raise
except Exception as e:
    logger.exception("Unexpected error", operation="risky_operation")
    # Re-raise or handle appropriately
    raise
```

## Contributing

### Before Submitting

1. **Run tests**: `uv run pytest`
2. **Format code**: `uv run black src/ tests/`
3. **Sort imports**: `uv run isort src/ tests/`
4. **Type check**: `uv run mypy src/`
5. **Update docs**: If adding features, update README.md

### Commit Messages

```
feat: add support for Signal ingestion
fix: resolve duplicate identity claims on contacts import
docs: update MCP tools documentation
refactor: extract identity resolution to separate module
test: add tests for Chinese name alias generation
```

### Pull Request Process

1. Create feature branch from `main`
2. Make changes with tests
3. Update documentation
4. Submit PR with clear description
5. Address review feedback

## Resources

- **Main docs**: [README.md](README.md)
- **Identity details**: [docs/IDENTITY_UNIQUENESS.md](docs/IDENTITY_UNIQUENESS.md)
- **Known issues**: [docs/IDENTITY_CONSTRAINT_ISSUE.md](docs/IDENTITY_CONSTRAINT_ISSUE.md)
- **FastMCP**: https://github.com/jlowin/fastmcp
- **MCP spec**: https://modelcontextprotocol.io/
- **osxphotos**: https://github.com/RhetTbull/osxphotos
