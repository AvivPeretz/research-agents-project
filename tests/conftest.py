"""Shared pytest fixtures and configuration for all test suites."""

import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from utils.database_manager import DatabaseManager


@pytest.fixture
def db_in_memory(tmp_path, monkeypatch):
    """
    Fixture: creates a fresh in-memory DatabaseManager using SQLite ':memory:'.
    Patches Config.LIBRARY_DIR to use tmp_path for any file operations.
    Yields the DatabaseManager instance and tears down after test.
    """
    from config import Config

    # Patch Config.LIBRARY_DIR to use a temporary directory
    monkeypatch.setattr(Config, "LIBRARY_DIR", str(tmp_path))

    # Create database in the patched tmp_path directory
    db = DatabaseManager()

    yield db

    # Cleanup (connection will be closed automatically)


@pytest.fixture
def mock_notifier():
    """
    Fixture: returns a MagicMock that mimics NotificationAgent with mocked methods.
    Methods: send_literature_update, send_progress_feedback, send_stanford_tasks,
             send_supervisor_report, send_admin_alert, get_researcher_email.
    """
    notifier = MagicMock()
    notifier.send_literature_update = MagicMock(return_value=True)
    notifier.send_progress_feedback = MagicMock(return_value=True)
    notifier.send_stanford_tasks = MagicMock(return_value=True)
    notifier.send_supervisor_report = MagicMock(return_value=True)
    notifier.send_admin_alert = MagicMock(return_value=True)
    notifier.get_researcher_email = MagicMock(return_value="test@example.com")

    return notifier


@pytest.fixture
def mock_llm_response():
    """
    Fixture: patches BaseAgent.ask_llm to return a configurable string.
    Returns a patcher that can be used to configure the response.
    """
    from agents.base_agent import BaseAgent
    from tests.fixtures.mock_responses import VALID_LITERATURE_JSON

    with patch.object(BaseAgent, "ask_llm", return_value=VALID_LITERATURE_JSON) as mock:
        yield mock


@pytest.fixture
def sample_tex_content():
    """
    Fixture: returns a hardcoded LaTeX string with all required elements:
    - \\documentclass, \\usepackage, \\textbf{}, \\maketitle, comments (%), real paragraphs.
    """
    return r"""
\documentclass[12pt]{article}
\usepackage{amsmath}
\usepackage{graphicx}
% This is a comment that should be removed
\title{Test Paper}
\author{Test Author}

\begin{document}
\maketitle

This is a real paragraph about machine learning and neural networks.
The field has experienced tremendous growth over the past decade.
\textbf{Deep learning} has revolutionized computer vision and natural language processing.

\textit{Another important concept} is the application of transfer learning.
Pre-trained models can be fine-tuned for specific tasks with limited data.
This approach has enabled practical AI applications across numerous domains.

The future of machine learning holds exciting possibilities for scientific discovery.
Researchers continue to develop novel architectures and training techniques.
\textit{Reinforcement learning} and unsupervised methods remain active areas of investigation.

\end{document}
"""


@pytest.fixture
def sample_project_name():
    """Fixture: returns the string 'Test_Research_Project'."""
    return "Test_Research_Project"


@pytest.fixture
def temp_project_dir(tmp_path, sample_tex_content):
    """
    Fixture: creates a real temporary directory with a main.tex file.
    Uses sample_tex_content fixture.
    Returns the path to the directory.
    """
    main_tex_path = tmp_path / "main.tex"
    main_tex_path.write_text(sample_tex_content)

    return tmp_path


@pytest.fixture
def patch_config_validate():
    """
    Fixture: patches Config.validate() to not raise during tests.
    Use this when Config initialization would otherwise fail.
    """
    from config import Config

    with patch.object(Config, "validate", return_value=None):
        yield
