# Test Organization

This directory contains all tests for the Messages RAG System.

## Structure

```
tests/
├── unit/                      # Unit tests for individual components
│   ├── ingestion/            # Tests for ingestion modules
│   │   ├── test_imessage.py
│   │   ├── test_contacts.py
│   │   └── test_base.py
│   ├── models/               # Tests for database models
│   │   ├── test_people.py
│   │   └── test_messages.py
│   └── utils/                # Tests for utility functions
│       ├── test_normalization.py
│       └── test_identity_resolver.py
├── integration/              # Integration tests
│   ├── test_imessage_pipeline.py
│   ├── test_contacts_pipeline.py
│   └── test_identity_resolution.py
├── fixtures/                 # Test data and database samples
│   ├── test_imessage_sample.db
│   ├── extract_imessage_sample.py
│   └── create_test_imessage_db.py
├── mocks/                    # Mock objects for testing
│   └── mock_attachment_manager.py
└── conftest.py              # Shared pytest configuration and fixtures
```

## Running Tests

```bash
# Run all tests
uv run pytest

# Run specific test category
uv run pytest tests/unit/
uv run pytest tests/integration/

# Run tests for specific module
uv run pytest tests/unit/ingestion/

# Run with coverage
uv run pytest --cov=src --cov-report=html

# Run with verbose output
uv run pytest -v

# Run specific test
uv run pytest tests/unit/ingestion/test_imessage.py::TestiMessageNormalization
```

## Test Categories

### Unit Tests (`tests/unit/`)
- Test individual functions and classes in isolation
- Use mocks and stubs for dependencies
- Fast execution
- No database or filesystem access

### Integration Tests (`tests/integration/`)
- Test complete workflows and pipelines
- Use test databases (in-memory SQLite)
- May use real sample data from fixtures
- Test component interactions

### Fixtures (`tests/fixtures/`)
- Sample databases extracted from real data (anonymized)
- Test data generators
- Shared test resources

### Mocks (`tests/mocks/`)
- Mock implementations of external dependencies
- Avoid filesystem and network operations
- Provide predictable behavior for testing

## Best Practices

1. **Isolation**: Each test should be independent and not affect others
2. **Speed**: Unit tests should run in milliseconds, integration tests in seconds
3. **Clarity**: Test names should clearly describe what they test
4. **Coverage**: Aim for >80% code coverage, focusing on critical paths
5. **Fixtures**: Use pytest fixtures for common setup/teardown
6. **Mocking**: Mock external dependencies (filesystem, network, databases)
7. **Sample Data**: Use anonymized real data for realistic testing

## Creating New Tests

When adding new functionality:
1. Write unit tests first (TDD approach)
2. Add integration tests for workflows
3. Update or create fixtures as needed
4. Ensure tests are added to appropriate category

## Test Data Safety

- **NEVER** use real personal data in tests
- Sample databases are anonymized using `extract_imessage_sample.py`
- Phone numbers are replaced with `+1555000XXXX` format
- Emails become `userX@example.com`
- All personal information is sanitized