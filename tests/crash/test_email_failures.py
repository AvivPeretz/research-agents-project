"""Tests for email (IMAP/SMTP) failure handling."""

import imaplib
import smtplib
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest

from config import Config


class TestEmailFailures:
    """Tests for handling email service failures."""

    def test_imap_auth_failure_returns_none_token(self):
        """IMAP login failure causes _get_stanford_token_from_email to return None."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        mock_self = MagicMock()
        mock_self.dummy_email = "dummy@example.com"
        mock_self.dummy_password = "password"
        mock_self.logger = MagicMock()

        with patch("agents.research_enhancement_agent.imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_imap_cls.return_value.login.side_effect = imaplib.IMAP4.error("Authentication failed")
            result = ResearchEnhancementAgent._get_stanford_token_from_email(mock_self, "Test_Project")

        assert result is None

    def test_imap_connection_error_returns_none_token(self):
        """IMAP connection error causes _get_stanford_token_from_email to return None."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        mock_self = MagicMock()
        mock_self.dummy_email = "dummy@example.com"
        mock_self.dummy_password = "password"
        mock_self.logger = MagicMock()

        with patch("agents.research_enhancement_agent.imaplib.IMAP4_SSL",
                   side_effect=ConnectionRefusedError("Cannot connect to IMAP")):
            result = ResearchEnhancementAgent._get_stanford_token_from_email(mock_self, "Test_Project")

        assert result is None

    def test_token_regex_no_match_returns_none(self):
        """Email body without token pattern causes _get_stanford_token_from_email to return None."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent
        import email as email_module

        mock_self = MagicMock()
        mock_self.dummy_email = "dummy@example.com"
        mock_self.dummy_password = "password"
        mock_self.logger = MagicMock()

        # Build a real email message with NO token pattern
        raw_msg = email_module.message_from_bytes(
            b"Subject: Test_Project review\r\nFrom: paperreview@example.com\r\n\r\n"
            b"Thank you for your submission. No token provided here."
        )
        raw_bytes = raw_msg.as_bytes()

        with patch("agents.research_enhancement_agent.imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_mail = MagicMock()
            mock_imap_cls.return_value = mock_mail
            mock_mail.search.return_value = ("OK", [b"1"])
            mock_mail.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", raw_bytes)])
            mock_mail.logout.return_value = None

            result = ResearchEnhancementAgent._get_stanford_token_from_email(mock_self, "test_project")

        # No "Your Access Token:" pattern → returns None
        assert result is None

    def test_token_subject_mismatch_skips_email(self):
        """Email whose subject does not contain the project name is skipped."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent
        import email as email_module

        mock_self = MagicMock()
        mock_self.dummy_email = "dummy@example.com"
        mock_self.dummy_password = "password"
        mock_self.logger = MagicMock()

        # Email subject does NOT mention the project
        raw_msg = email_module.message_from_bytes(
            b"Subject: Unrelated Newsletter\r\nFrom: news@example.com\r\n\r\n"
            b"Your Access Token: ABCDEFGHIJKLMNOPQRST"
        )
        raw_bytes = raw_msg.as_bytes()

        with patch("agents.research_enhancement_agent.imaplib.IMAP4_SSL") as mock_imap_cls:
            mock_mail = MagicMock()
            mock_imap_cls.return_value = mock_mail
            mock_mail.search.return_value = ("OK", [b"1"])
            mock_mail.fetch.return_value = ("OK", [(b"1 (RFC822 {100})", raw_bytes)])
            mock_mail.logout.return_value = None

            result = ResearchEnhancementAgent._get_stanford_token_from_email(
                mock_self, "My_Research_Project"
            )

        # Subject mismatch → token not extracted → None
        assert result is None

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
