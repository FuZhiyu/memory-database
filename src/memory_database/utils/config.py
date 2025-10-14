"""
Configuration utilities for the normalization system.
"""

import os
from typing import Optional

# Environment variable for default country
DEFAULT_COUNTRY_ENV = "MEMORIES_DEFAULT_COUNTRY"

# Get default country from environment or use US
DEFAULT_COUNTRY = os.environ.get(DEFAULT_COUNTRY_ENV, "US")


def get_default_country() -> str:
    """
    Get the default country code for phone number parsing.
    
    Can be set via MEMORIES_DEFAULT_COUNTRY environment variable.
    
    Returns:
        ISO 3166-1 alpha-2 country code (e.g., 'US', 'GB', 'CN')
    """
    return DEFAULT_COUNTRY


def set_default_country(country_code: str):
    """
    Set the default country code for the current session.
    
    Args:
        country_code: ISO 3166-1 alpha-2 country code
    """
    global DEFAULT_COUNTRY
    DEFAULT_COUNTRY = country_code.upper()