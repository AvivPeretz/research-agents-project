#!/usr/bin/env python3
"""
Standalone CLI test runner for the research agents test suite.

Usage:
    python tests/run_tests.py --suite unit --agent literature --verbose --coverage

Requirements:
    pip install pytest pytest-cov
"""

import argparse
import sys
from pathlib import Path

import pytest


def build_pytest_args(args):
    """
    Build pytest command-line arguments based on CLI options.

    Args:
        args: Parsed command-line arguments

    Returns:
        List of pytest arguments
    """
    pytest_args = []

    # Add test directory based on suite selection
    if args.suite == "all":
        pytest_args.append(str(Path(__file__).parent))
    elif args.suite == "unit":
        pytest_args.append(str(Path(__file__).parent / "unit"))
    elif args.suite == "integration":
        pytest_args.append(str(Path(__file__).parent / "integration"))
    elif args.suite == "db":
        pytest_args.append(str(Path(__file__).parent / "db"))
    elif args.suite == "idempotency":
        pytest_args.append(str(Path(__file__).parent / "idempotency"))
    elif args.suite == "crash":
        pytest_args.append(str(Path(__file__).parent / "crash"))
    elif args.suite == "stress":
        pytest_args.append(str(Path(__file__).parent / "stress"))

    # Filter by agent if specified (but not "all")
    if args.agent != "all":
        agent_map = {
            "literature": "literature",
            "progress": "progress",
            "enhancement": "enhancement",
            "supervisor": "supervisor",
            "notification": "notification",
        }

        pattern = agent_map.get(args.agent, args.agent)
        pytest_args.extend(["-k", f"{pattern}"])

    # Add verbose flag
    if args.verbose:
        pytest_args.append("-v")

    # Add coverage flag
    if args.coverage:
        pytest_args.extend(["--cov=.", "--cov-report=html", "--cov-report=term-missing"])

    return pytest_args


def print_header(args):
    """Print a header showing what will be tested."""
    print("\n" + "=" * 70)
    print("TEST SUITE RUNNER - Academic Research Multi-Agent System")
    print("=" * 70)
    print(f"Suite:    {args.suite.upper()}")
    print(f"Agent:    {args.agent.upper()}")
    print(f"Verbose:  {args.verbose}")
    print(f"Coverage: {args.coverage}")
    print("=" * 70 + "\n")


def main():
    """Main entry point for the test runner."""
    parser = argparse.ArgumentParser(
        description="Run test suites for the research agents project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tests/run_tests.py --suite unit --verbose
  python tests/run_tests.py --suite crash --agent playwright --coverage
  python tests/run_tests.py --suite all --agent literature
        """,
    )

    parser.add_argument(
        "--suite",
        choices=["unit", "integration", "db", "idempotency", "crash", "stress", "all"],
        default="all",
        help="Which test suite to run (default: all)",
    )

    parser.add_argument(
        "--agent",
        choices=["literature", "progress", "enhancement", "supervisor", "notification", "all"],
        default="all",
        help="Which agent to test (default: all)",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose pytest output (-v)",
    )

    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Enable coverage report (--cov)",
    )

    args = parser.parse_args()

    # Print header
    print_header(args)

    # Build pytest arguments
    pytest_args = build_pytest_args(args)

    # Run pytest
    print(f"Running: pytest {' '.join(pytest_args)}\n")
    exit_code = pytest.main(pytest_args)

    # Print summary
    print("\n" + "=" * 70)
    if exit_code == 0:
        print("✓ ALL TESTS PASSED")
    else:
        print("✗ SOME TESTS FAILED")
    print("=" * 70 + "\n")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
