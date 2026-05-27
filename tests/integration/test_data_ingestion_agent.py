"""Integration tests for DataIngestionAgent.

All Playwright browser calls are mocked so no real network requests are made.
All file system operations use tmp_path; no production directories are touched.
"""

import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config import Config
from ingestion.data_ingestion_agent import DataIngestionAgent


# ─── Playwright mock helpers ─────────────────────────────────────────────────

def _make_playwright_mocks():
    """Return (mock_sync_playwright, mock_context, mock_page) ready to inject.

    The mock replicates the launch_persistent_context usage in DataIngestionAgent:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(...)
            page = context.pages[0]
    """
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.pages = [mock_page]

    mock_p = MagicMock()
    mock_p.chromium.launch_persistent_context.return_value = mock_context

    mock_pw = MagicMock()
    mock_pw.return_value.__enter__.return_value = mock_p
    mock_pw.return_value.__exit__.return_value = False

    return mock_pw, mock_context, mock_page


def _make_project_row(project_name: str, href: str, modified_text: str) -> MagicMock:
    """Return a mock Playwright locator row as found on the Overleaf dashboard."""
    mock_link = MagicMock()
    mock_link.count.return_value = 1
    mock_link.inner_text.return_value = project_name
    mock_link.get_attribute.return_value = href

    mock_row = MagicMock()
    mock_row.inner_text.return_value = modified_text
    mock_row.locator.return_value.first = mock_link

    return mock_row


def _make_download_ctx(save_fn=None) -> MagicMock:
    """Return a context manager mock for page.expect_download().

    If save_fn is provided, it is called as the side_effect of download.save_as(path).
    """
    mock_dl = MagicMock()
    if save_fn:
        mock_dl.save_as.side_effect = save_fn

    ctx_value = MagicMock()
    ctx_value.value = mock_dl

    ctx = MagicMock()
    ctx.__enter__.return_value = ctx_value
    ctx.__exit__.return_value = False
    return ctx


def _zip_creator(path: str) -> None:
    """Create a minimal valid ZIP at *path* so the extraction step succeeds."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("main.tex", r"\documentclass{article}\begin{document}Hello\end{document}")


def _pdf_creator(path: str) -> None:
    """Create a non-empty fake PDF at *path* so the size check passes."""
    Path(path).write_bytes(b"%PDF-1.4 fake content for testing")


def _page_rows_locator(mock_page: MagicMock, rows: list) -> None:
    """Wire mock_page.locator so the row-selector returns *rows* from .all()."""
    rows_locator = MagicMock()
    rows_locator.all.return_value = rows

    original_locator = MagicMock()

    def locator_side_effect(selector):
        if "tr:has" in selector or "li:has" in selector:
            return rows_locator
        return MagicMock()

    mock_page.locator.side_effect = locator_side_effect


# ─── Fixture ─────────────────────────────────────────────────────────────────

class TestDataIngestionAgent:
    """Integration tests for DataIngestionAgent sync behaviour."""

    @pytest.fixture
    def agent(self, tmp_path, mock_notifier, monkeypatch):
        """DataIngestionAgent pointing at tmp_path; DB is a MagicMock."""
        profile_dir = str(tmp_path / "profile")
        downloads_dir = str(tmp_path / "downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        monkeypatch.setattr(Config, "OVERLEAF_DIR", downloads_dir)
        monkeypatch.setattr(Config, "OVERLEAF_USER_DATA_DIR", profile_dir)
        monkeypatch.setattr(Config, "PLAYWRIGHT_HEADLESS", True)
        monkeypatch.setattr(Config, "PLAYWRIGHT_TIMEOUT_MS", 30000)
        monkeypatch.setattr(Config, "OVERLEAF_EMAIL", "researcher@university.edu")

        mock_db = MagicMock()
        mock_db.get_last_modified.return_value = None  # every project is NEW by default

        a = DataIngestionAgent(db=mock_db, notifier=mock_notifier)
        # Attach test helpers as private attributes for convenience
        a._profile_dir = profile_dir
        a._downloads_dir = downloads_dir
        a._mock_db = mock_db
        return a

    # ─── Helper ──────────────────────────────────────────────────────────────

    @staticmethod
    def _populate_profile(profile_dir: str) -> None:
        """Create a non-empty Chrome profile dir so session checks pass."""
        os.makedirs(profile_dir, exist_ok=True)
        (Path(profile_dir) / "Cookies").write_bytes(b"fake session data")

    # ─── Tests ───────────────────────────────────────────────────────────────

    def test_db_none_aborts_and_returns_empty_list(self, mock_notifier, monkeypatch):
        """sync_all_projects() returns [] immediately when db is None."""
        monkeypatch.setattr(Config, "OVERLEAF_DIR", "/tmp/_dia_test_downloads")
        monkeypatch.setattr(Config, "OVERLEAF_USER_DATA_DIR", "/tmp/_dia_test_profile")
        a = DataIngestionAgent(db=None, notifier=mock_notifier)
        result = a.sync_all_projects()
        assert result == []

    def test_missing_profile_triggers_manual_login(self, agent):
        """When user_data_dir is absent, _perform_manual_login() is called once."""
        # profile_dir does NOT exist
        with patch.object(agent, "_perform_manual_login") as mock_login:
            result = agent.sync_all_projects()

        mock_login.assert_called_once()
        # Profile is still absent after mock login → should abort
        assert result == []

    def test_profile_still_absent_after_login_aborts(self, agent):
        """If profile remains missing after _perform_manual_login, return []."""
        # Patching _perform_manual_login to no-op; profile is never created
        with patch.object(agent, "_perform_manual_login"):
            result = agent.sync_all_projects()
        assert result == []

    def test_zero_projects_on_dashboard_returns_empty_list(self, agent):
        """Dashboard with 0 project rows returns [] without error."""
        self._populate_profile(agent._profile_dir)

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        mock_page.locator.return_value.all.return_value = []

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert result == []

    def test_happy_path_new_project_synced_zip_and_pdf(self, agent):
        """NEW project: ZIP extracted, PDF saved, DB updated, name returned."""
        self._populate_profile(agent._profile_dir)

        project_name = "Quantum Computing Thesis"
        modified_text = "Quantum Computing Thesis 3 days ago"
        agent._mock_db.get_last_modified.return_value = None  # NEW project

        row = _make_project_row(project_name, "/project/qc123", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            _make_download_ctx(_pdf_creator),
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert project_name in result
        agent._mock_db.update_sync_registry.assert_called_once_with(project_name, modified_text)
        # add_project is called with (project_name, email) — check project_name
        assert agent._mock_db.add_project.call_args[0][0] == project_name

    def test_happy_path_two_new_projects_both_synced(self, agent):
        """Two NEW projects both appear in the returned list."""
        self._populate_profile(agent._profile_dir)

        rows = [
            _make_project_row("Paper Alpha", "/project/a1", "Paper Alpha 1 day ago"),
            _make_project_row("Paper Beta", "/project/b2", "Paper Beta 2 days ago"),
        ]
        agent._mock_db.get_last_modified.return_value = None

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, rows)

        # 2 projects × (1 ZIP + 1 PDF) = 4 download contexts
        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            _make_download_ctx(_pdf_creator),
            _make_download_ctx(_zip_creator),
            _make_download_ctx(_pdf_creator),
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert "Paper Alpha" in result
        assert "Paper Beta" in result
        assert agent._mock_db.update_sync_registry.call_count == 2

    def test_project_up_to_date_is_skipped(self, agent):
        """Project whose last_modified text matches DB is skipped; DB not updated."""
        self._populate_profile(agent._profile_dir)

        modified_text = "Stable Project 5 days ago"
        agent._mock_db.get_last_modified.return_value = modified_text  # same → no change

        row = _make_project_row("Stable Project", "/project/s1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert result == []
        agent._mock_db.update_sync_registry.assert_not_called()
        agent._mock_db.add_project.assert_not_called()

    def test_session_expired_deletes_stale_profile(self, agent):
        """Dashboard selector timeout triggers shutil.rmtree on the profile dir."""
        self._populate_profile(agent._profile_dir)

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        mock_page.wait_for_selector.side_effect = PlaywrightTimeoutError("selector timeout")
        mock_page.locator.return_value.all.return_value = []  # no rows to process

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"), \
             patch("ingestion.data_ingestion_agent.shutil.rmtree") as mock_rmtree:
            result = agent.sync_all_projects(_retry_depth=2)  # at max → no recursion

        mock_rmtree.assert_called_once_with(agent._profile_dir)
        assert result == []

    def test_max_retry_depth_sends_admin_alert_and_returns_empty(self, agent):
        """At _retry_depth == MAX_RETRY_DEPTH, send_admin_alert is called and [] returned."""
        self._populate_profile(agent._profile_dir)

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        mock_page.wait_for_selector.side_effect = PlaywrightTimeoutError("timeout")
        mock_page.locator.return_value.all.return_value = []

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"), \
             patch("ingestion.data_ingestion_agent.shutil.rmtree"):
            result = agent.sync_all_projects(_retry_depth=2)

        assert result == []
        agent.notifier.send_admin_alert.assert_called_once()
        # The subject should mention Overleaf or Sync failure
        call_str = str(agent.notifier.send_admin_alert.call_args)
        assert any(kw in call_str for kw in ("Overleaf", "Sync", "Max", "Retries", "Failed"))

    def test_empty_zip_after_download_skips_project(self, agent):
        """Project skipped when downloaded ZIP is 0 bytes; DB not updated."""
        self._populate_profile(agent._profile_dir)

        project_name = "Empty Zip Project"
        modified_text = "Empty Zip Project yesterday"
        agent._mock_db.get_last_modified.return_value = None

        row = _make_project_row(project_name, "/project/ez1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        def create_empty_zip(path: str) -> None:
            Path(path).write_bytes(b"")  # 0-byte file triggers skip

        mock_page.expect_download.side_effect = [_make_download_ctx(create_empty_zip)]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert project_name not in result
        agent._mock_db.update_sync_registry.assert_not_called()

    def test_pdf_download_failure_project_still_synced(self, agent):
        """PDF exception is caught; ZIP was saved, so project IS returned as synced."""
        self._populate_profile(agent._profile_dir)

        project_name = "PDF Fail Project"
        modified_text = "PDF Fail Project 1 day ago"
        agent._mock_db.get_last_modified.return_value = None

        row = _make_project_row(project_name, "/project/pf1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        # ZIP succeeds; PDF expect_download raises
        failing_pdf_ctx = MagicMock()
        failing_pdf_ctx.__enter__.side_effect = Exception("Network timeout during PDF download")
        failing_pdf_ctx.__exit__.return_value = False

        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            failing_pdf_ctx,
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        # Project IS synced (ZIP was fine) — PDF failure is logged and absorbed
        assert project_name in result
        agent._mock_db.update_sync_registry.assert_called_once_with(project_name, modified_text)

    def test_log_agent_run_called_with_started_and_success(self, agent):
        """log_agent_run is invoked with status=STARTED and status=SUCCESS per project."""
        self._populate_profile(agent._profile_dir)

        project_name = "Logging Test Project"
        modified_text = "Logging Test Project 1 day ago"
        agent._mock_db.get_last_modified.return_value = None

        row = _make_project_row(project_name, "/project/lt1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            _make_download_ctx(_pdf_creator),
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            agent.sync_all_projects()

        all_log_calls = str(agent._mock_db.log_agent_run.call_args_list)
        assert "STARTED" in all_log_calls
        assert "SUCCESS" in all_log_calls

    def test_perform_manual_login_sends_admin_alert(self, agent):
        """_perform_manual_login() notifies admin before opening the browser."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        # Simulate login page timeout — the alert still fires before wait_for_url
        mock_page.wait_for_url.side_effect = PlaywrightTimeoutError("login timeout")

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            agent._perform_manual_login()

        agent.notifier.send_admin_alert.assert_called_once()
        call_str = str(agent.notifier.send_admin_alert.call_args)
        assert "Overleaf" in call_str

    # TEST B — FIX 3: per-project isolation — project 1 failure must not abort project 2
    def test_per_project_failure_does_not_abort_remaining_projects(self, agent):
        """Project 1 raises a mid-loop exception; project 2 must still be synced and returned."""
        self._populate_profile(agent._profile_dir)

        agent._mock_db.get_last_modified.return_value = None  # both are NEW

        row1 = _make_project_row("Failing Project", "/project/fail1", "Failing Project 1 day ago")
        row2 = _make_project_row("Successful Project", "/project/ok2", "Successful Project 2 days ago")

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row1, row2])

        # Project 1: ZIP download raises immediately; project 2: ZIP + PDF succeed
        failing_zip_ctx = MagicMock()
        failing_zip_ctx.__enter__.side_effect = Exception("Simulated ZIP download failure for project 1")
        failing_zip_ctx.__exit__.return_value = False

        mock_page.expect_download.side_effect = [
            failing_zip_ctx,                        # project 1 ZIP — raises
            _make_download_ctx(_zip_creator),       # project 2 ZIP — succeeds
            _make_download_ctx(_pdf_creator),       # project 2 PDF — succeeds
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert "Successful Project" in result
        assert "Failing Project" not in result
