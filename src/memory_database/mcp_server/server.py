"""
Memory Database - Personal Communication Assistant via MCP

Provides intelligent search across your contacts and complete communication history
(iMessage, email, Google Contacts) through the Model Context Protocol.

**PRIMARY PURPOSE**: Proactively search when people are mentioned in conversations to provide
relevant context about who they are and what they've said.

**CORE WORKFLOW (practical):**
1. If you already have a stable identifier (person id/email/phone/username), pass it directly via the `person` selector to downstream tools (e.g., search_messages, photos_search). This is the most accurate path.
2. If you only have a name or it's ambiguous, first call search_person() to disambiguate and get the `person.id`, then pass that `id` to downstream tools.
3. For Photos-only label matching, you may use `people: ["Photos Person Label"]`, but prefer `person` when possible for DB-backed identity resolution.

**KEY CAPABILITIES:**
- Complete contact database with identity resolution across platforms
- Full communication history search (iMessage, email)
- Smart person identification (name, email, phone, username)
- Message content search with timeline and attachment support
- Contact management (add, update, link identities)

**AUTO-TRIGGER PATTERNS:**
- Names: "Aditya", "Dr. Smith", "my friend Sarah"
- Contact references: "that person", "my colleague", "the guy with..."
- Communication queries: "What did X say?", "Recent messages from Y"

This server uses FastMCP with session-based protocol. Available transports:
- stdio: For local Claude Desktop integration  
- http: For remote access (FastMCP's Streamable HTTP implementation)
"""

import hmac
import os
from typing import Optional, List, Dict, Any
import structlog
from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken, TokenVerifier

from memory_database.database.connection import DatabaseManager, DatabaseSettings
from .queries import search_people_by_identity, find_person_by_any_identity, search_messages_for_person
from memory_database.utils.identity_resolver import resolve_person_selector
from .write_tools import (
    create_contact, 
    add_contact_identity, 
    update_contact_identity,
    remove_contact_identity,
    ALLOWED_IDENTITY_KINDS,
    ALLOWED_PLATFORMS
)

logger = structlog.get_logger()


class _StaticTokenAuth(TokenVerifier):
    """Simple bearer token authentication for HTTP transports."""

    def __init__(self, token: str, resource_server_url: Optional[str] = None):
        super().__init__(resource_server_url=resource_server_url)
        self._expected_token = token.strip()

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        provided = token.strip()
        if provided.lower().startswith("bearer "):
            provided = provided[7:].strip()

        if not provided:
            return None

        if not hmac.compare_digest(provided, self._expected_token):
            return None

        return AccessToken(
            token=provided,
            client_id="memory-database-http",
            scopes=["memory-database:full"],
        )


HTTP_AUTH_TOKEN = os.getenv("MEMORY_DB_HTTP_TOKEN")
HTTP_RESOURCE_URL = os.getenv("MEMORY_DB_HTTP_RESOURCE_URL")

auth_provider = None
if HTTP_AUTH_TOKEN:
    auth_provider = _StaticTokenAuth(
        token=HTTP_AUTH_TOKEN,
        resource_server_url=HTTP_RESOURCE_URL,
    )

# Initialize MCP server
mcp = FastMCP("memory-database", auth=auth_provider)

# Initialize database
db_manager = DatabaseManager(DatabaseSettings())

# Register additional tool modules (e.g., photos)
try:
    # Importing registers tools on the same FastMCP instance
    from . import photos_tools  # noqa: F401
except Exception as _e:
    # Don't crash the whole server if optional tools fail to load
    logger.warning("Optional tools not loaded", tool_module="photos_tools", error=str(_e))


@mcp.tool
def search_person(
    phone: Optional[str] = None,
    email: Optional[str] = None,
    name: Optional[str] = None,
    username: Optional[str] = None,
    contact_id: Optional[str] = None,
    fuzzy_match: bool = False,
    include_all_identities: bool = True,
    limit: int = 10
) -> Dict[str, Any]:
    """
    Search your contact database for people using any identifier.

    **PROACTIVE USAGE - Use this tool whenever the user mentions:**
    - Any person's name ("John", "Sarah", "Dr. Smith", "Aditya")
    - Phone numbers or email addresses
    - Usernames, nicknames, or handles
    - References like "my contact", "that person", "my friend", etc.

    This database contains all your contacts with their communication history
    across platforms (iMessage, email, Google Contacts). Always search when people
    are mentioned to provide relevant context about who they are.
    
    **Common usage patterns:**
    - User: "What did Aditya say about the project?" → search_person(name="Aditya", fuzzy_match=True)
    - User: "Email from john@company.com" → search_person(email="john@company.com")
    - User: "My friend Sarah mentioned..." → search_person(name="Sarah", fuzzy_match=True)
    - User: "The person with +1-555-0123" → search_person(phone="+1-555-0123")
    
    
    Available identity types:
    - phone: E.164 normalized phone numbers (+14155551234)
    - email: Lowercase normalized email addresses
    - name: Display names (supports fuzzy matching)
    - username: Platform usernames (lowercase)
    - contact_id: Platform-specific contact IDs
    - memory_url: Permalinks to basic memory notes (returned as memory://...)
    
    Args:
        phone: Phone number to search for (will be normalized to E.164)
        email: Email address to search for (will be normalized to lowercase)
        name: Display name to search for (supports fuzzy matching)
        username: Platform username to search for
        contact_id: Platform-specific contact ID
        fuzzy_match: Enable fuzzy matching for name searches (recommended for names)
        include_all_identities: Include all identity claims in results
        limit: Maximum number of results to return
        
    Returns:
        Dictionary with 'people' key containing list of matching people with all their 
        known identities, organization info, and platform details
    """
    try:
        with db_manager.get_session() as session:
            people = search_people_by_identity(
                session=session,
                phone=phone,
                email=email,
                name=name,
                username=username,
                contact_id=contact_id,
                fuzzy_match=fuzzy_match,
                limit=limit
            )
            
            # If not including all identities, simplify the response
            if not include_all_identities:
                for person in people:
                    # Keep only the primary identity types that were searched
                    simplified_identities = {}
                    if phone and 'phone' in person['identities']:
                        simplified_identities['phone'] = person['identities']['phone']
                    if email and 'email' in person['identities']:
                        simplified_identities['email'] = person['identities']['email']
                    if name and 'display_name' in person['identities']:
                        simplified_identities['display_name'] = person['identities']['display_name']
                    if username and 'username' in person['identities']:
                        simplified_identities['username'] = person['identities']['username']
                    person['identities'] = simplified_identities
            
            return {
                'people': people,
                'total_found': len(people),
                'search_criteria': {
                    'phone': phone,
                    'email': email,
                    'name': name,
                    'username': username,
                    'contact_id': contact_id,
                    'fuzzy_match': fuzzy_match
                }
            }
    
    except Exception as e:
        logger.error("Error searching people", error=str(e))
        return {
            'error': f"Search failed: {str(e)}",
            'people': [],
            'total_found': 0
        }


@mcp.tool
def search_messages(
    person_id: Optional[str] = None,
    person_email: Optional[str] = None,
    person_phone: Optional[str] = None,
    person_name: Optional[str] = None,
    person: Optional[Dict[str, Any]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    content_contains: Optional[str] = None,
    platform: Optional[str] = None,
    include_attachments: bool = False,
    limit: int = 50
) -> Dict[str, Any]:
    """
    Search through your complete communication history (iMessage, email) for a specific person.
    
    **RECOMMENDED WORKFLOW:**
    1. First use search_person() to find the person's contact info
    2. Then use this tool with the person_id for best results
    3. Or directly use person_email/person_name if you have them
    
    **Common patterns:**
    - User: "What did Aditya say about the project?" 
      → search_person(name="Aditya") → search_messages(person_id="...", content_contains="project")
    - User: "Recent emails from john@company.com"
      → search_messages(person_email="john@company.com", platform="email", date_from="2024-01-01")
    - User: "Files Sarah sent me"
      → search_messages(person_name="Sarah", include_attachments=true)
    
    Person identification (provide any one):
    - person_id: Direct person ID from search_person() (most efficient)
    - person_email: Email address (will be normalized and resolved to person)
    - person_phone: Phone number (will be normalized and resolved to person)
    - person_name: Display name (fuzzy matching supported)
    
    Message filters:
    - date_from/date_to: ISO format dates (e.g., "2024-01-01T00:00:00")
    - content_contains: Text to search for in message content
    - platform: Filter by platform ('email', 'imessage')
    - include_attachments: Include attachment information
    
    Args:
        person_id: Direct person ID (most efficient, get from search_person)
        person_email: Email to resolve to person
        person_phone: Phone number to resolve to person
        person_name: Display name to resolve to person
        date_from: Start date filter (ISO format)
        date_to: End date filter (ISO format)
        content_contains: Text to search in message content
        platform: Platform filter ('email', 'imessage')
        include_attachments: Include attachment details
        limit: Maximum number of messages to return
        
    Returns:
        Dictionary with 'messages' key containing message content, timestamps, 
        sender/recipients, platform info, and optionally attachments
    """
    try:
        with db_manager.get_session() as session:
            # Resolve person ID if not provided directly
            resolved_person_id = person_id

            if not resolved_person_id and person:
                pr = resolve_person_selector(session, person)
                if pr:
                    resolved_person_id = pr.id

            if not resolved_person_id:
                resolved_person_id = find_person_by_any_identity(
                    session=session,
                    person_email=person_email,
                    person_phone=person_phone,
                    person_name=person_name
                )
            
            if not resolved_person_id:
                return {
                    'error': 'Could not find person with provided identifiers',
                    'messages': [],
                    'total_found': 0,
                    'person_resolved': None
                }
            
            # Search messages for the resolved person
            messages = search_messages_for_person(
                session=session,
                person_id=resolved_person_id,
                date_from=date_from,
                date_to=date_to,
                content_contains=content_contains,
                platform=platform,
                include_attachments=include_attachments,
                limit=limit
            )
            
            # Get person info for context
            from memory_database.models import Principal
            person = session.query(Principal).get(resolved_person_id)
            person_info = {
                'id': person.id,
                'display_name': person.display_name,
                'org': person.org
            } if person else None
            
            return {
                'messages': messages,
                'total_found': len(messages),
                'person_resolved': person_info,
                'search_criteria': {
                    'person_id': person_id,
                    'person_email': person_email,
                    'person_phone': person_phone,
                    'person_name': person_name,
                    'date_from': date_from,
                    'date_to': date_to,
                    'content_contains': content_contains,
                    'platform': platform,
                    'include_attachments': include_attachments
                }
            }
    
    except Exception as e:
        logger.error("Error searching messages", error=str(e))
        return {
            'error': f"Search failed: {str(e)}",
            'messages': [],
            'total_found': 0,
            'person_resolved': None
        }


@mcp.tool
def create_new_contact(
    display_name: str,
    identities: Optional[List[Dict[str, Any]]] = None,
    org: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new contact in your database when mentioned people aren't found.
    
    **WHEN TO USE:**
    - After search_person() returns no results for someone the user mentions
    - User explicitly asks to add a new contact: "Add John Doe to my contacts"
    - User provides contact info for someone new: "Save jane@company.com as Jane Smith"
    
    **WORKFLOW:**
    1. Always try search_person() first to avoid duplicates
    2. Only create new contacts if the person doesn't exist
    3. Include all known identities (email, phone, etc.) when creating
    
    **Example usage:**
    - User: "Add my new colleague Sarah Johnson, her email is sarah@company.com"
      → search_person(name="Sarah Johnson") → if not found → create_new_contact(...)
    
    Creates a new person in the database with the specified display name and
    optional identity information (email, phone, etc.).
    
    Args:
        display_name: Display name for the contact (required)
        identities: List of identity objects with 'kind', 'value', 'platform', 'confidence'
        org: Optional organization name
        
    Available identity kinds: email, phone, display_name, username, contact_id, alias, memory_url
    Available platforms: contacts, imessage, email, manual, basic-memory
    
    Example identity:
    {
        "kind": "email",
        "value": "john.doe@example.com", 
        "platform": "manual",
        "confidence": 0.9
    }
    
    Returns:
        Dictionary with contact information and success status
    """
    try:
        with db_manager.get_session() as session:
            return create_contact(session, display_name, identities or [], org)
    except Exception as e:
        logger.error("Error creating contact", error=str(e))
        return {'success': False, 'error': f"Failed to create contact: {str(e)}"}


@mcp.tool
def add_identity_to_contact(
    person_id: str,
    kind: str,
    value: str,
    platform: str = 'manual',
    confidence: float = 0.9
) -> Dict[str, Any]:
    """
    Add additional contact information (email, phone, etc.) to an existing person.
    
    **WHEN TO USE:**
    - User provides new contact info for someone you found: "His email is john@work.com"
    - Connecting multiple identities to same person: "That's also @john_twitter on Twitter"
    - User updates contact details: "Sarah's new phone number is +1-555-0123"
    
    **WORKFLOW:**
    1. First use search_person() to find the contact
    2. Add the new identity information to link it to the same person
    
    Adds email, phone, username, or other identity information to a contact.
    All values are automatically normalized and validated.
    
    Args:
        person_id: ID of the existing contact (from search_person results)
        kind: Type of identity (email, phone, username, contact_id, display_name, alias)
        value: Identity value (will be normalized)
        platform: Source platform (manual, contacts, imessage, email)
        confidence: Confidence score (0.0-1.0, default 0.9)
        
    Available identity kinds: email, phone, display_name, username, contact_id, alias, memory_url
    Available platforms: contacts, imessage, email, manual, Life.md
    
    Examples:
    - add_identity_to_contact("01ABC123", "email", "john@example.com")
    - add_identity_to_contact("01ABC123", "phone", "+1-415-555-1234") 
    - add_identity_to_contact("01ABC123", "username", "@john_doe", "manual")
    - add_identity_to_contact("01ABC123", "alias", "Johnny", "manual")
    
    Returns:
        Dictionary with success status and identity information
    """
    try:
        with db_manager.get_session() as session:
            return add_contact_identity(session, person_id, kind, value, platform, confidence)
    except Exception as e:
        logger.error("Error adding identity to contact", error=str(e))
        return {'success': False, 'error': f"Failed to add identity: {str(e)}"}


@mcp.tool 
def update_identity_to_contact(
    person_id: str,
    identity_id: str,
    new_value: Optional[str] = None,
    new_confidence: Optional[float] = None,
    new_platform: Optional[str] = None
) -> Dict[str, Any]:
    """
    Update an existing identity claim for a contact.
    
    Allows updating the value, confidence, or platform of an existing identity claim.
    Values are automatically normalized and validated. At least one field must be provided.
    
    Args:
        person_id: ID of the contact
        identity_id: ID of the specific identity claim to update
        new_value: New identity value (will be normalized for the existing kind)
        new_confidence: New confidence score (0.0-1.0)
        new_platform: New platform source (manual, contacts, imessage, email)
        
    Available platforms: contacts, imessage, email, manual
    
    Examples:
    - update_identity_to_contact("01ABC123", "01DEF456", new_value="john.doe@gmail.com")
    - update_identity_to_contact("01ABC123", "01DEF456", new_confidence=0.95)
    - update_identity_to_contact("01ABC123", "01DEF456", new_platform="contacts")
    
    Returns:
        Dictionary with success status and updated identity information
    """
    try:
        with db_manager.get_session() as session:
            return update_contact_identity(session, person_id, identity_id, new_value, new_confidence, new_platform)
    except Exception as e:
        logger.error("Error updating identity", error=str(e))
        return {'success': False, 'error': f"Failed to update identity: {str(e)}"}


@mcp.tool
def remove_identity_from_contact(
    person_id: str,
    identity_id: str
) -> Dict[str, Any]:
    """
    Remove an identity claim from a contact.
    
    Removes a specific identity (email, phone, etc.) from a contact.
    Use search_person first to find the identity_id to remove.
    
    Args:
        person_id: ID of the contact
        identity_id: ID of the specific identity claim to remove
        
    Returns:
        Dictionary with success status and removed identity info
    """
    try:
        with db_manager.get_session() as session:
            return remove_contact_identity(session, person_id, identity_id)
    except Exception as e:
        logger.error("Error removing identity from contact", error=str(e))
        return {'success': False, 'error': f"Failed to remove identity: {str(e)}"}


# Identity types resource 
@mcp.resource("file://identity_types")
def get_identity_types_resource() -> str:
    """
    Resource containing allowed identity kinds and platforms for validation.
    
    Returns reference data for creating or updating contact identities.
    Shows valid values for 'kind' and 'platform' parameters.
    """
    data = {
        'allowed_identity_kinds': sorted(list(ALLOWED_IDENTITY_KINDS)),
        'allowed_platforms': sorted(list(ALLOWED_PLATFORMS)),
        'description': {
            'identity_kinds': {
                'email': 'Email addresses (auto-validated and normalized)',
                'phone': 'Phone numbers (normalized to E.164 format)',
                'display_name': 'Display names for the person',
                'username': 'Usernames from various platforms',
                'contact_id': 'Platform-specific contact identifiers (e.g., Google Contacts resource_name)',
                'alias': 'Alternative names, nicknames, or name variants',
                'memory_url': 'Permalink to Life.md note associated with this person'
            },
            'platforms': {
                'contacts': 'Google Contacts or similar contact system',
                'imessage': 'iMessage/Messages app',
                'email': 'Email systems',
                'manual': 'Manually entered via MCP tools',
                'Life.md': 'Generated by Life.md note creation tools'
            }
        },
        'uniqueness_constraints': {
            'rule': 'Each person can have only one identity claim per (platform, normalized_value) combination',
            'examples': {
                'allowed': [
                    'Person A: email=john@ex.com from contacts AND email=john@ex.com from imessage (different platforms)',
                    'Person A: email=john@work.com AND email=john@personal.com from contacts (different values)'
                ],
                'blocked': [
                    'Person A: email=john@ex.com twice from contacts (same platform, same value)'
                ]
            },
            'rationale': 'Platform-level granularity preserves provenance and enables richer identity resolution'
        }
    }
    
    import json
    return json.dumps(data, indent=2)


if __name__ == "__main__":
    # For development/testing - run with stdio transport
    # Note: When imported by run_mcp_server.py, this block won't execute
    mcp.run(transport="stdio")
