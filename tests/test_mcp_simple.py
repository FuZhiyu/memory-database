"""
Simple MCP server tests that create their own mock data.
This approach avoids dependency on external fixtures.
"""

import pytest
import tempfile
import os
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.database.connection import Base
from src.models import Principal, IdentityClaim, Channel, Thread, Message, PersonMessage
from src.mcp_server.queries import (
    search_people_by_identity, 
    search_messages_for_person, 
    find_person_by_any_identity
)
from src.utils.ulid import generate_ulid


@pytest.fixture
def test_session():
    """Create an in-memory test database with sample data."""
    # Create in-memory SQLite database
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    
    # Create sample data
    alice = Principal(
        id=generate_ulid(),
        display_name="Alice Johnson",
        created_at=datetime.now(timezone.utc)
    )
    
    bob = Principal(
        id=generate_ulid(),
        display_name="Bob Wilson",
        created_at=datetime.now(timezone.utc)
    )
    
    session.add_all([alice, bob])
    
    # Add identity claims
    alice_email = IdentityClaim(
        id=generate_ulid(),
        principal_id=alice.id,
        kind="email",
        value="alice.johnson@techcorp.com",
        normalized="alice.johnson@techcorp.com",
        platform="email",
        confidence=1.0,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc)
    )
    
    alice_phone = IdentityClaim(
        id=generate_ulid(),
        principal_id=alice.id,
        kind="phone",
        value="+1-415-555-1001",
        normalized="+14155551001",
        platform="contacts",
        confidence=1.0,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc)
    )
    
    bob_email = IdentityClaim(
        id=generate_ulid(),
        principal_id=bob.id,
        kind="email",
        value="bob.wilson@example.com",
        normalized="bob.wilson@example.com",
        platform="email",
        confidence=1.0,
        first_seen=datetime.now(timezone.utc),
        last_seen=datetime.now(timezone.utc)
    )
    
    session.add_all([alice_email, alice_phone, bob_email])
    
    # Create a channel and thread
    channel = Channel(
        id=generate_ulid(),
        name="email-general",
        platform="email",
        created_at=datetime.now(timezone.utc)
    )
    
    thread = Thread(
        id=generate_ulid(),
        channel_id=channel.id,
        subject="Test Email Thread",
        created_at=datetime.now(timezone.utc)
    )
    
    session.add_all([channel, thread])
    
    # Create sample messages
    message1 = Message(
        id=generate_ulid(),
        thread_id=thread.id,
        content="Hello Bob, how are you doing?",
        sent_at=datetime.now(timezone.utc),
        platform_message_id="msg1"
    )
    
    message2 = Message(
        id=generate_ulid(),
        thread_id=thread.id,
        content="Hi Alice! I'm doing great, thanks for asking.",
        sent_at=datetime.now(timezone.utc),
        platform_message_id="msg2"
    )
    
    session.add_all([message1, message2])
    
    # Link messages to people
    alice_sends_msg1 = PersonMessage(
        id=generate_ulid(),
        principal_id=alice.id,
        message_id=message1.id,
        role="sender"
    )
    
    bob_receives_msg1 = PersonMessage(
        id=generate_ulid(),
        principal_id=bob.id,
        message_id=message1.id,
        role="recipient"
    )
    
    bob_sends_msg2 = PersonMessage(
        id=generate_ulid(),
        principal_id=bob.id,
        message_id=message2.id,
        role="sender"
    )
    
    alice_receives_msg2 = PersonMessage(
        id=generate_ulid(),
        principal_id=alice.id,
        message_id=message2.id,
        role="recipient"
    )
    
    session.add_all([alice_sends_msg1, bob_receives_msg1, bob_sends_msg2, alice_receives_msg2])
    
    session.commit()
    
    yield session
    
    session.close()


class TestMCPSearchFunctionality:
    """Test MCP search functionality with self-contained mock data."""
    
    def test_search_people_by_email(self, test_session):
        """Test searching people by email address."""
        results = search_people_by_identity(
            test_session, 
            email="alice.johnson@techcorp.com"
        )
        
        assert len(results) == 1
        assert results[0]['display_name'] == "Alice Johnson"
        assert 'email' in results[0]['identities']
        
        # Check that email is properly normalized
        email_claims = results[0]['identities']['email']
        assert any(
            claim['normalized'] == 'alice.johnson@techcorp.com' 
            for claim in email_claims
        )
    
    def test_search_people_by_phone(self, test_session):
        """Test searching people by phone number."""
        results = search_people_by_identity(
            test_session, 
            phone="+14155551001"
        )
        
        assert len(results) == 1
        assert results[0]['display_name'] == "Alice Johnson"
        assert 'phone' in results[0]['identities']
    
    def test_search_people_by_name_fuzzy(self, test_session):
        """Test fuzzy name searching."""
        results = search_people_by_identity(
            test_session, 
            name="Alice",
            fuzzy_match=True
        )
        
        assert len(results) >= 1
        alice = next((p for p in results if p['display_name'] == "Alice Johnson"), None)
        assert alice is not None
    
    def test_find_person_by_email(self, test_session):
        """Test identity resolution by email."""
        person_id = find_person_by_any_identity(
            test_session,
            person_email="alice.johnson@techcorp.com"
        )
        
        assert person_id is not None
        
        # Verify it's the correct person
        person = test_session.query(Principal).get(person_id)
        assert person.display_name == "Alice Johnson"
    
    def test_find_person_by_phone(self, test_session):
        """Test identity resolution by phone."""
        person_id = find_person_by_any_identity(
            test_session,
            person_phone="+14155551001"
        )
        
        assert person_id is not None
        
        # Verify it's the correct person
        person = test_session.query(Principal).get(person_id)
        assert person.display_name == "Alice Johnson"
    
    def test_search_messages_for_person(self, test_session):
        """Test searching messages for a specific person."""
        # Get Alice's ID
        alice_id = find_person_by_any_identity(
            test_session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            test_session,
            person_id=alice_id
        )
        
        # Alice should have 2 messages (1 sent, 1 received)
        assert len(messages) == 2
        
        # Check message structure
        for msg in messages:
            assert 'content' in msg
            assert 'sent_at' in msg
            assert 'thread' in msg
            assert 'channel' in msg['thread']
            assert msg['thread']['channel']['platform'] == "email"
    
    def test_search_messages_with_content_filter(self, test_session):
        """Test searching messages with content filter."""
        alice_id = find_person_by_any_identity(
            test_session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            test_session,
            person_id=alice_id,
            content_contains="Hello"
        )
        
        # Should find the message Alice sent that contains "Hello"
        assert len(messages) == 1
        assert "Hello" in messages[0]['content']
    
    def test_search_messages_with_platform_filter(self, test_session):
        """Test searching messages with platform filter."""
        alice_id = find_person_by_any_identity(
            test_session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            test_session,
            person_id=alice_id,
            platform="email"
        )
        
        # Should find all email messages for Alice
        assert len(messages) == 2
        for msg in messages:
            assert msg['thread']['channel']['platform'] == "email"
    
    def test_search_error_handling(self, test_session):
        """Test error handling for non-existent searches."""
        # Non-existent email
        results = search_people_by_identity(
            test_session,
            email="nonexistent@example.com"
        )
        assert len(results) == 0
        
        # Non-existent person for identity resolution
        person_id = find_person_by_any_identity(
            test_session,
            person_email="nonexistent@example.com"
        )
        assert person_id is None
        
        # Non-existent person for message search
        messages = search_messages_for_person(
            test_session,
            person_id="nonexistent-id"
        )
        assert len(messages) == 0


def test_mcp_server_simulation(test_session):
    """Test simulating MCP server tool behavior."""
    # Simulate search_person tool
    def simulate_search_person(email=None, name=None, fuzzy_match=False):
        try:
            people = search_people_by_identity(
                test_session,
                email=email,
                name=name,
                fuzzy_match=fuzzy_match
            )
            return {
                'people': people,
                'total_found': len(people),
                'search_criteria': {
                    'email': email,
                    'name': name,
                    'fuzzy_match': fuzzy_match
                }
            }
        except Exception as e:
            return {
                'error': str(e),
                'people': [],
                'total_found': 0
            }
    
    # Simulate search_messages tool
    def simulate_search_messages(person_email=None, limit=50):
        try:
            person_id = find_person_by_any_identity(
                test_session,
                person_email=person_email
            )
            
            if not person_id:
                return {
                    'error': 'Could not find person with provided identifiers',
                    'messages': [],
                    'total_found': 0,
                    'person_resolved': None
                }
            
            messages = search_messages_for_person(
                test_session,
                person_id=person_id,
                limit=limit
            )
            
            person = test_session.query(Principal).get(person_id)
            person_info = {
                'id': person.id,
                'display_name': person.display_name
            } if person else None
            
            return {
                'messages': messages,
                'total_found': len(messages),
                'person_resolved': person_info
            }
        except Exception as e:
            return {
                'error': str(e),
                'messages': [],
                'total_found': 0,
                'person_resolved': None
            }
    
    # Test simulated tools
    person_result = simulate_search_person(email="alice.johnson@techcorp.com")
    assert not person_result.get('error')
    assert person_result['total_found'] == 1
    assert person_result['people'][0]['display_name'] == "Alice Johnson"
    
    messages_result = simulate_search_messages(person_email="alice.johnson@techcorp.com")
    assert not messages_result.get('error')
    assert messages_result.get('person_resolved') is not None
    assert messages_result['person_resolved']['display_name'] == "Alice Johnson"
    assert messages_result['total_found'] == 2
    
    # Test error case
    error_result = simulate_search_messages(person_email="nonexistent@example.com")
    assert error_result.get('error') is not None
    assert 'Could not find person' in error_result['error']


if __name__ == "__main__":
    print("🧪 Running simple MCP tests...")
    pytest.main([__file__, "-v"])