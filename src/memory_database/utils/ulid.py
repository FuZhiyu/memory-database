from ulid import ULID


def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(ULID())


def is_valid_ulid(ulid_str: str) -> bool:
    """Check if a string is a valid ULID."""
    try:
        ULID.from_str(ulid_str)
        return True
    except ValueError:
        return False