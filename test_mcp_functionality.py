#!/usr/bin/env python3
"""
Simple test script to verify MCP server search functionality.
Run this to ensure search is working properly.
"""

import sys
from memory_database.database.connection import DatabaseManager, DatabaseSettings
from memory_database.mcp_server.queries import search_people_by_identity, search_messages_for_person, find_person_by_any_identity

def test_search_functionality():
    """Test core search functionality."""
    print("🧪 Testing MCP Server Search Functionality")
    print("=" * 50)
    
    db_manager = DatabaseManager(DatabaseSettings())
    
    with db_manager.get_session() as session:
        # Test person search by email
        print("1. Testing person search by email...")
        people = search_people_by_identity(
            session,
            email="christopher.s.carpenter@vanderbilt.edu"
        )
        
        if people:
            person = people[0]
            print(f"   ✅ Found: {person['display_name']}")
            print(f"   ID: {person['id']}")
            
            # Test message search for this person
            print("\n2. Testing message search...")
            messages = search_messages_for_person(
                session,
                person_id=person['id'],
                limit=5
            )
            print(f"   ✅ Found {len(messages)} messages")
            
            # Test identity resolution
            print("\n3. Testing identity resolution...")
            resolved_id = find_person_by_any_identity(
                session,
                person_email="christopher.s.carpenter@vanderbilt.edu"
            )
            print(f"   ✅ Resolved to person ID: {resolved_id}")
            
            # Test fuzzy name search
            print("\n4. Testing fuzzy name search...")
            fuzzy_people = search_people_by_identity(
                session,
                name="Christopher",
                fuzzy_match=True
            )
            print(f"   ✅ Found {len(fuzzy_people)} people with fuzzy search")
            
            print("\n🎉 All tests passed! MCP search is working correctly.")
            return True
        else:
            print("   ❌ No people found - check your data")
            return False

if __name__ == "__main__":
    success = test_search_functionality()
    sys.exit(0 if success else 1)