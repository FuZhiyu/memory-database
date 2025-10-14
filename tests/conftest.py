"""
Pytest configuration and shared fixtures.
"""
import pytest
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(autouse=True)
def reset_sqlalchemy_state():
    """Reset SQLAlchemy state between tests to avoid conflicts."""
    from sqlalchemy.orm import clear_mappers
    yield
    clear_mappers()


@pytest.fixture
def disable_logging():
    """Disable logging during tests for cleaner output."""
    import logging
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)