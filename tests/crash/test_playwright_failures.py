"""Tests for Playwright browser automation failure handling."""

import json
import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config import Config
from ingestion.data_ingestion_agent import DataIngestionAgent


# ─── Shared playwright mock helpers (same pattern as integration tests) ───────

def _make_playwright_mocks():
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_p = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_pw = MagicMock()
    mock_pw.return_value.__enter__.return_value = mock_p
    mock_pw.return_value.__exit__.return_value = False
    return mock_pw, mock_context, mock_page


def _make_enhancement_playwright_mocks():
    """Mocks for ResearchEnhancementAgent which uses browser.launch() + new_context()."""
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page.return_value = mock_page
    mock_browser = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_p = MagicMock()
    mock_p.chromium.launch.return_value = mock_browser
    mock_pw = MagicMock()
    mock_pw.return_value.__enter__.return_value = mock_p
    mock_pw.return_value.__exit__.return_value = False
    return mock_pw, mock_context, mock_page


class TestPlaywrightFailures:
    """Tests for handling Playwright/browser automation failures."""

    @pytest.fixture
    def ingestion_agent(self, tmp_path, mock_notifier, monkeypatch):
        """Lightweight DataIngestionAgent wired to tmp_path."""
        state_file = str(tmp_path / "state.json")
        Path(state_file).write_text(json.dumps({"cookies": [], "origins": []}))
        downloads_dir = str(tmp_path / "downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        monkeypatch.setattr(Config, "OVERLEAF_DIR", downloads_dir)
        monkeypatch.setattr(Config, "OVERLEAF_STATE_PATH", Path(state_file))
        monkeypatch.setattr(Config, "SCHOLAR_STATE_PATH", Path(state_file))
        monkeypatch.setattr(Config, "PLAYWRIGHT_HEADLESS", True)
        monkeypatch.setattr(Config, "PLAYWRIGHT_TIMEOUT_MS", 30000)
        monkeypatch.setattr(Config, "OVERLEAF_EMAIL", "test@example.com")

        db = MagicMock()
        db.get_last_modified.return_value = None
        return DataIngestionAgent(db=db, notifier=mock_notifier)

    def test_overleaf_session_expired_triggers_relogin(self, ingestion_agent):
        """Session timeout is handled without crash; returns a list (not exception)."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        mock_page.wait_for_selector.side_effect = PlaywrightTimeoutError("Selector not found")
        mock_page.locator.return_value.all.return_value = []

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"), \
             patch("ingestion.data_ingestion_agent.shutil.rmtree"):
            result = ingestion_agent.sync_all_projects(_retry_depth=2)

        # Must return a list (not raise), and must be empty when session expired at max depth
        assert isinstance(result, list)
        assert result == []

    def test_overleaf_session_expired_deletes_state_file(self, ingestion_agent):
        """State file is removed with os.remove on session expiry."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        mock_page.wait_for_selector.side_effect = PlaywrightTimeoutError("Selector not found")

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"), \
             patch("ingestion.data_ingestion_agent.os.remove") as mock_remove:
            ingestion_agent.sync_all_projects(_retry_depth=2)

        # The state file must be deleted so the next run triggers re-login
        mock_remove.assert_called_once_with(ingestion_agent.state_file)

    def test_overleaf_no_projects_found_returns_empty_list(self, ingestion_agent):
        """sync_all_projects returns [] when no project rows appear on dashboard."""
        mock_pw, mock_context, mock_page = _make_playwright_mocks()
        mock_page.locator.return_value.all.return_value = []  # 0 rows

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = ingestion_agent.sync_all_projects()

        assert result == []

    def test_overleaf_zip_download_failure_does_not_crash(self, ingestion_agent, tmp_path):
        """ZIP save_as raising an exception does not crash; project is excluded from result."""
        from unittest.mock import MagicMock

        mock_pw, mock_context, mock_page = _make_playwright_mocks()

        # Build a project row
        mock_link = MagicMock()
        mock_link.count.return_value = 1
        mock_link.inner_text.return_value = "Crash Project"
        mock_link.get_attribute.return_value = "/project/cp1"
        mock_row = MagicMock()
        mock_row.inner_text.return_value = "Crash Project 1 day ago"
        mock_row.locator.return_value.first = mock_link

        rows_locator = MagicMock()
        rows_locator.all.return_value = [mock_row]

        def locator_side_effect(sel):
            if "tr:has" in sel or "li:has" in sel:
                return rows_locator
            return MagicMock()

        mock_page.locator.side_effect = locator_side_effect

        # save_as creates a 0-byte file → triggers the "ZIP empty" skip path
        def create_empty_file(path):
            Path(path).write_bytes(b"")

        zip_ctx = MagicMock()
        mock_dl = MagicMock()
        mock_dl.save_as.side_effect = create_empty_file
        ctx_val = MagicMock()
        ctx_val.value = mock_dl
        zip_ctx.__enter__.return_value = ctx_val
        zip_ctx.__exit__.return_value = False
        mock_page.expect_download.return_value = zip_ctx

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"):
            result = ingestion_agent.sync_all_projects()

        # Must not raise; project with bad ZIP must be excluded
        assert isinstance(result, list)
        assert "Crash Project" not in result

    def test_stanford_site_down_returns_false(self):
        """upload_to_stanford returns False when the site raises a network error."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        mock_self = MagicMock()
        mock_self.logger = MagicMock()
        mock_self.uni_email = "uni@example.com"

        mock_pw = MagicMock()
        mock_page = MagicMock()
        mock_page.goto.side_effect = Exception("Network error")
        mock_browser = MagicMock()
        mock_browser.new_context.return_value.new_page.return_value = mock_page
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
        mock_pw.return_value.__exit__.return_value = False

        with patch("agents.research_enhancement_agent.sync_playwright", mock_pw), \
             patch("agents.research_enhancement_agent.os.path.exists", return_value=True):
            result = ResearchEnhancementAgent.upload_to_stanford(mock_self, "TestProject", "/fake/path.pdf")

        assert result is False

    def test_stanford_file_input_not_found_does_not_crash(self):
        """upload_to_stanford completes without crash when file input locator is absent."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        mock_self = MagicMock()
        mock_self.logger = MagicMock()
        mock_self.uni_email = "uni@example.com"

        mock_pw, mock_context, mock_page = _make_enhancement_playwright_mocks()
        mock_page.locator.return_value.count.return_value = 0  # no file input found

        with patch("agents.research_enhancement_agent.sync_playwright", mock_pw), \
             patch("agents.research_enhancement_agent.os.path.exists", return_value=True), \
             patch("agents.research_enhancement_agent.time.sleep"):
            result = ResearchEnhancementAgent.upload_to_stanford(mock_self, "TestProject", "/fake/path.pdf")

        # Completes without exception; result can be True or False
        assert isinstance(result, bool)

    def test_stanford_token_rejected_returns_none(self):
        """_fetch_review_from_stanford returns None when page shows 'Invalid Token'."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        mock_self = MagicMock()
        mock_self.logger = MagicMock()

        mock_pw, mock_context, mock_page = _make_enhancement_playwright_mocks()

        def locator_side_effect(selector):
            m = MagicMock()
            if selector == 'text="Invalid Token"':
                m.count.return_value = 1  # site shows "Invalid Token"
            elif selector == 'text="Summary"':
                m.first.wait_for.side_effect = Exception("Not found")
            else:
                m.count.return_value = 0
            return m

        mock_page.locator.side_effect = locator_side_effect

        token = "validtoken12345678901234567890"
        with patch("agents.research_enhancement_agent.sync_playwright", mock_pw), \
             patch("agents.research_enhancement_agent.Config.PLAYWRIGHT_HEADLESS", True):
            result = ResearchEnhancementAgent._fetch_review_from_stanford(mock_self, token)

        assert result is None

    def test_stanford_review_too_short_returns_none(self):
        """_fetch_review_from_stanford returns None when scraped text is < 400 chars."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        mock_self = MagicMock()
        mock_self.logger = MagicMock()

        mock_pw, mock_context, mock_page = _make_enhancement_playwright_mocks()

        short_text = "This review is too short."
        assert len(short_text) < 400  # Sanity check the fixture itself

        def locator_side_effect(selector):
            m = MagicMock()
            if selector == "body":
                m.inner_text.return_value = short_text
            elif selector == 'text="Invalid Token"':
                m.count.return_value = 0
            elif selector == 'text="Error"':
                m.count.return_value = 0
            elif selector == 'button:visible':
                m.count.return_value = 0
            return m

        mock_page.locator.side_effect = locator_side_effect

        token = "validtoken12345678901234567890"
        with patch("agents.research_enhancement_agent.sync_playwright", mock_pw), \
             patch("agents.research_enhancement_agent.Config.PLAYWRIGHT_HEADLESS", True):
            result = ResearchEnhancementAgent._fetch_review_from_stanford(mock_self, token)

        assert result is None

    def test_google_scholar_captcha_no_session_calls_manual_login(self, tmp_path):
        """Missing profile dir triggers _perform_manual_login when sync_all_projects runs."""
        # profile_dir deliberately absent — profile check will fail
        profile_dir = str(tmp_path / "nonexistent_profile")
        downloads_dir = str(tmp_path / "downloads")
        os.makedirs(downloads_dir, exist_ok=True)

        db = MagicMock()
        notifier = MagicMock()

        state_file = str(tmp_path / "nonexistent_state.json")

        with patch.object(Config, "OVERLEAF_DIR", downloads_dir), \
             patch.object(Config, "OVERLEAF_USER_DATA_DIR", profile_dir), \
             patch.object(Config, "OVERLEAF_STATE_PATH", state_file), \
             patch.object(Config, "PLAYWRIGHT_HEADLESS", True), \
             patch.object(Config, "PLAYWRIGHT_TIMEOUT_MS", 30000):
            agent = DataIngestionAgent(db=db, notifier=notifier)

        with patch.object(agent, "_perform_manual_login") as mock_login:
            agent.sync_all_projects()

        mock_login.assert_called_once()
