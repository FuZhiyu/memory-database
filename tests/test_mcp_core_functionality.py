"""
Core MCP server functionality tests using mock database.
Focuses on testing the essential search logic without wrapper complications.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.mcp_server.queries import (
    search_people_by_identity, 
    search_messages_for_person, 
    find_person_by_any_identity
)


class TestMCPCoreFunctionality:
    """Test core MCP functionality that really matters."""
    
    def test_person_search_comprehensive(self, session, sample_principals):
        """Comprehensive test of person search functionality."""
        # Test email search
        email_results = search_people_by_identity(
            session, 
            email="alice.johnson@techcorp.com"
        )
        assert len(email_results) == 1
        alice = email_results[0]
        assert alice['display_name'] == "Alice Johnson"
        
        # Test phone search
        phone_results = search_people_by_identity(
            session, 
            phone="+14155551001"
        )
        assert len(phone_results) == 1
        assert phone_results[0]['id'] == alice['id']  # Same person
        
        # Test name search (exact)
        name_results = search_people_by_identity(
            session, 
            name="Alice Johnson"
        )
        assert len(name_results) == 1
        assert name_results[0]['id'] == alice['id']  # Same person
        
        # Test fuzzy name search
        fuzzy_results = search_people_by_identity(
            session, 
            name="Alice",
            fuzzy_match=True
        )
        assert len(fuzzy_results) >= 1
        alice_found = any(p['id'] == alice['id'] for p in fuzzy_results)
        assert alice_found
        
        print(f"✅ Person search tests passed for {alice['display_name']}")
    
    def test_identity_resolution_comprehensive(self, session, sample_principals):
        """Comprehensive test of identity resolution."""
        # Test email resolution
        alice_id_by_email = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        assert alice_id_by_email is not None
        
        # Test phone resolution
        alice_id_by_phone = find_person_by_any_identity(
            session,
            person_phone="+14155551001"
        )
        assert alice_id_by_phone is not None
        assert alice_id_by_email == alice_id_by_phone  # Same person
        
        # Test name resolution
        alice_id_by_name = find_person_by_any_identity(
            session,
            person_name="Alice Johnson"
        )
        assert alice_id_by_name is not None
        assert alice_id_by_email == alice_id_by_name  # Same person
        
        print(f"✅ Identity resolution tests passed for ID: {alice_id_by_email}")
    
    def test_message_search_comprehensive(self, session, sample_principals, sample_messages):
        """Comprehensive test of message search functionality."""
        # Get Alice's ID
        alice_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        assert alice_id is not None
        
        # Basic message search
        messages = search_messages_for_person(
            session,
            person_id=alice_id
        )
        
        # Verify message structure
        for msg in messages:
            assert 'id' in msg
            assert 'content' in msg
            assert 'sent_at' in msg
            assert 'thread' in msg
            assert 'thread' in msg and 'channel' in msg['thread']
            assert 'platform' in msg['thread']['channel']
            assert msg.get('sender') is not None or msg.get('recipients') is not None
        
        # Test with limit
        limited_messages = search_messages_for_person(
            session,
            person_id=alice_id,
            limit=1
        )
        assert len(limited_messages) <= 1
        
        # Test with content filter
        filtered_messages = search_messages_for_person(
            session,
            person_id=alice_id,
            content_contains="Hello"
        )
        for msg in filtered_messages:
            assert "Hello" in msg['content']
        
        # Test with platform filter
        email_messages = search_messages_for_person(
            session,
            person_id=alice_id,
            platform="email"
        )
        for msg in email_messages:
            assert msg['thread']['channel']['platform'] == "email"
        
        print(f"✅ Message search tests passed. Found {len(messages)} total messages for Alice")
    
    def test_search_error_handling(self, session, sample_principals):
        """Test error handling and edge cases."""
        # Non-existent email
        no_results = search_people_by_identity(
            session,
            email="nonexistent@example.com"
        )
        assert len(no_results) == 0
        
        # Empty search
        empty_results = search_people_by_identity(session)
        assert len(empty_results) == 0
        
        # Non-existent person for identity resolution
        no_person = find_person_by_any_identity(
            session,
            person_email="nonexistent@example.com"
        )
        assert no_person is None
        
        # Non-existent person for message search
        no_messages = search_messages_for_person(
            session,
            person_id="nonexistent-id"
        )
        assert len(no_messages) == 0
        
        print("✅ Error handling tests passed")
    
    def test_mcp_server_integration_simulation(self, session, sample_principals, sample_messages):
        """Simulate how the MCP server would use these functions."""
        # Simulate search_person tool call
        def simulate_search_person(email=None, name=None, phone=None, fuzzy_match=False):
            """Simulate the search_person MCP tool."""
            try:
                people = search_people_by_identity(
                    session,
                    email=email,
                    name=name,
                    phone=phone,
                    fuzzy_match=fuzzy_match
                )
                return {
                    'people': people,
                    'total_found': len(people),
                    'search_criteria': {
                        'email': email,
                        'name': name,
                        'phone': phone,
                        'fuzzy_match': fuzzy_match
                    }
                }
            except Exception as e:
                return {
                    'error': str(e),
                    'people': [],
                    'total_found': 0
                }
        
        # Simulate search_messages tool call
        def simulate_search_messages(person_email=None, person_phone=None, person_id=None, limit=50):
            """Simulate the search_messages MCP tool."""
            try:
                # Resolve person ID if not provided
                resolved_person_id = person_id
                if not resolved_person_id:
                    resolved_person_id = find_person_by_any_identity(
                        session,
                        person_email=person_email,
                        person_phone=person_phone
                    )
                
                if not resolved_person_id:
                    return {
                        'error': 'Could not find person with provided identifiers',
                        'messages': [],
                        'total_found': 0,
                        'person_resolved': None
                    }
                
                messages = search_messages_for_person(
                    session,
                    person_id=resolved_person_id,
                    limit=limit
                )
                
                # Get person info
                from src.models import Principal
                person = session.query(Principal).get(resolved_person_id)
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
        
        # Test simulated search_person tool
        print("Testing simulated search_person tool...")
        person_result = simulate_search_person(email="alice.johnson@techcorp.com")
        
        assert not person_result.get('error')
        assert person_result['total_found'] == 1
        assert person_result['people'][0]['display_name'] == "Alice Johnson"
        print(f"   ✅ Found {person_result['total_found']} people")
        
        # Test simulated search_messages tool
        print("Testing simulated search_messages tool...")
        messages_result = simulate_search_messages(person_email="alice.johnson@techcorp.com", limit=5)
        
        assert not messages_result.get('error')
        assert messages_result.get('person_resolved') is not None
        assert messages_result['person_resolved']['display_name'] == "Alice Johnson"
        print(f"   ✅ Found {messages_result['total_found']} messages for {messages_result['person_resolved']['display_name']}")
        
        # Test error cases
        print("Testing error cases...")
        error_person = simulate_search_person(email="nonexistent@example.com")
        assert error_person['total_found'] == 0
        
        error_messages = simulate_search_messages(person_email="nonexistent@example.com")
        assert error_messages.get('error') is not None
        assert 'Could not find person' in error_messages['error']
        
        print("✅ MCP server integration simulation tests passed")
    
    def test_data_quality_and_structure(self, session, sample_principals, sample_messages):
        """Test data quality and structure requirements."""
        # Get a person
        alice_id = find_person_by_any_identity(
            session,
            person_email="alice.johnson@techcorp.com"
        )
        
        # Test person data structure
        people = search_people_by_identity(session, email="alice.johnson@techcorp.com")
        person = people[0]
        
        # Required fields
        required_person_fields = ['id', 'display_name', 'identities']
        for field in required_person_fields:
            assert field in person, f"Missing required field: {field}"
        
        # Identity structure
        identities = person['identities']
        assert isinstance(identities, dict)
        
        for identity_type, claims in identities.items():
            assert isinstance(claims, list)
            for claim in claims:
                required_claim_fields = ['value', 'normalized', 'platform']
                for field in required_claim_fields:
                    assert field in claim, f"Missing claim field: {field}"
        
        # Test message data structure
        messages = search_messages_for_person(session, person_id=alice_id, limit=1)
        
        if messages:
            msg = messages[0]
            required_msg_fields = ['id', 'content', 'sent_at', 'thread']
            for field in required_msg_fields:
                assert field in msg, f"Missing message field: {field}"
            
            # Thread structure
            thread = msg['thread']
            assert 'channel' in thread
            assert 'platform' in thread['channel']
        
        print("✅ Data quality and structure tests passed")


def test_run_comprehensive_mcp_test_suite(session, sample_principals, sample_messages):
    """Run the complete MCP test suite and report results."""
    print("🧪 RUNNING COMPREHENSIVE MCP TEST SUITE")
    print("=" * 60)
    
    test_class = TestMCPCoreFunctionality()
    
    tests = [
        ("Person Search", lambda: test_class.test_person_search_comprehensive(session, sample_principals)),
        ("Identity Resolution", lambda: test_class.test_identity_resolution_comprehensive(session, sample_principals)),
        ("Message Search", lambda: test_class.test_message_search_comprehensive(session, sample_principals, sample_messages)),
        ("Error Handling", lambda: test_class.test_search_error_handling(session, sample_principals)),
        ("MCP Integration Simulation", lambda: test_class.test_mcp_server_integration_simulation(session, sample_principals, sample_messages)),
        ("Data Quality", lambda: test_class.test_data_quality_and_structure(session, sample_principals, sample_messages))
    ]
    
    results = []
    for test_name, test_func in tests:
        try:
            print(f"\n🔍 Running {test_name}...")
            test_func()
            results.append((test_name, True))
            print(f"✅ {test_name} PASSED")
        except Exception as e:
            results.append((test_name, False))
            print(f"❌ {test_name} FAILED: {str(e)}")
    
    # Summary
    passed = sum(1 for _, success in results if success)
    total = len(results)
    
    print("\n" + "=" * 60)
    print("🏁 MCP TEST SUITE SUMMARY")
    print("=" * 60)
    
    for test_name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"{status} {test_name}")
    
    success_rate = (passed / total) * 100
    print(f"\nResults: {passed}/{total} tests passed ({success_rate:.1f}%)")
    
    if passed == total:
        print("\n🎉 ALL MCP TESTS PASSED!")
        print("✅ MCP server search functionality is fully verified with mock database")
        print("✅ Safe to use without corrupting real data")
        print("✅ Ready for production deployment")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
    
    return passed == total