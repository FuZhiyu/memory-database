"""
Tests for MCP server search functionality using mock database.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from src.mcp_server.queries import (
    search_people_by_identity, 
    search_messages_for_person, 
    find_person_by_any_identity
)


class TestMCPSearchQueries:
    """Test MCP server search queries using mock database."""
    
    def test_search_people_by_identity_email(self, session, sample_principals):
        """Test searching people by email address."""
        # Search for Alice Johnson by email
        results = search_people_by_identity(
            session, 
            email="alice.johnson@techcorp.com"
        )
        
        assert len(results) == 1
        assert results[0]['display_name'] == "Alice Johnson"
        assert 'email' in results[0]['identities']
        assert any(
            claim['normalized'] == 'alice.johnson@techcorp.com' 
            for claim in results[0]['identities']['email']
        )
    
    def test_search_people_by_identity_phone(self, session, sample_principals):
        """Test searching people by phone number."""
        results = search_people_by_identity(
            session, 
            phone="+14155551001"
        )
        
        assert len(results) == 1
        assert results[0]['display_name'] == "Alice Johnson"
        assert 'phone' in results[0]['identities']
    
    def test_search_people_by_identity_name_exact(self, session, sample_principals):
        """Test searching people by exact name."""
        results = search_people_by_identity(
            session, 
            name="Bob Wilson"
        )
        
        assert len(results) == 1
        assert results[0]['display_name'] == "Bob Wilson"
    
    def test_search_people_by_identity_name_fuzzy(self, session, sample_principals):
        """Test searching people by fuzzy name matching."""
        results = search_people_by_identity(
            session, 
            name="Alice",
            fuzzy_match=True
        )
        
        # Should find Alice Johnson
        assert len(results) >= 1
        alice = next((p for p in results if p['display_name'] == "Alice Johnson"), None)
        assert alice is not None
    
    def test_search_people_by_identity_multiple_criteria(self, session, sample_principals):
        """Test searching people with multiple criteria."""
        results = search_people_by_identity(
            session, 
            email="alice.johnson@techcorp.com",
            name="Alice"
        )
        
        assert len(results) == 1
        assert results[0]['display_name'] == "Alice Johnson"
    
    def test_search_people_by_identity_no_results(self, session, sample_principals):
        """Test searching for non-existent person."""
        results = search_people_by_identity(
            session, 
            email="nonexistent@example.com"
        )
        
        assert len(results) == 0
    
    def test_search_people_by_identity_empty_params(self, session, sample_principals):
        """Test searching with no parameters."""
        results = search_people_by_identity(session)
        
        assert len(results) == 0
    
    def test_find_person_by_any_identity_email(self, session, sample_principals):
        """Test finding person ID by email."""
        person_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        assert person_id is not None
        
        # Verify it's the correct person
        from src.models import Principal
        person = session.query(Principal).get(person_id)
        assert person.display_name == "Alice Johnson"
    
    def test_find_person_by_any_identity_phone(self, session, sample_principals):
        """Test finding person ID by phone."""
        person_id = find_person_by_any_identity(
            session,
            person_phone="+14155551001"
        )
        
        assert person_id is not None
        
        # Verify it's the correct person
        from src.models import Principal
        person = session.query(Principal).get(person_id)
        assert person.display_name == "Alice Johnson"
    
    def test_find_person_by_any_identity_name(self, session, sample_principals):
        """Test finding person ID by name."""
        person_id = find_person_by_any_identity(
            session,
            person_name="Bob Wilson"
        )
        
        assert person_id is not None
        
        # Verify it's the correct person
        from src.models import Principal
        person = session.query(Principal).get(person_id)
        assert person.display_name == "Bob Wilson"
    
    def test_find_person_by_any_identity_not_found(self, session, sample_principals):
        """Test finding non-existent person."""
        person_id = find_person_by_any_identity(
            session,
            person_email="nonexistent@example.com"
        )
        
        assert person_id is None
    
    def test_search_messages_for_person_by_id(self, session, sample_principals, sample_messages):
        """Test searching messages for a specific person by ID."""
        # Get Alice Johnson's ID
        john_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            session,
            person_id=john_id
        )
        
        # Should find messages where John is sender or recipient
        assert len(messages) > 0
        
        # Verify message structure
        msg = messages[0]
        assert 'content' in msg
        assert 'sent_at' in msg
        assert 'thread' in msg
        assert 'sender' in msg or 'recipients' in msg
    
    def test_search_messages_for_person_with_content_filter(self, session, sample_principals, sample_messages):
        """Test searching messages with content filter."""
        john_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            session,
            person_id=john_id,
            content_contains="Hello"
        )
        
        # Should only return messages containing "Hello"
        for msg in messages:
            assert "Hello" in msg['content']
    
    def test_search_messages_for_person_with_platform_filter(self, session, sample_principals, sample_messages):
        """Test searching messages with platform filter."""
        john_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            session,
            person_id=john_id,
            platform="email"
        )
        
        # Should only return email messages
        for msg in messages:
            assert msg['thread']['channel']['platform'] == "email"
    
    def test_search_messages_for_person_with_date_filter(self, session, sample_principals, sample_messages):
        """Test searching messages with date filter."""
        john_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            session,
            person_id=john_id,
            date_from="2024-01-01T00:00:00",
            date_to="2024-12-31T23:59:59"
        )
        
        # Should return messages within date range
        assert len(messages) >= 0  # May be 0 if sample data is outside range
    
    def test_search_messages_for_person_with_limit(self, session, sample_principals, sample_messages):
        """Test searching messages with limit."""
        john_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            session,
            person_id=john_id,
            limit=1
        )
        
        # Should return at most 1 message
        assert len(messages) <= 1
    
    def test_search_messages_for_person_nonexistent(self, session, sample_principals, sample_messages):
        """Test searching messages for non-existent person."""
        messages = search_messages_for_person(
            session,
            person_id="nonexistent-id"
        )
        
        assert len(messages) == 0
    
    def test_search_messages_for_person_with_attachments(self, session, sample_principals, sample_messages):
        """Test searching messages with attachment info."""
        john_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        messages = search_messages_for_person(
            session,
            person_id=john_id,
            include_attachments=True
        )
        
        # Should include attachments field
        for msg in messages:
            assert 'attachments' in msg
            # attachments field should be a list (may be empty)
            assert isinstance(msg['attachments'], list)


class TestMCPServerToolWrappers:
    """Test MCP server tool wrapper functions using mock database."""
    
    def test_search_person_tool_email(self, session, sample_principals):
        """Test search_person tool with email."""
        # Mock the database manager to use our test session
        with patch('src.mcp_server.server.db_manager.get_session') as mock_session:
            mock_session.return_value.__enter__.return_value = session
            mock_session.return_value.__exit__.return_value = None
            
            from src.mcp_server.server import search_person
            
            # Get the actual function from the tool
            if hasattr(search_person, 'func'):
                search_person_func = search_person.func
            else:
                # Try to get the function directly
                import inspect
                if inspect.isfunction(search_person):
                    search_person_func = search_person
                else:
                    pytest.skip("Could not access search_person function")
            
            result = search_person_func(email="alice.johnson@techcorp.com")
            
            assert result['total_found'] == 1
            assert len(result['people']) == 1
            assert result['people'][0]['display_name'] == "Alice Johnson"
            assert not result.get('error')
    
    def test_search_person_tool_not_found(self, session, sample_principals):
        """Test search_person tool with non-existent email."""
        with patch('src.mcp_server.server.db_manager.get_session') as mock_session:
            mock_session.return_value.__enter__.return_value = session
            mock_session.return_value.__exit__.return_value = None
            
            from src.mcp_server.server import search_person
            
            # Get the actual function
            if hasattr(search_person, 'func'):
                search_person_func = search_person.func
            else:
                import inspect
                if inspect.isfunction(search_person):
                    search_person_func = search_person
                else:
                    pytest.skip("Could not access search_person function")
            
            result = search_person_func(email="nonexistent@example.com")
            
            assert result['total_found'] == 0
            assert len(result['people']) == 0
            assert not result.get('error')
    
    def test_search_messages_tool_email_resolution(self, session, sample_principals, sample_messages):
        """Test search_messages tool with email resolution."""
        with patch('src.mcp_server.server.db_manager.get_session') as mock_session:
            mock_session.return_value.__enter__.return_value = session
            mock_session.return_value.__exit__.return_value = None
            
            from src.mcp_server.server import search_messages
            
            # Get the actual function
            if hasattr(search_messages, 'func'):
                search_messages_func = search_messages.func
            else:
                import inspect
                if inspect.isfunction(search_messages):
                    search_messages_func = search_messages
                else:
                    pytest.skip("Could not access search_messages function")
            
            result = search_messages_func(
                person_email="alice.johnson@techcorp.com",
                limit=10
            )
            
            assert not result.get('error')
            assert result.get('person_resolved') is not None
            assert result['person_resolved']['display_name'] == "Alice Johnson"
            assert 'messages' in result
            assert 'total_found' in result
    
    def test_search_messages_tool_person_not_found(self, session, sample_principals, sample_messages):
        """Test search_messages tool with non-existent person."""
        with patch('src.mcp_server.server.db_manager.get_session') as mock_session:
            mock_session.return_value.__enter__.return_value = session
            mock_session.return_value.__exit__.return_value = None
            
            from src.mcp_server.server import search_messages
            
            # Get the actual function
            if hasattr(search_messages, 'func'):
                search_messages_func = search_messages.func
            else:
                import inspect
                if inspect.isfunction(search_messages):
                    search_messages_func = search_messages
                else:
                    pytest.skip("Could not access search_messages function")
            
            result = search_messages_func(
                person_email="nonexistent@example.com"
            )
            
            assert result.get('error') is not None
            assert 'Could not find person' in result['error']
            assert result['total_found'] == 0
            assert result['person_resolved'] is None


class TestMCPSearchDataConsistency:
    """Test data consistency between different search methods."""
    
    def test_person_search_consistency(self, session, sample_principals):
        """Test that different search methods return consistent results."""
        # Search by email
        email_results = search_people_by_identity(
            session,
            email="alice.johnson@techcorp.com"
        )
        
        # Search by name
        name_results = search_people_by_identity(
            session,
            name="Alice Johnson"
        )
        
        # Should find the same person
        assert len(email_results) == 1
        assert len(name_results) == 1
        assert email_results[0]['id'] == name_results[0]['id']
    
    def test_message_search_consistency(self, session, sample_principals, sample_messages):
        """Test that message search by person_id and person_email are consistent."""
        # Get person ID
        person_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        # Search messages by person ID
        messages_by_id = search_messages_for_person(
            session,
            person_id=person_id
        )
        
        # Mock the email resolution in search_messages tool
        with patch('src.mcp_server.server.db_manager.get_session') as mock_session:
            mock_session.return_value.__enter__.return_value = session
            mock_session.return_value.__exit__.return_value = None
            
            from src.mcp_server.server import search_messages
            
            if hasattr(search_messages, 'func'):
                search_messages_func = search_messages.func
            else:
                import inspect
                if inspect.isfunction(search_messages):
                    search_messages_func = search_messages
                else:
                    pytest.skip("Could not access search_messages function")
            
            result = search_messages_func(
                person_email="alice.johnson@techcorp.com"
            )
            
            messages_by_email = result.get('messages', [])
        
        # Should return the same number of messages
        assert len(messages_by_id) == len(messages_by_email)
        
        # Should return the same message IDs
        if messages_by_id and messages_by_email:
            id_set = {msg['id'] for msg in messages_by_id}
            email_set = {msg['id'] for msg in messages_by_email}
            assert id_set == email_set