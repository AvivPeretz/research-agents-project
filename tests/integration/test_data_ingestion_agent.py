"""Integration tests for DataIngestionAgent.

All Playwright browser calls are mocked so no real network requests are made.
All file system operations use tmp_path; no production directories are touched.
"""

import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import Config
from ingestion.data_ingestion_agent import DataIngestionAgent


# ─── Playwright mock helpers ─────────────────────────────────────────────────

def _make_playwright_mocks():
    """Return (mock_sync_playwright, mock_context, mock_page) ready to inject.

    The mock replicates the launch + new_context + new_page usage in DataIngestionAgent:
        with sync_playwright() as p:
            browser = p.chromium.launch(...)
            context = browser.new_context(...)
            page = context.new_page()
    """
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_context.pages = [mock_page]

    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context

    mock_p = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
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
        state_file = str(tmp_path / "state.json")
        downloads_dir = str(tmp_path / "downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        monkeypatch.setattr(Config, "OVERLEAF_DIR", downloads_dir)
        monkeypatch.setattr(Config, "OVERLEAF_STATE_PATH", Path(state_file))
        monkeypatch.setattr(Config, "SCHOLAR_STATE_PATH", Path(state_file))
        monkeypatch.setattr(Config, "PLAYWRIGHT_HEADLESS", True)
        monkeypatch.setattr(Config, "PLAYWRIGHT_TIMEOUT_MS", 30000)
        monkeypatch.setattr(Config, "OVERLEAF_EMAIL", "researcher@university.edu")

        mock_db = MagicMock()
        mock_db.get_last_modified.return_value = None  # every project is NEW by default

        a = DataIngestionAgent(db=mock_db, notifier=mock_notifier)
        # Attach test helpers as private attributes for convenience
        a._profile_dir = state_file  # state_file is checked with os.path.exists
        a._downloads_dir = downloads_dir
        a._mock_db = mock_db
        return a

    # ─── Helper ──────────────────────────────────────────────────────────────

    @staticmethod
    def _populate_profile(state_file: str) -> None:
        """Create a minimal Playwright storage-state JSON so session checks pass."""
        import json
        Path(state_file).write_text(json.dumps({"cookies": [], "origins": []}))

    # ─── Tests ───────────────────────────────────────────────────────────────

    def test_db_none_aborts_and_returns_empty_list(self, mock_notifier, monkeypatch):
        """sync_all_projects() returns [] immediately when db is None."""
        monkeypatch.setattr(Config, "OVERLEAF_DIR", "/tmp/_dia_test_downloads")
        a = DataIngestionAgent(db=None, notifier=mock_notifier)
        result = a.sync_all_projects()
        assert result == []

    def test_missing_profile_triggers_manual_login(self, agent):
        """When the session file is absent, the pre-run health check fails and the
        sync aborts cleanly with an admin alert — no interactive login is launched
        automatically, since this is an unattended scheduled run."""
        # profile_dir does NOT exist
        with patch.object(agent, "_perform_manual_login") as mock_login:
            result = agent.sync_all_projects()

        mock_login.assert_not_called()
        assert result == []
        agent.notifier.send_admin_alert.assert_called_once()
        assert "Overleaf" in str(agent.notifier.send_admin_alert.call_args)

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
        """Dashboard selector timeout triggers deletion of the state file."""
        self._populate_profile(agent._profile_dir)

        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        mock_page.wait_for_selector.side_effect = PlaywrightTimeoutError("selector timeout")
        mock_page.locator.return_value.all.return_value = []  # no rows to process

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"), \
             patch("ingestion.data_ingestion_agent.os.remove") as mock_remove:
            result = agent.sync_all_projects(_retry_depth=2)  # at max → no recursion

        mock_remove.assert_called_once_with(Path(agent._profile_dir))
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

        # Project 1: ZIP download raises on every attempt (including the built-in
        # download retries, so all of them must fail for the project to be skipped);
        # project 2: ZIP + PDF succeed on the first try.
        failing_zip_ctx = MagicMock()
        failing_zip_ctx.__enter__.side_effect = Exception("Simulated ZIP download failure for project 1")
        failing_zip_ctx.__exit__.return_value = False

        mock_page.expect_download.side_effect = [
            failing_zip_ctx, failing_zip_ctx, failing_zip_ctx,  # project 1 ZIP — raises every retry
            _make_download_ctx(_zip_creator),       # project 2 ZIP — succeeds
            _make_download_ctx(_pdf_creator),       # project 2 PDF — succeeds
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert "Successful Project" in result
        assert "Failing Project" not in result

    # TEST — FIX 4d: outer-loop exception (outside the per-project try) must be
    # logged via self.logger.error and alerted via self.notifier.send_admin_alert,
    # not silently swallowed by a bare `print`.
    def test_outer_loop_exception_logs_and_alerts_admin(self, agent, caplog):
        """An exception raised outside the per-project try (e.g. while pre-fetching
        known_last_modified via db.get_all_last_modified) must be reported through
        self.logger.error(...) and self.notifier.send_admin_alert(...), not just
        printed to stdout."""
        self._populate_profile(agent._profile_dir)

        row = _make_project_row("Some Project", "/project/sp1", "Some Project 1 day ago")
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        # get_all_last_modified is called once, right after the row-scan, and is
        # strictly outside the per-project try/except block further down.
        agent._mock_db.get_all_last_modified.side_effect = RuntimeError(
            "Simulated DB failure outside the per-project try"
        )

        import logging
        with caplog.at_level(logging.ERROR, logger="DataIngestionAgent"), \
             patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert result == []
        agent.notifier.send_admin_alert.assert_called_once()
        assert any("Simulated DB failure" in rec.message for rec in caplog.records)

    def test_zip_download_recovers_from_transient_failure(self, agent):
        """A ZIP download that fails once (e.g. a network blip) and succeeds on retry
        must not skip the project -- previously any single failure meant waiting for
        the next scheduled run, days later, even for a one-off glitch."""
        self._populate_profile(agent._profile_dir)
        agent._mock_db.get_last_modified.return_value = None

        row = _make_project_row("Flaky Project", "/project/flaky", "Flaky Project 1 day ago")
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        failing_zip_ctx = MagicMock()
        failing_zip_ctx.__enter__.side_effect = Exception("Transient network error")
        failing_zip_ctx.__exit__.return_value = False

        mock_page.expect_download.side_effect = [
            failing_zip_ctx,                          # first attempt — transient failure
            _make_download_ctx(_zip_creator),         # retry — succeeds
            _make_download_ctx(_pdf_creator),         # PDF — succeeds
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep") as mock_sleep:
            result = agent.sync_all_projects()

        assert "Flaky Project" in result
        mock_sleep.assert_called()  # backoff before the retry actually happened

    # ─── A1: pdf_sync_status / pdf-retry regression tests ───────────────────

    def test_pdf_fail_zip_succeeds_marks_failed_pdf_compile_and_alerts(self, agent):
        """PDF download fails but ZIP/source succeeds: pdf_sync_status must be set to
        FAILED_PDF_COMPILE, an admin alert must fire (subject mentions PDF Compile),
        and the ZIP-derived sync state must still be recorded (update_sync_registry /
        add_project still run) -- this is the partial-success half of the behavior."""
        self._populate_profile(agent._profile_dir)

        project_name = "PDF Compile Fail Project"
        modified_text = "PDF Compile Fail Project 1 day ago"
        agent._mock_db.get_last_modified.return_value = None
        agent._mock_db.get_pdf_alert_last_sent_at.return_value = None

        row = _make_project_row(project_name, "/project/pcf1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        failing_pdf_ctx = MagicMock()
        failing_pdf_ctx.__enter__.side_effect = Exception("Overleaf compile failure")
        failing_pdf_ctx.__exit__.return_value = False

        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            failing_pdf_ctx,
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        # Partial success: project still synced (ZIP was fine).
        assert project_name in result
        agent._mock_db.update_sync_registry.assert_called_once_with(project_name, modified_text)
        assert agent._mock_db.add_project.call_args[0][0] == project_name

        # pdf_sync_status ends up FAILED_PDF_COMPILE.
        update_state_calls = agent._mock_db.update_project_state.call_args_list
        matching = [
            c for c in update_state_calls
            if c.args and c.args[0] == project_name and c.kwargs.get("pdf_sync_status") == "FAILED_PDF_COMPILE"
        ]
        assert matching, f"Expected an update_project_state call with pdf_sync_status='FAILED_PDF_COMPILE', got {update_state_calls}"

        # Admin alert fired with a subject mentioning the PDF compile failure.
        agent.notifier.send_admin_alert.assert_called_once()
        _, kwargs = agent.notifier.send_admin_alert.call_args
        assert "PDF Compile" in kwargs["subject"]
        assert project_name in kwargs["subject"]

    # ─── A3: async-compile readiness wait (disabled "Download as PDF" item) ──
    #
    # NOTE: these two tests are SIMULATED/MOCKED verification only. They do not
    # exercise a real Overleaf session or Playwright's real polling loop inside
    # page.wait_for_function -- that loop runs client-side inside a single
    # Playwright call and is opaque to a MagicMock. What they DO verify is the
    # code path around that call: a call that resolves (representing Playwright
    # having detected the menu item's disabled state clear) leads to a normal
    # successful download, and a call that raises PlaywrightTimeoutError
    # (representing the ceiling being exhausted while the item stayed disabled)
    # is caught, logged distinctly, and still flows into the existing
    # pdf_ok=False / FAILED_PDF_COMPILE / admin-alert path unchanged.

    def test_pdf_ready_wait_resolves_then_download_succeeds(self, agent):
        """When wait_for_function resolves (menu item's disabled class cleared),
        the code proceeds to click and download normally: pdf_ok=True, no
        FAILED_PDF_COMPILE, no admin alert."""
        self._populate_profile(agent._profile_dir)

        project_name = "Large Manuscript Project"
        modified_text = "Large Manuscript Project 1 day ago"
        agent._mock_db.get_last_modified.return_value = None

        row = _make_project_row(project_name, "/project/lm1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        # wait_for_function simply resolves (default MagicMock behavior — no
        # exception raised), simulating Playwright detecting the disabled
        # class clearing before the ceiling.
        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            _make_download_ctx(_pdf_creator),
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert project_name in result
        mock_page.wait_for_function.assert_called_once()
        # Full success path: stanford_status READY_FOR_UPLOAD, pdf_sync_status cleared.
        update_state_calls = agent._mock_db.update_project_state.call_args_list
        matching = [
            c for c in update_state_calls
            if c.args and c.args[0] == project_name and c.kwargs.get("pdf_sync_status") is None
        ]
        assert matching, f"Expected pdf_sync_status cleared on success, got {update_state_calls}"
        agent.notifier.send_admin_alert.assert_not_called()

    def test_pdf_ready_wait_exhausts_ceiling_marks_failed_pdf_compile(self, agent):
        """When wait_for_function raises PlaywrightTimeoutError (item stayed
        disabled past the ceiling), the distinct timeout log message is printed
        and the existing pdf_ok=False / FAILED_PDF_COMPILE / admin-alert path
        still fires exactly as it does for any other PDF-download exception."""
        self._populate_profile(agent._profile_dir)

        project_name = "Slow Compile Project"
        modified_text = "Slow Compile Project 1 day ago"
        agent._mock_db.get_last_modified.return_value = None
        agent._mock_db.get_pdf_alert_last_sent_at.return_value = None

        row = _make_project_row(project_name, "/project/sc1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        mock_page.wait_for_function.side_effect = PlaywrightTimeoutError(
            "wait_for_function timeout"
        )
        mock_page.expect_download.side_effect = [_make_download_ctx(_zip_creator)]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        # Partial success: project still synced (ZIP was fine), but PDF failed.
        assert project_name in result
        update_state_calls = agent._mock_db.update_project_state.call_args_list
        matching = [
            c for c in update_state_calls
            if c.args and c.args[0] == project_name and c.kwargs.get("pdf_sync_status") == "FAILED_PDF_COMPILE"
        ]
        assert matching, f"Expected pdf_sync_status='FAILED_PDF_COMPILE', got {update_state_calls}"
        agent.notifier.send_admin_alert.assert_called_once()

        # expect_download was never reached — the click never happened because
        # the ready-wait raised first.
        mock_page.expect_download.assert_called_once()  # only the ZIP download

    def test_pdf_ready_wait_ceiling_exhaustion_is_logged_as_warning(self, agent, caplog):
        """The 'Download as PDF' ceiling-exhaustion case must reach the persistent
        log (self.logger.warning), not just stdout via print -- this is exactly the
        scenario a prior incident had no log evidence for, because this whole
        PDF-download code path previously used bare print() only."""
        self._populate_profile(agent._profile_dir)

        project_name = "Slow Compile Project"
        modified_text = "Slow Compile Project 1 day ago"
        agent._mock_db.get_last_modified.return_value = None
        agent._mock_db.get_pdf_alert_last_sent_at.return_value = None

        row = _make_project_row(project_name, "/project/sc1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        mock_page.wait_for_function.side_effect = PlaywrightTimeoutError(
            "wait_for_function timeout"
        )
        mock_page.expect_download.side_effect = [_make_download_ctx(_zip_creator)]

        import logging
        with caplog.at_level(logging.WARNING, logger="DataIngestionAgent"), \
             patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            agent.sync_all_projects()

        warning_records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "still disabled" in r.message and project_name in r.message
            for r in warning_records
        ), f"Expected a WARNING log record for the ceiling exhaustion, got: {[r.message for r in caplog.records]}"

    def test_pdf_retry_without_delta_change_is_still_attempted_and_clears_status_on_success(self, agent):
        """A project already flagged pdf_sync_status='FAILED_PDF_COMPILE', whose
        dashboard 'last modified' text is UNCHANGED from sync_registry (so it is
        neither NEW nor MODIFIED by the normal delta check), must still be attempted
        this run via the is_pdf_retry path. On a successful retry, pdf_sync_status
        must be cleared back to None/empty."""
        self._populate_profile(agent._profile_dir)

        project_name = "Stuck PDF Project"
        modified_text = "Stuck PDF Project 3 days ago"  # UNCHANGED text -> not new/modified

        # known_last_modified (from get_all_last_modified) matches the dashboard text
        # exactly, so is_new=False and is_modified=False for this project.
        agent._mock_db.get_all_last_modified.return_value = {project_name: modified_text}
        # But this project is flagged as pending a PDF retry.
        agent._mock_db.get_projects_with_pdf_pending.return_value = {project_name}

        row = _make_project_row(project_name, "/project/stuck1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        # Retry succeeds this time: ZIP + PDF both download fine.
        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            _make_download_ctx(_pdf_creator),
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        # The project WAS attempted (and synced) despite no delta change.
        assert project_name in result
        agent._mock_db.update_sync_registry.assert_called_once_with(project_name, modified_text)

        # pdf_sync_status is cleared back to None on the successful retry.
        update_state_calls = agent._mock_db.update_project_state.call_args_list
        matching = [
            c for c in update_state_calls
            if c.args and c.args[0] == project_name and c.kwargs.get("pdf_sync_status") is None
            and "pdf_sync_status" in c.kwargs
        ]
        assert matching, f"Expected an update_project_state call clearing pdf_sync_status, got {update_state_calls}"

    # ─── A2: pdf-compile-failing alert throttle tests ───────────────────────

    def test_pdf_compile_alert_throttled_within_window_then_resent_after_window(self, agent):
        """First detection sends the admin alert. An immediate second run within the
        24h throttle window must NOT re-send it. A run after the window has elapsed
        (simulated by moving the stored pdf_alert_last_sent_at timestamp into the
        past) DOES re-send it."""
        from datetime import datetime, timedelta

        self._populate_profile(agent._profile_dir)

        project_name = "Repeatedly Failing PDF Project"
        modified_text = "Repeatedly Failing PDF Project 5 days ago"
        agent._mock_db.get_last_modified.return_value = None  # NEW on first run

        def _make_failing_pdf_row():
            row = _make_project_row(project_name, "/project/rf1", modified_text)
            return row

        def _run_once():
            mock_pw, mock_context, mock_page = _make_playwright_mocks()
            _page_rows_locator(mock_page, [_make_failing_pdf_row()])
            failing_pdf_ctx = MagicMock()
            failing_pdf_ctx.__enter__.side_effect = Exception("Overleaf compile failure")
            failing_pdf_ctx.__exit__.return_value = False
            mock_page.expect_download.side_effect = [
                _make_download_ctx(_zip_creator),
                failing_pdf_ctx,
            ]
            with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
                 patch("ingestion.data_ingestion_agent.time.sleep"):
                return agent.sync_all_projects()

        # --- Run 1: first detection -> alert sent, timestamp "persisted" ---
        agent._mock_db.get_pdf_alert_last_sent_at.return_value = None
        _run_once()
        assert agent.notifier.send_admin_alert.call_count == 1

        persisted_ts = None
        for c in agent._mock_db.update_project_state.call_args_list:
            if c.args and c.args[0] == project_name and "pdf_alert_last_sent_at" in c.kwargs \
               and c.kwargs["pdf_alert_last_sent_at"] is not None:
                persisted_ts = c.kwargs["pdf_alert_last_sent_at"]
        assert persisted_ts is not None, "Expected pdf_alert_last_sent_at to be persisted after the first alert"

        # --- Run 2: retry (is_pdf_retry path), still failing, WITHIN the window ---
        agent._mock_db.get_all_last_modified.return_value = {project_name: modified_text}
        agent._mock_db.get_projects_with_pdf_pending.return_value = {project_name}
        agent._mock_db.get_pdf_alert_last_sent_at.return_value = persisted_ts  # "just sent"
        _run_once()
        assert agent.notifier.send_admin_alert.call_count == 1, "Alert must NOT be re-sent within the throttle window"

        # --- Run 3: same failure, but the stored timestamp is now > 24h old ---
        stale_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        agent._mock_db.get_pdf_alert_last_sent_at.return_value = stale_ts
        _run_once()
        assert agent.notifier.send_admin_alert.call_count == 2, "Alert MUST be re-sent once the throttle window has elapsed"

    def test_pdf_compile_alert_malformed_timestamp_still_alerts(self, agent):
        """A genuinely malformed/unparseable stored pdf_alert_last_sent_at value
        (e.g. corrupted DB data, not None) must still result in the alert being
        sent -- the throttle is designed to 'err toward alerting on any
        uncertainty', so a bad timestamp must never silently suppress the alert."""
        self._populate_profile(agent._profile_dir)

        project_name = "Malformed Timestamp Project"
        modified_text = "Malformed Timestamp Project 1 day ago"
        agent._mock_db.get_last_modified.return_value = None
        agent._mock_db.get_pdf_alert_last_sent_at.return_value = "not-a-real-timestamp"

        row = _make_project_row(project_name, "/project/malformed1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        failing_pdf_ctx = MagicMock()
        failing_pdf_ctx.__enter__.side_effect = Exception("Overleaf compile failure")
        failing_pdf_ctx.__exit__.return_value = False

        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            failing_pdf_ctx,
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        assert project_name in result
        agent.notifier.send_admin_alert.assert_called_once()

    def test_pdf_alert_db_read_failure_is_not_silently_swallowed(self, agent):
        """If self.db.get_pdf_alert_last_sent_at() itself raises (a genuine DB/
        connectivity problem, NOT a date-parsing issue), that failure must NOT be
        silently swallowed by the throttle's exception handling. It must propagate
        to the per-project `except Exception as e:` handler, which logs FAILURE via
        log_agent_run for that project -- not vanish with a false SUCCESS and no
        alert sent, which is the exact opposite of the throttle's 'err toward
        alerting on any uncertainty' design intent."""
        self._populate_profile(agent._profile_dir)

        project_name = "DB Read Failure Project"
        modified_text = "DB Read Failure Project 1 day ago"
        agent._mock_db.get_last_modified.return_value = None
        agent._mock_db.get_pdf_alert_last_sent_at.side_effect = RuntimeError(
            "simulated DB failure"
        )

        row = _make_project_row(project_name, "/project/dbfail1", modified_text)
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        _page_rows_locator(mock_page, [row])

        failing_pdf_ctx = MagicMock()
        failing_pdf_ctx.__enter__.side_effect = Exception("Overleaf compile failure")
        failing_pdf_ctx.__exit__.return_value = False

        mock_page.expect_download.side_effect = [
            _make_download_ctx(_zip_creator),
            failing_pdf_ctx,
        ]

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = agent.sync_all_projects()

        # The bug (unmodified code): the RuntimeError is silently swallowed by the
        # broad `except Exception: pass` around the whole throttle block, the
        # alert is never sent, and NO trace of the failure is logged anywhere --
        # the cycle reports a clean success for this project with a real,
        # unrelated infrastructure problem completely invisible.
        #
        # The fix: the DB-read failure is no longer caught by the narrowed
        # (ValueError, TypeError) handler around only the date-parsing step, so
        # it propagates out to the per-project `except Exception as e:` handler,
        # which logs FAILURE via log_agent_run for this project -- making the
        # failure visible instead of vanishing silently.
        agent.notifier.send_admin_alert.assert_not_called()

        failure_calls = [
            c for c in agent._mock_db.log_agent_run.call_args_list
            if c.kwargs.get("project_name") == project_name
            and c.kwargs.get("status") == "FAILURE"
        ]
        assert failure_calls, (
            "Expected the DB-read failure to surface as a logged FAILURE for "
            f"'{project_name}' via log_agent_run, got calls: "
            f"{agent._mock_db.log_agent_run.call_args_list}"
        )
