"""
Database query functions for MCP server.
"""

from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, desc
from datetime import datetime
import structlog

from memory_database.models import Principal, IdentityClaim, Message, PersonMessage, Thread, Channel, MessageAttachment
from memory_database.utils.normalization import normalize_identity_value

logger = structlog.get_logger()


def search_people_by_identity(
    session: Session,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
    username: Optional[str] = None,
    contact_id: Optional[str] = None,
    fuzzy_match: bool = False,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Search for people using flexible identity criteria.
    """
    query = session.query(Principal).options(
        joinedload(Principal.identity_claims)
    )
    
    conditions = []
    
    # Build conditions for each provided identity
    if phone:
        normalized_phone = normalize_identity_value(phone, 'phone')
        if normalized_phone:
            conditions.append(
                Principal.identity_claims.any(
                    and_(
                        IdentityClaim.kind == 'phone',
                        IdentityClaim.normalized == normalized_phone
                    )
                )
            )
    
    if email:
        normalized_email = normalize_identity_value(email, 'email')
        if normalized_email:
            conditions.append(
                Principal.identity_claims.any(
                    and_(
                        IdentityClaim.kind == 'email',
                        IdentityClaim.normalized == normalized_email
                    )
                )
            )
    
    if name:
        normalized_name = normalize_identity_value(name, 'display_name')
        if fuzzy_match:
            # Use ILIKE for fuzzy matching
            conditions.append(
                or_(
                    Principal.display_name.ilike(f'%{name}%'),
                    Principal.identity_claims.any(
                        and_(
                            IdentityClaim.kind == 'display_name',
                            IdentityClaim.normalized.ilike(f'%{normalized_name}%')
                        )
                    )
                )
            )
        else:
            conditions.append(
                or_(
                    Principal.display_name.ilike(normalized_name),
                    Principal.identity_claims.any(
                        and_(
                            IdentityClaim.kind == 'display_name',
                            IdentityClaim.normalized == normalized_name
                        )
                    )
                )
            )
    
    if username:
        normalized_username = normalize_identity_value(username, 'username')
        if normalized_username:
            conditions.append(
                Principal.identity_claims.any(
                    and_(
                        IdentityClaim.kind == 'username',
                        IdentityClaim.normalized == normalized_username
                    )
                )
            )
    
    if contact_id:
        normalized_contact_id = normalize_identity_value(contact_id, 'contact_id')
        if normalized_contact_id:
            conditions.append(
                Principal.identity_claims.any(
                    and_(
                        IdentityClaim.kind == 'contact_id',
                        IdentityClaim.normalized == normalized_contact_id
                    )
                )
            )
    
    if not conditions:
        return []
    
    # Combine conditions with OR (any match)
    query = query.filter(or_(*conditions))
    
    principals = query.limit(limit).all()
    
    # Format results
    results = []
    for principal in principals:
        # Group identities by kind
        identities = {}
        for claim in principal.identity_claims:
            if claim.kind not in identities:
                identities[claim.kind] = []
            identities[claim.kind].append({
                'id': claim.id,
                'value': claim.value,
                'normalized': claim.normalized,
                'platform': claim.platform,
                'confidence': claim.confidence,
                'first_seen': claim.first_seen.isoformat() if claim.first_seen else None,
                'last_seen': claim.last_seen.isoformat() if claim.last_seen else None
            })
        
        results.append({
            'id': principal.id,
            'display_name': principal.display_name,
            'org': principal.org,
            'created_at': principal.created_at.isoformat() if principal.created_at else None,
            'identities': identities,
            'merged_from': principal.merged_from or [],
            'extra': principal.extra or {}
        })
    
    return results


def find_person_by_any_identity(
    session: Session,
    person_email: Optional[str] = None,
    person_phone: Optional[str] = None,
    person_name: Optional[str] = None
) -> Optional[str]:
    """
    Find a person ID by any identity. Returns the first match found.
    """
    conditions = []
    
    if person_email:
        normalized_email = normalize_identity_value(person_email, 'email')
        if normalized_email:
            conditions.append(
                and_(
                    IdentityClaim.kind == 'email',
                    IdentityClaim.normalized == normalized_email
                )
            )
    
    if person_phone:
        normalized_phone = normalize_identity_value(person_phone, 'phone')
        if normalized_phone:
            conditions.append(
                and_(
                    IdentityClaim.kind == 'phone',
                    IdentityClaim.normalized == normalized_phone
                )
            )
    
    if person_name:
        normalized_name = normalize_identity_value(person_name, 'display_name')
        if normalized_name:
            # Try both display_name and identity claims
            principal_by_name = session.query(Principal).filter(
                Principal.display_name.ilike(normalized_name)
            ).first()
            if principal_by_name:
                return principal_by_name.id
            
            conditions.append(
                and_(
                    IdentityClaim.kind == 'display_name',
                    IdentityClaim.normalized.ilike(f'%{normalized_name}%')
                )
            )
    
    if not conditions:
        return None
    
    # Try to find by identity claims
    claim = session.query(IdentityClaim).filter(or_(*conditions)).first()
    return claim.principal_id if claim else None


def search_messages_for_person(
    session: Session,
    person_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    content_contains: Optional[str] = None,
    platform: Optional[str] = None,
    include_attachments: bool = False,
    limit: int = 50
) -> List[Dict[str, Any]]:
    """
    Search messages for a specific person with optional filters.
    """
    query = session.query(Message).join(PersonMessage).join(Thread).join(Channel)
    
    if include_attachments:
        query = query.options(joinedload(Message.attachments))
    
    # Filter by person
    query = query.filter(PersonMessage.principal_id == person_id)
    
    # Date filters
    if date_from:
        try:
            date_from_dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            query = query.filter(Message.sent_at >= date_from_dt)
        except ValueError:
            logger.warning("Invalid date_from format", date_from=date_from)
    
    if date_to:
        try:
            date_to_dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            query = query.filter(Message.sent_at <= date_to_dt)
        except ValueError:
            logger.warning("Invalid date_to format", date_to=date_to)
    
    # Content filter
    if content_contains:
        query = query.filter(Message.content.ilike(f'%{content_contains}%'))
    
    # Platform filter
    if platform:
        query = query.filter(Channel.platform == platform)
    
    # Order by most recent first
    query = query.order_by(desc(Message.sent_at))
    
    messages = query.limit(limit).all()
    
    # Format results
    results = []
    for message in messages:
        # Get sender info
        sender_link = session.query(PersonMessage).filter_by(
            message_id=message.id, role='sender'
        ).first()
        
        sender_info = None
        if sender_link:
            sender = session.query(Principal).get(sender_link.principal_id)
            if sender:
                sender_info = {
                    'id': sender.id,
                    'display_name': sender.display_name
                }
        
        # Get recipients info
        recipient_links = session.query(PersonMessage).filter_by(
            message_id=message.id, role='recipient'
        ).all()
        
        recipients = []
        for link in recipient_links:
            recipient = session.query(Principal).get(link.principal_id)
            if recipient:
                recipients.append({
                    'id': recipient.id,
                    'display_name': recipient.display_name
                })
        
        # Format attachments if requested
        attachments = []
        if include_attachments and message.attachments:
            for att in message.attachments:
                attachments.append({
                    'filename': att.filename,
                    'mime_type': att.mime_type,
                    'file_size': att.file_size,
                    'storage_method': att.storage_method,
                    'stored_path': att.stored_path
                })
        
        results.append({
            'id': message.id,
            'content': message.content,
            'sent_at': message.sent_at.isoformat() if message.sent_at else None,
            'sender': sender_info,
            'recipients': recipients,
            'thread': {
                'id': message.thread.id,
                'subject': message.thread.subject,
                'channel': {
                    'id': message.thread.channel.id,
                    'name': message.thread.channel.name,
                    'platform': message.thread.channel.platform
                }
            },
            'attachments': attachments if include_attachments else None,
            'extra': message.extra or {}
        })
    
    return results
