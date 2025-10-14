# Testing Strategy for Messages RAG System

## Overview

All tests use **real anonymized data** extracted from actual iMessage databases. This provides the most realistic testing scenarios while protecting privacy.

## Test Database Sample

The test suite uses `tests/fixtures/test_imessage_sample.db` - an anonymized extract from a real iMessage database containing:
- 200 messages
- 11 handles (contacts)
- 57 attachments
- Real message threading and relationships

### Creating/Updating the Sample

To extract a fresh sample from your iMessage database:

```bash
uv run python tests/fixtures/extract_imessage_sample.py --limit 200
```

This will:
1. Extract 200 recent messages from `~/Library/Messages/chat.db`
2. Anonymize all personal data:
   - Phone numbers → `+1555000XXXX`
   - Emails → `userX@example.com`
   - URLs → `https://example.com`
   - Message content patterns are sanitized
3. Preserve database structure and relationships
4. Save to `tests/fixtures/test_imessage_sample.db`

## Running Tests

### Prerequisites

1. **Test Database**: Create a PostgreSQL test database
   ```bash
   psql -c "CREATE DATABASE test_memories_rag;" postgres
   ```

2. **Build Rust Extension** (optional, for some tests):
   ```bash
   cd imessage-bridge && maturin develop
   ```

### Run All Tests
```bash
uv run pytest tests/
```

### Run Specific Test Categories
```bash
# Tests that only need the sample database (no PostgreSQL)
uv run pytest tests/ -k "integrity or anonymization"

# Full pipeline tests (requires PostgreSQL)
uv run pytest tests/ -k "import_pipeline"

# Tests requiring Rust bridge
uv run pytest tests/ -k "imessage_db_connection"
```

### Run with Coverage
```bash
uv run pytest tests/ --cov=src --cov-report=html
# Open htmlcov/index.html to view coverage report
```

## Test Structure

### `test_imessage_real_sample.py`

Main test suite using real anonymized data:

1. **Sample Integrity Tests**
   - `test_sample_database_integrity`: Verifies sample has expected structure
   - `test_anonymization_quality`: Ensures no PII in sample

2. **Connection Tests**
   - `test_imessage_db_connection`: Tests Rust bridge connection (optional)

3. **Pipeline Tests** (require PostgreSQL)
   - `test_full_import_pipeline`: Complete import from sample
   - `test_incremental_import_deduplication`: Verifies no duplicates
   - `test_known_contacts_filtering`: Tests contact filtering
   - `test_attachment_processing`: Attachment handling
   - `test_threading_and_channels`: Message organization

### Mock Components

- **`MockAttachmentManager`**: Simulates file storage without filesystem access
- Returns realistic metadata without actual file operations
- Tracks all operations for verification

## Benefits of Real Sample Testing

1. **Realistic Data**: Tests use actual message patterns, not synthetic data
2. **Complex Relationships**: Real threading, attachments, and participants
3. **Edge Cases**: Captures real-world edge cases automatically
4. **Performance**: Sample is small (712KB) for fast tests
5. **Privacy**: All personal data is anonymized
6. **Reproducible**: Same sample used across all test runs

## Continuous Integration

For CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Setup PostgreSQL
  run: |
    psql -c "CREATE DATABASE test_memories_rag;"
    
- name: Run Tests
  run: |
    uv sync --dev
    uv run pytest tests/ --cov=src
```

## Troubleshooting

### "Sample database not found"
Run: `uv run python tests/fixtures/extract_imessage_sample.py`

### "Test database does not exist"
Run: `psql -c "CREATE DATABASE test_memories_rag;" postgres`

### "iMessage bridge not built"
Optional - only needed for Rust bridge tests:
```bash
cd imessage-bridge && maturin develop
```

### Tests are slow
The sample size can be adjusted:
```bash
# Smaller sample for faster tests
uv run python tests/fixtures/extract_imessage_sample.py --limit 50
```

## Adding New Tests

When adding new ingestion sources:

1. Create an extraction script similar to `extract_imessage_sample.py`
2. Ensure proper anonymization of all PII
3. Use the same test patterns as `test_imessage_real_sample.py`
4. Test with real data, not synthetic

## Security Notes

- **NEVER** commit non-anonymized samples
- The extraction script automatically anonymizes data
- Review samples before committing to ensure no PII
- Use `.gitignore` to prevent accidental commits of real databases