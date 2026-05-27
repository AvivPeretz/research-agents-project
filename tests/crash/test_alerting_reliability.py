"""Tests for alerting system reliability."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config import Config


class TestAlertingReliability:
    """Tests for alert notification system resilience."""

    def test_overleaf_login_required_calls_admin_alert(self, mock_notifier, tmp_path, monkeypatch):
        """When max retry depth is hit, send_admin_alert is called with 'Overleaf' in subject."""
        from ingestion.data_ingestion_agent import DataIngestionAgent
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        profile_dir = str(tmp_path / "profile")
        downloads_dir = str(tmp_path / "downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        os.makedirs(profile_dir, exist_ok=True)
        (Path(profile_dir) / "Cookies").write_bytes(b"session")

        monkeypatch.setattr(Config, "OVERLEAF_DIR", downloads_dir)
        monkeypatch.setattr(Config, "OVERLEAF_USER_DATA_DIR", profile_dir)
        monkeypatch.setattr(Config, "PLAYWRIGHT_HEADLESS", True)
        monkeypatch.setattr(Config, "PLAYWRIGHT_TIMEOUT_MS", 30000)

        agent = DataIngestionAgent(db=MagicMock(), notifier=mock_notifier)

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.pages = [mock_page]
        mock_p = MagicMock()
        mock_p.chromium.launch_persistent_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.return_value.__enter__.return_value = mock_p
        mock_pw.return_value.__exit__.return_value = False
        mock_page.wait_for_selector.side_effect = PlaywrightTimeoutError("timeout")
        mock_page.locator.return_value.all.return_value = []

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"), \
             patch("ingestion.data_ingestion_agent.shutil.rmtree"):
            agent.sync_all_projects(_retry_depth=2)  # at max depth → triggers alert

        mock_notifier.send_admin_alert.assert_called_once()
        call_str = str(mock_notifier.send_admin_alert.call_args)
        assert "Overleaf" in call_str

    def test_admin_alert_subject_contains_overleaf(self, mock_notifier, tmp_path, monkeypatch):
        """The admin alert subject sent on max retry contains 'Overleaf'."""
        from ingestion.data_ingestion_agent import DataIngestionAgent
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        profile_dir = str(tmp_path / "profile")
        downloads_dir = str(tmp_path / "downloads")
        os.makedirs(downloads_dir, exist_ok=True)
        os.makedirs(profile_dir, exist_ok=True)
        (Path(profile_dir) / "Cookies").write_bytes(b"session")

        monkeypatch.setattr(Config, "OVERLEAF_DIR", downloads_dir)
        monkeypatch.setattr(Config, "OVERLEAF_USER_DATA_DIR", profile_dir)
        monkeypatch.setattr(Config, "PLAYWRIGHT_HEADLESS", True)
        monkeypatch.setattr(Config, "PLAYWRIGHT_TIMEOUT_MS", 30000)

        agent = DataIngestionAgent(db=MagicMock(), notifier=mock_notifier)

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.pages = [mock_page]
        mock_p = MagicMock()
        mock_p.chromium.launch_persistent_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.return_value.__enter__.return_value = mock_p
        mock_pw.return_value.__exit__.return_value = False
        mock_page.wait_for_selector.side_effect = PlaywrightTimeoutError("timeout")
        mock_page.locator.return_value.all.return_value = []

        with patch("ingestion.data_ingestion_agent.sync_playwright", mock_pw), \
             patch("ingestion.data_ingestion_agent.time.sleep"), \
             patch("ingestion.data_ingestion_agent.shutil.rmtree"):
            agent.sync_all_projects(_retry_depth=2)

        # The subject kwarg must contain "Overleaf"
        call_kwargs = mock_notifier.send_admin_alert.call_args
        subject = (call_kwargs.kwargs or call_kwargs[1]).get("subject", "")
        assert "Overleaf" in subject

    def test_smtp_failure_logs_error_does_not_crash(self, monkeypatch):
        """_dispatch_email returns False when SMTP raises; never propagates the exception."""
        from agents.notification_agent import NotificationAgent
        from email.message import EmailMessage

        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_EMAIL", "sender@gmail.com")
        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_PASSWORD", "testpassword")

        agent = NotificationAgent(db=None)

        msg = EmailMessage()
        msg["Subject"] = "Test"
        msg["From"] = "sender@gmail.com"
        msg["To"] = "recipient@example.com"
        msg.set_content("Test body")

        with patch("smtplib.SMTP_SSL", side_effect=ConnectionRefusedError("SMTP down")), \
             patch("agents.notification_agent.time.sleep"):
            result = agent._dispatch_email(msg, "recipient@example.com")

        # Must return False (not raise)
        assert result is False

    def test_all_llm_providers_fail_does_not_silently_swallow_error(self):
        """ask_llm raises RuntimeError when all providers are exhausted — never silently None."""
        from agents.base_agent import BaseAgent

        # Build a stub agent bypassing __init__ (which needs real API keys)
        class _Stub(BaseAgent):
            def run(self): pass

        agent = object.__new__(_Stub)
        agent.agent_name = "StubAgent"
        agent.logger = MagicMock()
        agent.groq_client = MagicMock()
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")
        agent.gemini_available = False
        agent.openai_available = False

        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("test prompt")

    def test_run_agent_safely_logs_failure_message(self, capsys):
        """run_agent_safely returns False and prints a failure message when agent.run() raises."""
        from main import run_agent_safely

        failing_agent = MagicMock()
        failing_agent.__class__.__name__ = "FailingAgent"
        failing_agent.run.side_effect = Exception("Test failure from agent")

        result = run_agent_safely(failing_agent)

        assert result is False
        printed = capsys.readouterr().out
        assert "FailingAgent" in printed or "failed" in printed.lower()

    def test_send_admin_alert_on_smtp_failure_is_not_recursive(self, monkeypatch):
        """send_admin_alert does not attempt to send another alert if SMTP fails."""
        from agents.notification_agent import NotificationAgent

        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_EMAIL", "sender@gmail.com")
        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_PASSWORD", "testpassword")

        agent = NotificationAgent(db=None)

        with patch.object(agent, "_dispatch_email", return_value=False) as mock_dispatch, \
             patch("agents.notification_agent.time.sleep"):
            # send_admin_alert eventually calls _dispatch_email; if that returns False,
            # it must NOT call send_admin_alert again (no recursion)
            agent.send_admin_alert(
                subject="SMTP failure test",
                message="The email system failed."
            )

        # _dispatch_email called a bounded number of times (not infinitely)
        assert mock_dispatch.call_count <= 1
