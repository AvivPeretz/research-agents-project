"""Tests for email (SMTP) failure handling."""

import smtplib
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from config import Config


class TestEmailFailures:
    """Tests for handling email service failures.

    Note: ResearchEnhancementAgent no longer polls IMAP for the Stanford review
    token — the token is captured directly from the upload confirmation page
    (see tests/crash/test_playwright_failures.py) since paperreview.ai itself
    warns that email delivery of the token is unreliable. IMAP-specific test
    coverage for that removed code path was removed accordingly; the SMTP
    notification tests below are unaffected and remain.
    """

    def test_smtp_connection_failure_returns_false(self, monkeypatch):
        """_dispatch_email returns False when SMTP server is unreachable."""
        from agents.notification_agent import NotificationAgent

        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_EMAIL", "sender@gmail.com")
        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_PASSWORD", "testpassword")

        agent = NotificationAgent(db=None)
        msg = EmailMessage()
        msg["Subject"] = "Test"
        msg["From"] = "sender@gmail.com"
        msg["To"] = "recipient@example.com"
        msg.set_content("body")

        with patch("smtplib.SMTP_SSL", side_effect=ConnectionRefusedError("SMTP unavailable")), \
             patch("agents.notification_agent.time.sleep"):
            result = agent._dispatch_email(msg, "recipient@example.com")

        assert result is False

    def test_smtp_login_failure_returns_false(self, monkeypatch):
        """_dispatch_email returns False when SMTP login raises SMTPAuthenticationError."""
        from agents.notification_agent import NotificationAgent

        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_EMAIL", "sender@gmail.com")
        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_PASSWORD", "wrongpassword")

        agent = NotificationAgent(db=None)
        msg = EmailMessage()
        msg["Subject"] = "Test"
        msg["From"] = "sender@gmail.com"
        msg["To"] = "recipient@example.com"
        msg.set_content("body")

        with patch("smtplib.SMTP_SSL") as mock_smtp_cls, \
             patch("agents.notification_agent.time.sleep"):
            mock_smtp_instance = MagicMock()
            mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp_instance)
            mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_smtp_instance.login.side_effect = smtplib.SMTPAuthenticationError(535, "Bad credentials")

            result = agent._dispatch_email(msg, "recipient@example.com")

        assert result is False

    def test_missing_sender_credentials_returns_false(self, monkeypatch):
        """_dispatch_email returns False immediately when sender credentials are absent."""
        from agents.notification_agent import NotificationAgent

        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_EMAIL", None)
        monkeypatch.setattr(Config, "NOTIFICATION_SENDER_PASSWORD", None)

        agent = NotificationAgent(db=None)
        msg = EmailMessage()
        msg["Subject"] = "Test"
        msg["From"] = "none@none.com"
        msg["To"] = "recipient@example.com"
        msg.set_content("body")

        result = agent._dispatch_email(msg, "recipient@example.com")

        assert result is False
