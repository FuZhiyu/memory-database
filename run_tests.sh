#!/bin/bash
# Test runner script for Messages RAG System

set -e  # Exit on error

echo "Messages RAG System - Test Runner"
echo "=================================="
echo ""

# Parse command line arguments
TEST_TYPE=${1:-all}
COVERAGE=${2:-no}

case $TEST_TYPE in
  unit)
    echo "Running unit tests..."
    if [ "$COVERAGE" = "coverage" ]; then
      uv run pytest tests/unit/ -m unit --cov=src --cov-report=term-missing
    else
      uv run pytest tests/unit/ -m unit
    fi
    ;;
    
  integration)
    echo "Running integration tests..."
    if [ "$COVERAGE" = "coverage" ]; then
      uv run pytest tests/integration/ -m integration --cov=src --cov-report=term-missing
    else
      uv run pytest tests/integration/ -m integration
    fi
    ;;
    
  sample)
    echo "Running tests with sample database..."
    uv run pytest tests/integration/ -m requires_sample
    ;;
    
  all)
    echo "Running all tests..."
    if [ "$COVERAGE" = "coverage" ]; then
      uv run pytest --cov=src --cov-report=html --cov-report=term-missing
      echo ""
      echo "Coverage report generated in htmlcov/index.html"
    else
      uv run pytest
    fi
    ;;
    
  *)
    echo "Usage: $0 [unit|integration|sample|all] [coverage]"
    echo ""
    echo "Examples:"
    echo "  $0              # Run all tests"
    echo "  $0 unit         # Run only unit tests"
    echo "  $0 integration  # Run only integration tests"
    echo "  $0 sample       # Run tests requiring sample database"
    echo "  $0 all coverage # Run all tests with coverage report"
    exit 1
    ;;
esac

echo ""
echo "Test run completed!"