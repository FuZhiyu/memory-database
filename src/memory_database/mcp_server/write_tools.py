"""
Safe write tools for MCP server - Contact management only.

These tools allow safe modification of contact information while preserving
data integrity through strict validation and normalization.

CRITICAL: Database Uniqueness Constraint
=========================================
The database enforces: UNIQUE (principal_id, platform, normalized)

This means:
- Each person can have ONE identity claim per (platform, normalized_value)
- Before creating/updating claims, MUST check for (principal_id, platform, normalized) duplicates
- The database will reject duplicate insertions with IntegrityError

All write functions in this module enforce this constraint at the application level
to provide better error messages before hitting the database.
"""

from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from enum import Enum

import structlog
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from memory_database.models import Principal, IdentityClaim
from memory_database.utils.normalization import (
    normalize_identity_value,
    is_valid_email,
    is_valid_phone,
    extract_identity_kind
)

logger = structlog.get_logger()

# Strict validation constants
ALLOWED_IDENTITY_KINDS = {
    'email',
    'phone', 
    'display_name',
    'username',
    'contact_id',
    'alias',  # Alternative names, nicknames, variants
    'memory_url',  # Permalink to basic memory note for this contact
    'person_uuid',  # Photos People person UUID
}

ALLOWED_PLATFORMS = {
    'contacts',
    'imessage', 
    'email',
    'manual',  # For MCP-created entries
    'life.md',  # Generated from basic memory note tooling
    'photos',   # macOS Photos platform for People/face links
}

# Maximum values for safety
MAX_DISPLAY_NAME_LENGTH = 200
MAX_IDENTITY_VALUE_LENGTH = 500
MIN_CONFIDENCE = 0.0
MAX_CONFIDENCE = 1.0


class ValidationError(Exception):
    """Raised when validation fails."""
    pass


def validate_identity_kind(kind: str) -> str:
    """Validate and normalize identity kind."""
    if not kind or not isinstance(kind, str):
        raise ValidationError("Identity kind must be a non-empty string")
    
    kind_lower = kind.lower().strip()
    if kind_lower not in ALLOWED_IDENTITY_KINDS:
        raise ValidationError(
            f"Invalid identity kind '{kind}'. Must be one of: {', '.join(sorted(ALLOWED_IDENTITY_KINDS))}"
        )
    
    return kind_lower


def validate_platform(platform: str) -> str:
    """Validate and normalize platform."""
    if not platform or not isinstance(platform, str):
        raise ValidationError("Platform must be a non-empty string")
    
    platform_lower = platform.lower().strip()
    if platform_lower not in ALLOWED_PLATFORMS:
        raise ValidationError(
            f"Invalid platform '{platform}'. Must be one of: {', '.join(sorted(ALLOWED_PLATFORMS))}"
        )
    
    return platform_lower


def validate_alias_value(value: str) -> str:
    """Validate alias with enhanced security rules."""
    import re
    
    if not value or not isinstance(value, str):
        raise ValidationError("Alias must be a non-empty string")
    
    # Remove potentially dangerous characters for security
    dangerous_chars = r'[<>\'\"&;\\]'
    if re.search(dangerous_chars, value):
        raise ValidationError("Alias contains prohibited characters: < > ' \" & ; \\")
    
    # Strict length limit for aliases
    if len(value.strip()) > 100:
        raise ValidationError("Alias too long (max 100 characters)")
    
    # Remove excessive whitespace and normalize
    cleaned = re.sub(r'\s+', ' ', value.strip())
    if not cleaned:
        raise ValidationError("Alias cannot be empty after cleaning")
    
    # Additional security: prevent control characters
    if any(ord(c) < 32 for c in cleaned):
        raise ValidationError("Alias contains control characters")
    
    return cleaned.lower()


def validate_identity_value(value: str, kind: str) -> str:
    """Validate and normalize identity value based on kind."""
    if not value or not isinstance(value, str):
        raise ValidationError("Identity value must be a non-empty string")
    
    if len(value) > MAX_IDENTITY_VALUE_LENGTH:
        raise ValidationError(f"Identity value too long (max {MAX_IDENTITY_VALUE_LENGTH} chars)")
    
    # Enhanced validation for alias kind
    if kind == 'alias':
        return validate_alias_value(value)
    if kind == 'memory_url':
        normalized = normalize_identity_value(value, kind)
        if not normalized:
            raise ValidationError(f"Invalid memory_url format: '{value}'")
        return normalized

    # Normalize the value
    normalized = normalize_identity_value(value, kind)
    if not normalized:
        raise ValidationError(f"Invalid {kind} format: '{value}'")
    
    # Additional validation for specific kinds
    if kind == 'email' and not is_valid_email(normalized):
        raise ValidationError(f"Invalid email format: '{value}'")
    
    if kind == 'phone' and not is_valid_phone(normalized):
        raise ValidationError(f"Invalid phone number format: '{value}'")
    
    return normalized


def validate_confidence(confidence: float) -> float:
    """Validate confidence score."""
    if not isinstance(confidence, (int, float)):
        raise ValidationError("Confidence must be a number")
    
    if not (MIN_CONFIDENCE <= confidence <= MAX_CONFIDENCE):
        raise ValidationError(f"Confidence must be between {MIN_CONFIDENCE} and {MAX_CONFIDENCE}")
    
    return float(confidence)


def validate_display_name(name: str) -> str:
    """Validate display name."""
    if not name or not isinstance(name, str):
        raise ValidationError("Display name must be a non-empty string")
    
    name = name.strip()
    if len(name) > MAX_DISPLAY_NAME_LENGTH:
        raise ValidationError(f"Display name too long (max {MAX_DISPLAY_NAME_LENGTH} chars)")
    
    if not name:
        raise ValidationError("Display name cannot be empty")
    
    return name


def create_contact(
    session: Session,
    display_name: str,
    identities: List[Dict[str, Any]] = None,
    org: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new contact with optional identity claims.
    
    Args:
        session: Database session
        display_name: Display name for the contact
        identities: List of identity dicts with 'kind', 'value', 'platform', 'confidence'
        org: Optional organization
        
    Returns:
        Dictionary with contact information and success status
    """
    try:
        # Validate display name
        display_name = validate_display_name(display_name)
        
        # Create principal
        principal = Principal(
            display_name=display_name,
            org=org.strip() if org else None,
            created_at=datetime.now(timezone.utc)
        )
        
        session.add(principal)
        session.flush()  # Get the ID
        
        # Add identity claims
        created_identities = []
        if identities:
            for identity in identities:
                try:
                    kind = validate_identity_kind(identity.get('kind', ''))
                    platform = validate_platform(identity.get('platform', 'manual'))
                    value = identity.get('value', '')
                    confidence = validate_confidence(identity.get('confidence', 0.9))
                    
                    normalized_value = validate_identity_value(value, kind)
                    
                    # Check for duplicates (per platform)
                    existing = session.query(IdentityClaim).filter_by(
                        principal_id=principal.id,
                        platform=platform,
                        normalized=normalized_value
                    ).first()

                    if existing:
                        logger.warning("Duplicate identity claim skipped",
                                     platform=platform, kind=kind, value=normalized_value)
                        continue
                    
                    claim = IdentityClaim(
                        principal_id=principal.id,
                        platform=platform,
                        kind=kind,
                        value=value,
                        normalized=normalized_value,
                        confidence=confidence,
                        first_seen=datetime.now(timezone.utc),
                        last_seen=datetime.now(timezone.utc)
                    )
                    
                    session.add(claim)
                    created_identities.append({
                        'kind': kind,
                        'value': value,
                        'normalized': normalized_value,
                        'platform': platform,
                        'confidence': confidence
                    })
                    
                except ValidationError as e:
                    logger.warning("Skipping invalid identity", error=str(e), identity=identity)
                    continue
        
        session.commit()
        
        logger.info("Contact created", 
                   contact_id=principal.id, 
                   display_name=display_name,
                   identities_count=len(created_identities))
        
        return {
            'success': True,
            'contact': {
                'id': principal.id,
                'display_name': principal.display_name,
                'org': principal.org,
                'created_at': principal.created_at.isoformat(),
                'identities': created_identities
            }
        }
        
    except ValidationError as e:
        session.rollback()
        return {'success': False, 'error': f"Validation error: {str(e)}"}
    
    except IntegrityError as e:
        session.rollback()
        return {'success': False, 'error': "Contact with this information may already exist"}
    
    except Exception as e:
        session.rollback()
        logger.error("Unexpected error creating contact", error=str(e))
        return {'success': False, 'error': f"Failed to create contact: {str(e)}"}


def add_contact_identity(
    session: Session,
    person_id: str,
    kind: str,
    value: str,
    platform: str = 'manual',
    confidence: float = 0.9
) -> Dict[str, Any]:
    """
    Add a new identity claim to an existing contact.
    
    Args:
        session: Database session
        person_id: ID of the contact
        kind: Type of identity ('email', 'phone', etc.)
        value: Identity value
        platform: Platform source (default: 'manual')
        confidence: Confidence score (0.0-1.0)
        
    Returns:
        Dictionary with success status and identity information
    """
    try:
        # Validate inputs
        kind = validate_identity_kind(kind)
        platform = validate_platform(platform)
        confidence = validate_confidence(confidence)
        normalized_value = validate_identity_value(value, kind)
        
        # Check if person exists
        person = session.query(Principal).filter_by(id=person_id).first()
        if not person:
            return {'success': False, 'error': f"Contact not found: {person_id}"}
        
        # Check for duplicate (per platform)
        existing = session.query(IdentityClaim).filter_by(
            principal_id=person_id,
            platform=platform,
            normalized=normalized_value
        ).first()

        if existing:
            return {
                'success': False,
                'error': f"Identity already exists on {platform}: {kind} = {normalized_value}"
            }
        
        # Create new identity claim
        claim = IdentityClaim(
            principal_id=person_id,
            platform=platform,
            kind=kind,
            value=value,
            normalized=normalized_value,
            confidence=confidence,
            first_seen=datetime.now(timezone.utc),
            last_seen=datetime.now(timezone.utc)
        )
        
        session.add(claim)
        session.commit()
        
        logger.info("Identity added to contact", 
                   contact_id=person_id,
                   kind=kind,
                   normalized_value=normalized_value)
        
        return {
            'success': True,
            'identity': {
                'id': claim.id,
                'kind': kind,
                'value': value,
                'normalized': normalized_value,
                'platform': platform,
                'confidence': confidence,
                'created_at': claim.first_seen.isoformat()
            }
        }
        
    except ValidationError as e:
        return {'success': False, 'error': f"Validation error: {str(e)}"}
    
    except IntegrityError as e:
        session.rollback()
        return {'success': False, 'error': "Identity constraint violation"}
    
    except Exception as e:
        session.rollback()
        logger.error("Unexpected error adding identity", error=str(e))
        return {'success': False, 'error': f"Failed to add identity: {str(e)}"}


def update_contact_name(
    session: Session,
    person_id: str,
    new_name: str
) -> Dict[str, Any]:
    """
    Update a contact's display name.
    
    Args:
        session: Database session
        person_id: ID of the contact
        new_name: New display name
        
    Returns:
        Dictionary with success status
    """
    try:
        # Validate new name
        new_name = validate_display_name(new_name)
        
        # Find the contact
        person = session.query(Principal).filter_by(id=person_id).first()
        if not person:
            return {'success': False, 'error': f"Contact not found: {person_id}"}
        
        old_name = person.display_name
        person.display_name = new_name
        session.commit()
        
        logger.info("Contact name updated", 
                   contact_id=person_id,
                   old_name=old_name,
                   new_name=new_name)
        
        return {
            'success': True,
            'contact': {
                'id': person_id,
                'old_name': old_name,
                'new_name': new_name,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
        }
        
    except ValidationError as e:
        return {'success': False, 'error': f"Validation error: {str(e)}"}
    
    except Exception as e:
        session.rollback()
        logger.error("Unexpected error updating contact name", error=str(e))
        return {'success': False, 'error': f"Failed to update name: {str(e)}"}


def update_contact_identity(
    session: Session,
    person_id: str,
    identity_id: str,
    new_value: Optional[str] = None,
    new_confidence: Optional[float] = None,
    new_platform: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update an existing identity claim with enhanced transaction safety.
    
    Args:
        session: Database session
        person_id: ID of the contact
        identity_id: ID of the identity claim to update
        new_value: New identity value (will be normalized)
        new_confidence: New confidence score (0.0-1.0)
        new_platform: New platform source
        
    Returns:
        Dictionary with success status and updated identity information
    """
    # Validate at least one field is provided
    if all(x is None for x in [new_value, new_confidence, new_platform]):
        return {'success': False, 'error': "At least one field must be provided for update"}
    
    try:
        # Start explicit transaction for complex update
        savepoint = session.begin_nested()
        
        try:
            # Find the identity claim with SELECT FOR UPDATE to prevent race conditions
            claim = session.query(IdentityClaim).filter_by(
                id=identity_id,
                principal_id=person_id
            ).with_for_update().first()
            
            if not claim:
                return {'success': False, 'error': "Identity claim not found"}
            
            # Store original values for logging and rollback capability
            original_info = {
                'kind': claim.kind,
                'value': claim.value,
                'normalized': claim.normalized,
                'platform': claim.platform,
                'confidence': claim.confidence
            }
            
            # Prepare all changes first, validate everything before applying
            changes = {}
            
            # Validate new value if provided
            if new_value is not None:
                normalized_value = validate_identity_value(new_value, claim.kind)
                changes['value'] = new_value
                changes['normalized'] = normalized_value

            # Validate confidence if provided
            if new_confidence is not None:
                changes['confidence'] = validate_confidence(new_confidence)

            # Validate platform if provided
            if new_platform is not None:
                changes['platform'] = validate_platform(new_platform)

            # Check for duplicates based on final state (platform, normalized)
            # Only if either platform or normalized value is changing
            if 'platform' in changes or 'normalized' in changes:
                final_platform = changes.get('platform', claim.platform)
                final_normalized = changes.get('normalized', claim.normalized)

                # Only check if the combination is actually changing
                if final_platform != claim.platform or final_normalized != claim.normalized:
                    existing = session.query(IdentityClaim).filter_by(
                        principal_id=person_id,
                        platform=final_platform,
                        normalized=final_normalized
                    ).filter(IdentityClaim.id != identity_id).first()

                    if existing:
                        return {
                            'success': False,
                            'error': f"Update would create duplicate on {final_platform}: {claim.kind} = {final_normalized}"
                        }
            
            # Apply all changes atomically
            for field, value in changes.items():
                setattr(claim, field, value)

            # Update timestamp
            claim.last_seen = datetime.now(timezone.utc)

            # Commit the nested transaction
            # This may fail due to race condition if another thread created the same claim
            try:
                savepoint.commit()

            except IntegrityError as ie:
                # Handle race condition: duplicate claim created between check and commit
                savepoint.rollback()
                if 'uq_identity_per_platform' in str(ie):
                    logger.warning(
                        "Race condition detected during update - duplicate claim created",
                        person_id=person_id,
                        identity_id=identity_id,
                        changes=changes
                    )
                    return {
                        'success': False,
                        'error': f"Update conflicts with existing identity on {final_platform if 'platform' in changes or 'normalized' in changes else claim.platform}. Another process may have created a duplicate claim."
                    }
                raise

            logger.info("Identity updated",
                       contact_id=person_id,
                       identity_id=identity_id,
                       changes=changes,
                       old_values=original_info)

            return {
                'success': True,
                'identity': {
                    'id': claim.id,
                    'kind': claim.kind,
                    'value': claim.value,
                    'normalized': claim.normalized,
                    'platform': claim.platform,
                    'confidence': claim.confidence,
                    'updated_at': claim.last_seen.isoformat()
                },
                'original': original_info,
                'changes_applied': changes
            }

        except IntegrityError as e:
            # Catch any IntegrityErrors not caught by the inner handler
            savepoint.rollback()
            logger.error("Database constraint violation during identity update", error=str(e))
            return {'success': False, 'error': "Identity constraint violation - update conflicts with existing data"}

        except Exception as e:
            # Rollback nested transaction on any other error
            savepoint.rollback()
            raise e
        
    except ValidationError as e:
        return {'success': False, 'error': f"Validation error: {str(e)}"}
    
    except IntegrityError as e:
        session.rollback()
        logger.error("Database constraint violation during identity update", error=str(e))
        return {'success': False, 'error': "Identity constraint violation - update conflicts with existing data"}
    
    except Exception as e:
        session.rollback()
        logger.error("Unexpected error updating identity", 
                    error=str(e), 
                    person_id=person_id,
                    identity_id=identity_id)
        return {'success': False, 'error': f"Failed to update identity: {str(e)}"}


def remove_contact_identity(
    session: Session,
    person_id: str,
    identity_id: str
) -> Dict[str, Any]:
    """
    Remove an identity claim from a contact.
    
    Args:
        session: Database session
        person_id: ID of the contact
        identity_id: ID of the identity claim to remove
        
    Returns:
        Dictionary with success status
    """
    try:
        # Find the identity claim
        claim = session.query(IdentityClaim).filter_by(
            id=identity_id,
            principal_id=person_id
        ).first()
        
        if not claim:
            return {'success': False, 'error': "Identity claim not found"}
        
        # Store info for logging
        removed_info = {
            'kind': claim.kind,
            'value': claim.value,
            'normalized': claim.normalized,
            'platform': claim.platform
        }
        
        session.delete(claim)
        session.commit()
        
        logger.info("Identity removed from contact", 
                   contact_id=person_id,
                   identity_id=identity_id,
                   **removed_info)
        
        return {
            'success': True,
            'removed_identity': removed_info
        }
        
    except Exception as e:
        session.rollback()
        logger.error("Unexpected error removing identity", error=str(e))
        return {'success': False, 'error': f"Failed to remove identity: {str(e)}"}
