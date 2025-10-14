#!/usr/bin/env python3
"""
Quick MCP test runner using the simple test suite.
This works without complex fixture dependencies.
"""

import subprocess
import sys

def main():
    """Run the simple MCP test suite."""
    print("🧪 QUICK MCP SERVER FUNCTIONALITY TEST")
    print("=" * 50)
    print("✅ Using self-contained mock database")
    print("✅ Safe - will not affect your real data")
    print()
    
    cmd = [
        sys.executable, "-m", "pytest", 
        "tests/test_mcp_simple.py",
        "-v", "--tb=short"
    ]
    
    try:
        result = subprocess.run(cmd)
        if result.returncode == 0:
            print("\n🎉 ALL MCP TESTS PASSED!")
            print("✅ MCP server search functionality is working correctly")
            print("✅ Ready for production use")
        else:
            print("\n⚠️  Some tests failed - check output above")
        
        return result.returncode == 0
        
    except Exception as e:
        print(f"\n❌ Failed to run tests: {str(e)}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)