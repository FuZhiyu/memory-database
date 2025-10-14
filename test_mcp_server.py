#!/usr/bin/env python3
"""
Test script for MCP server functionality.
"""

import sys
sys.path.insert(0, '.')

from memory_database.mcp_server.queries import search_people_by_identity, search_messages_for_person, find_person_by_any_identity
from memory_database.database.connection import DatabaseManager, DatabaseSettings

def test_search_person():
    """Test person search functionality"""
    print("=== Testing Person Search ===")
    
    db_manager = DatabaseManager(DatabaseSettings())
    
    with db_manager.get_session() as session:
        # Test email search
        print("\n1. Searching by email:")
        result = search_people_by_identity(
            session, 
            email='christopher.s.carpenter@vanderbilt.edu'
        )
        print(f"Found {len(result)} people")
        if result:
            person = result[0]
            print(f"Name: {person['display_name']}")
            print(f"ID: {person['id']}")
            print(f"Identity types: {list(person['identities'].keys())}")
        
        # Test name search
        print("\n2. Searching by name (fuzzy):")
        result = search_people_by_identity(
            session, 
            name="Christopher",
            fuzzy_match=True,
            limit=3
        )
        print(f"Found {len(result)} people with 'Christopher' in name")
        for person in result:
            print(f"  - {person['display_name']}")

def test_search_messages():
    """Test message search functionality"""
    print("\n=== Testing Message Search ===")
    
    db_manager = DatabaseManager(DatabaseSettings())
    
    with db_manager.get_session() as session:
        # First find a person
        person_id = find_person_by_any_identity(
            session,
            person_email='christopher.s.carpenter@vanderbilt.edu'
        )
        
        if person_id:
            print(f"\nFound person ID: {person_id}")
            
            # Search messages for this person
            messages = search_messages_for_person(
                session,
                person_id=person_id,
                limit=3
            )
            
            print(f"Found {len(messages)} messages")
            for msg in messages:
                print(f"  - {msg['sent_at']}: {msg['content'][:50]}...")
                print(f"    Sender: {msg['sender']['display_name'] if msg['sender'] else 'Unknown'}")
                print(f"    Platform: {msg['thread']['channel']['platform']}")
        else:
            print("Could not find person")

if __name__ == "__main__":
    try:
        test_search_person()
        test_search_messages()
        print("\n✅ All tests completed successfully!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()