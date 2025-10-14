"""
Unified normalization utilities for identity resolution across platforms.

This module provides consistent normalization functions for various identity types
(phone numbers, emails, names) to enable accurate deduplication and linking
across different data sources (contacts, iMessage, email, etc.).
"""

import re
import phonenumbers
from phonenumbers import geocoder, carrier
import structlog
from memory_database.utils.config import get_default_country

INVALID_MEMORY_URL_CHARS = {"<", ">", '"', "|", "?"}

logger = structlog.get_logger()

# Default country for parsing phone numbers without country codes
DEFAULT_COUNTRY = get_default_country()


def normalize_phone(phone: str, default_country: str = DEFAULT_COUNTRY) -> str:
    """
    Normalize phone number to E.164 format using Google's libphonenumber.
    
    E.164 format: +[country code][subscriber number]
    Example: +14155552671
    
    Args:
        phone: Raw phone number string
        default_country: ISO 3166-1 alpha-2 country code for parsing (default: US)
        
    Returns:
        Normalized phone number in E.164 format, or empty string if invalid
        
    Examples:
        >>> normalize_phone("(415) 555-2671")
        '+14155552671'
        >>> normalize_phone("415-555-2671", "US")
        '+14155552671'
        >>> normalize_phone("+44 20 7183 8750")
        '+442071838750'
        >>> normalize_phone("020 7183 8750", "GB")
        '+442071838750'
    """
    if not phone:
        return ""
    
    try:
        # Try parsing with country code first
        parsed = None
        
        # If it starts with +, try parsing as international
        if phone.startswith('+'):
            try:
                parsed = phonenumbers.parse(phone, None)
            except phonenumbers.NumberParseException:
                pass
        
        # If not parsed yet, try with default country
        if parsed is None:
            parsed = phonenumbers.parse(phone, default_country)
        
        # Validate the number
        if not phonenumbers.is_valid_number(parsed):
            # Try to see if it's valid for any region
            if not phonenumbers.is_possible_number(parsed):
                logger.debug("Invalid phone number", phone=phone)
                return ""
        
        # Format to E.164
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        
    except phonenumbers.NumberParseException as e:
        logger.debug("Failed to parse phone number", phone=phone, error=str(e))
        return ""
    except Exception as e:
        logger.warning("Unexpected error parsing phone", phone=phone, error=str(e))
        return ""


def normalize_phone_with_metadata(phone: str, default_country: str = DEFAULT_COUNTRY) -> dict:
    """
    Normalize phone number and extract metadata.
    
    Args:
        phone: Raw phone number string
        default_country: ISO 3166-1 alpha-2 country code for parsing
        
    Returns:
        Dictionary with normalized number and metadata
    """
    result = {
        'normalized': '',
        'valid': False,
        'country': None,
        'country_code': None,
        'national_number': None,
        'carrier': None,
        'region': None,
        'number_type': None
    }
    
    if not phone:
        return result
    
    try:
        # Parse the phone number
        parsed = None
        if phone.startswith('+'):
            try:
                parsed = phonenumbers.parse(phone, None)
            except phonenumbers.NumberParseException:
                pass
        
        if parsed is None:
            parsed = phonenumbers.parse(phone, default_country)
        
        # Check validity
        result['valid'] = phonenumbers.is_valid_number(parsed)
        
        # Get E.164 format
        result['normalized'] = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
        
        # Extract metadata
        result['country_code'] = parsed.country_code
        result['national_number'] = parsed.national_number
        
        # Get country/region
        result['country'] = phonenumbers.region_code_for_number(parsed)
        
        # Get location description
        result['region'] = geocoder.description_for_number(parsed, "en")
        
        # Get carrier info (may be empty)
        result['carrier'] = carrier.name_for_number(parsed, "en")
        
        # Get number type
        number_type = phonenumbers.number_type(parsed)
        type_names = {
            phonenumbers.PhoneNumberType.FIXED_LINE: "fixed_line",
            phonenumbers.PhoneNumberType.MOBILE: "mobile",
            phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
            phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
            phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium_rate",
            phonenumbers.PhoneNumberType.SHARED_COST: "shared_cost",
            phonenumbers.PhoneNumberType.VOIP: "voip",
            phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "personal",
            phonenumbers.PhoneNumberType.PAGER: "pager",
            phonenumbers.PhoneNumberType.UAN: "uan",
            phonenumbers.PhoneNumberType.VOICEMAIL: "voicemail",
            phonenumbers.PhoneNumberType.UNKNOWN: "unknown"
        }
        result['number_type'] = type_names.get(number_type, "unknown")
        
    except phonenumbers.NumberParseException as e:
        logger.debug("Failed to parse phone number", phone=phone, error=str(e))
    except Exception as e:
        logger.warning("Unexpected error parsing phone", phone=phone, error=str(e))
    
    return result


def normalize_email(email: str) -> str:
    """
    Normalize email address for consistent comparison.
    
    Args:
        email: Raw email address string
        
    Returns:
        Normalized email (lowercase, trimmed)
        
    Examples:
        >>> normalize_email("  John.Doe@EXAMPLE.com  ")
        'john.doe@example.com'
        >>> normalize_email("Name <john@example.com>")
        'john@example.com'
    """
    if not email:
        return ""
    
    # Convert to lowercase and strip whitespace
    normalized = email.lower().strip()
    
    # Remove any angle brackets (from formats like "Name <email@example.com>")
    if '<' in normalized and '>' in normalized:
        match = re.search(r'<([^>]+)>', normalized)
        if match:
            normalized = match.group(1).strip()
    
    # Validate basic email format
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', normalized):
        return ""
    
    return normalized


def normalize_name(name: str) -> str:
    """
    Normalize display name for consistent comparison.
    
    Args:
        name: Raw name string
        
    Returns:
        Normalized name (lowercase, single spaces, trimmed)
        
    Examples:
        >>> normalize_name("  John   DOE  ")
        'john doe'
        >>> normalize_name("李明")
        '李明'
    """
    if not name:
        return ""
    
    # Convert to lowercase
    normalized = name.lower()
    
    # Replace multiple spaces with single space
    normalized = re.sub(r'\s+', ' ', normalized)
    
    # Strip leading/trailing whitespace
    normalized = normalized.strip()
    
    return normalized


def normalize_memory_url(value: str) -> str:
    """Normalize memory permalink URLs from basic memory."""
    if not value:
        return ""

    cleaned = value.strip()
    if not cleaned:
        return ""

    # Ensure memory:// prefix
    if not cleaned.startswith("memory://"):
        cleaned = cleaned.lstrip('/')  # remove accidental leading slashes
        cleaned = f"memory://{cleaned}"

    path = cleaned.removeprefix("memory://")

    if not path:
        return ""

    # Reject malformed paths
    if "://" in path:
        return ""
    if "//" in path:
        return ""
    if re.search(r"\s", path):
        return ""
    if any(char in path for char in INVALID_MEMORY_URL_CHARS):
        return ""

    normalized_path = path.strip('/')
    if not normalized_path:
        return ""

    return f"memory://{normalized_path}"


def normalize_identity_value(value: str, kind: str, default_country: str = DEFAULT_COUNTRY) -> str:
    """
    Route normalization based on identity kind.
    
    Args:
        value: Raw identity value
        kind: Type of identity ('phone', 'email', 'display_name', 'username', etc.)
        default_country: Country code for phone parsing (default: US)
        
    Returns:
        Normalized value appropriate for the identity kind
    """
    if not value:
        return ""
    
    if kind == 'phone':
        return normalize_phone(value, default_country)
    elif kind == 'email':
        return normalize_email(value)
    elif kind in ['display_name', 'name', 'alias']:
        return normalize_name(value)
    elif kind == 'memory_url':
        return normalize_memory_url(value)
    elif kind in ['username', 'user_id', 'contact_id']:
        # For usernames and IDs, just lowercase and strip
        return value.lower().strip()
    else:
        # Default: lowercase and strip
        return value.lower().strip()


def extract_identity_kind(value: str) -> str:
    """
    Determine the kind of identity based on the value format.
    
    Args:
        value: Identity value to analyze
        
    Returns:
        Likely identity kind ('email', 'phone', 'username')
    """
    if not value:
        return 'unknown'
    
    # Detect memory URLs
    if value.startswith('memory://'):
        return 'memory_url'

    # Check for email format
    if '@' in value and '.' in value:
        if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', value.strip()):
            return 'email'
    
    # Check for phone format using phonenumbers
    # Try to parse as phone number
    try:
        # Try with and without default country
        if value.startswith('+'):
            parsed = phonenumbers.parse(value, None)
        else:
            parsed = phonenumbers.parse(value, DEFAULT_COUNTRY)
        
        if phonenumbers.is_possible_number(parsed):
            return 'phone'
    except:
        pass
    
    # Check for simple phone patterns as fallback
    digits_only = ''.join(c for c in value if c.isdigit())
    if len(digits_only) >= 7 and len(digits_only) <= 15:
        # Check if it starts with + or has mostly digits
        if value.startswith('+') or len(digits_only) / len(value.replace(' ', '')) > 0.7:
            return 'phone'
    
    # Default to username
    return 'username'


def is_valid_phone(phone: str, default_country: str = DEFAULT_COUNTRY) -> bool:
    """
    Check if a string is a valid phone number.
    
    Args:
        phone: String to check
        default_country: Country code for parsing
        
    Returns:
        True if valid phone number
    """
    if not phone:
        return False
    
    try:
        if phone.startswith('+'):
            parsed = phonenumbers.parse(phone, None)
        else:
            parsed = phonenumbers.parse(phone, default_country)
        
        return phonenumbers.is_valid_number(parsed)
    except:
        return False


def is_valid_email(email: str) -> bool:
    """
    Check if a string is a valid email address.
    
    Args:
        email: String to check
        
    Returns:
        True if valid email address
    """
    if not email:
        return False
    
    # Basic email regex pattern
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    normalized = normalize_email(email)
    
    return bool(re.match(pattern, normalized))


def format_phone_display(phone: str, format_type: str = "INTERNATIONAL") -> str:
    """
    Format a phone number for display.
    
    Args:
        phone: Phone number (ideally in E.164 format)
        format_type: Format type - "INTERNATIONAL", "NATIONAL", or "E164"
        
    Returns:
        Formatted phone for display
        
    Examples:
        >>> format_phone_display("+14155552671", "INTERNATIONAL")
        '+1 415-555-2671'
        >>> format_phone_display("+14155552671", "NATIONAL")
        '(415) 555-2671'
    """
    if not phone:
        return ""
    
    try:
        # Parse the phone number
        if phone.startswith('+'):
            parsed = phonenumbers.parse(phone, None)
        else:
            # Try to parse with default country
            parsed = phonenumbers.parse(phone, DEFAULT_COUNTRY)
        
        # Format based on requested type
        if format_type == "INTERNATIONAL":
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        elif format_type == "NATIONAL":
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
        elif format_type == "E164":
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        else:
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
            
    except Exception as e:
        logger.debug("Failed to format phone", phone=phone, error=str(e))
        return phone
