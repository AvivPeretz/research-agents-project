"""
QUICK START GUIDE - Test Suite Execution

This document provides quick commands to get started with the test suite.
"""

# INSTALLATION
# ============

# Install test dependencies (pytest and coverage)
pip install pytest pytest-cov


# QUICK START EXAMPLES
# ====================

# Run ALL tests (comprehensive)
pytest tests/

# Run all tests with verbose output
pytest tests/ -v

# Run specific test suite
pytest tests/unit/               # Unit tests only
pytest tests/integration/        # Integration tests only
pytest tests/db/                 # Database tests only
pytest tests/crash/              # Crash/resilience tests
pytest tests/stress/             # Stress tests

# Run specific test file
pytest tests/unit/test_schemas.py

# Run specific test function
pytest tests/unit/test_schemas.py::TestPaperDataSchema::test_paper_data_valid_full

# Run tests matching a pattern
pytest -k "literature" tests/     # All tests with "literature" in name
pytest -k "database" tests/       # All tests with "database" in name

# Run with coverage report (generates HTML report)
pytest tests/ --cov=. --cov-report=html
# View report at: htmlcov/index.html

# Run with less verbose output (just dots)
pytest tests/ -q

# Stop on first failure
pytest tests/ -x

# Show print statements
pytest tests/ -s

# Run only last failed tests
pytest tests/ --lf

# Show the slowest 10 tests
pytest tests/ --durations=10


# USING THE CLI TEST RUNNER
# ==========================

# Run specific suite
python tests/run_tests.py --suite unit
python tests/run_tests.py --suite crash
python tests/run_tests.py --suite stress

# Filter by agent
python tests/run_tests.py --agent literature
python tests/run_tests.py --agent progress
python tests/run_tests.py --suite integration --agent literature

# Verbose output
python tests/run_tests.py --suite unit --verbose

# With coverage
python tests/run_tests.py --suite all --coverage

# Combination example
python tests/run_tests.py --suite crash --agent playwright --verbose --coverage


# COMMON WORKFLOWS
# ================

# 1. Quick sanity check (fast)
pytest tests/unit/ -q

# 2. Test a specific component (e.g., schemas)
pytest tests/unit/test_schemas.py -v

# 3. Test all literature agent functionality
pytest -k "literature" -v

# 4. Full test with coverage (slow)
pytest tests/ --cov=. --cov-report=html -v

# 5. Test suite by suite
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest tests/db/ -v
pytest tests/crash/ -v
pytest tests/stress/ -v
pytest tests/idempotency/ -v

# 6. Continuous testing (requires pytest-watch)
pip install pytest-watch
ptw tests/

# 7. Debug a failing test
pytest tests/unit/test_schemas.py::TestPaperDataSchema::test_paper_data_invalid_complexity -vv -s


# TEST COUNTS BY SUITE
# ====================

Unit Tests:        47 tests
Integration Tests: 45 tests
Database Tests:    23 tests
Idempotency Tests: 6 tests
Crash Tests:       61 tests
Stress Tests:      7 tests
─────────────────────────
TOTAL:            189 tests


# EXPECTED OUTPUT
# ===============

Example success run:
    $ pytest tests/unit/ -q
    .................................
    47 passed in 2.34s

Example with verbose:
    $ pytest tests/unit/test_schemas.py -v
    tests/unit/test_schemas.py::TestPaperDataSchema::test_paper_data_valid_full PASSED
    tests/unit/test_schemas.py::TestPaperDataSchema::test_paper_data_defaults_to_na PASSED
    ...
    14 passed in 1.52s

Example with coverage:
    $ pytest tests/ --cov=. -q
    ....................................................................
    189 passed in 45.23s
    Coverage HTML report: htmlcov/index.html
    Name                                      Stmts   Miss  Cover
    ────────────────────────────────────────────────────────────
    agents/base_agent.py                         45      8    82%
    ...


# TROUBLESHOOTING
# ================

Issue: "pytest: command not found"
Fix: pip install pytest

Issue: "ModuleNotFoundError: No module named 'pytest'"
Fix: pip install pytest pytest-cov

Issue: Tests fail with import errors
Fix: Make sure you're running from the project root directory

Issue: Coverage report is missing
Fix: Run with --cov flag: pytest tests/ --cov=. --cov-report=html

Issue: Tests are slow
Fix: Run only specific suites instead of all
     pytest tests/unit/ -q (quick)
     or use -x to stop on first failure

Issue: Fixture errors
Fix: Fixtures are in conftest.py and automatically discovered
     Ensure you're running pytest from the project root


# FIXTURE REFERENCE
# =================

In your tests, you can use these fixtures:

@pytest.fixture
def db_in_memory():
    """In-memory SQLite database for testing"""
    # Example: db_in_memory.add_project("Test")

@pytest.fixture
def mock_notifier():
    """Mocked NotificationAgent"""
    # Example: mock_notifier.send_literature_update.assert_called()

@pytest.fixture
def sample_project_name():
    """Returns 'Test_Research_Project'"""

@pytest.fixture
def sample_tex_content():
    """LaTeX document with all required elements"""

@pytest.fixture
def temp_project_dir(tmp_path):
    """Temporary directory with main.tex file"""


# WRITING NEW TESTS
# =================

Example test structure:

import pytest
from utils.library_manager import LibraryManager

class TestLibraryManager:
    """Tests for LibraryManager file operations."""
    
    def test_save_creates_file(self, tmp_path):
        """Asserts that save_literature_summary creates a file."""
        manager = LibraryManager(base_dir=str(tmp_path))
        manager.save_literature_summary("Project", "Content")
        
        files = list(tmp_path.glob("**/literature_summary.md"))
        assert len(files) > 0


# MORE HELP
# =========

# Pytest documentation
pytest -h
pytest --help

# Show fixtures available
pytest --fixtures

# Show all test names without running
pytest tests/ --collect-only

# Run with detailed failure output
pytest tests/ -vv


# FINAL NOTES
# ===========

✓ All tests are isolated (no side effects)
✓ All external services are mocked
✓ Tests run fast (< 1 minute for all suites)
✓ No modifications to project code needed for testing
✓ Coverage reports available via --cov
✓ Tests are CI/CD ready

Ready to test! 🚀
"""
