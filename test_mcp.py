#!/usr/bin/env python3
"""
MCP Server Test Runner - Choose between real data testing and mock database testing.
"""

import subprocess
import sys
import argparse

def run_real_data_tests():
    """Run MCP tests with real database (read-only, safe)."""
    print("🎯 Running MCP Tests with Real Data")
    print("=" * 50)
    print("✅ Read-only testing - will not modify your database")
    print()
    
    cmd = [sys.executable, "test_mcp_real_data.py"]
    
    try:
        result = subprocess.run(cmd)
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Failed to run real data tests: {str(e)}")
        return False

def run_functionality_tests():
    """Run basic functionality verification."""
    print("🧪 Running Basic MCP Functionality Tests")
    print("=" * 50)
    print("✅ Safe testing with real database (read-only)")
    print()
    
    cmd = [sys.executable, "test_mcp_functionality.py"]
    
    try:
        result = subprocess.run(cmd)
        return result.returncode == 0
    except Exception as e:
        print(f"❌ Failed to run functionality tests: {str(e)}")
        return False

def show_help():
    """Show available test options."""
    print("🧪 MCP Server Test Options")
    print("=" * 40)
    print()
    print("Available test modes:")
    print("  --real-data    Test with real database (read-only, recommended)")
    print("  --basic        Quick functionality verification")
    print("  --help         Show this help message")
    print()
    print("Examples:")
    print("  python test_mcp.py --real-data    # Comprehensive real data tests")
    print("  python test_mcp.py --basic        # Quick functionality check")
    print("  python test_mcp.py                # Default: real data tests")

def main():
    """Main test runner."""
    parser = argparse.ArgumentParser(description="MCP Server Test Runner")
    parser.add_argument("--real-data", action="store_true", help="Test with real database (read-only)")
    parser.add_argument("--basic", action="store_true", help="Quick functionality verification")
    parser.add_argument("--help-tests", action="store_true", help="Show test options")
    
    args = parser.parse_args()
    
    if args.help_tests:
        show_help()
        return True
    
    if args.basic:
        return run_functionality_tests()
    else:
        # Default to real data tests (most comprehensive)
        return run_real_data_tests()

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)