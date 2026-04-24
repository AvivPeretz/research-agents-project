"""Unit tests for Config validation and environment variables."""

from pathlib import Path
from unittest.mock import patch

import pytest

from config import Config


class TestConfigValidation:
    """Tests for Config.validate() and environment variable checking."""

    def test_validate_passes_with_all_keys_present(self):
        """Asserts that Config.validate() does not raise when all required env vars are present."""
        env_vars = {
            "GROQ_API_KEY": "test_key_123",
            "NOTIFICATION_SENDER_EMAIL": "sender@example.com",
            "NOTIFICATION_SENDER_PASSWORD": "pass123",
            "OVERLEAF_EMAIL": "overleaf@example.com",
            "OVERLEAF_PASSWORD": "overleaf_pass",
        }
        with patch.dict("os.environ", env_vars, clear=False):
            # Should not raise
            Config.validate()

    def test_validate_raises_when_groq_key_missing(self):
        """Asserts that ValueError is raised with key name when GROQ_API_KEY is missing."""
        env_vars = {
            "NOTIFICATION_SENDER_EMAIL": "sender@example.com",
            "NOTIFICATION_SENDER_PASSWORD": "pass123",
            "OVERLEAF_EMAIL": "overleaf@example.com",
            "OVERLEAF_PASSWORD": "overleaf_pass",
        }
        with patch.dict("os.environ", env_vars, clear=True):
            with pytest.raises(ValueError) as exc_info:
                Config.validate()
            assert "GROQ_API_KEY" in str(exc_info.value)

    def test_validate_raises_when_sender_email_missing(self):
        """Asserts that ValueError is raised when NOTIFICATION_SENDER_EMAIL is missing."""
        env_vars = {
            "GROQ_API_KEY": "test_key",
            "NOTIFICATION_SENDER_PASSWORD": "pass123",
            "OVERLEAF_EMAIL": "overleaf@example.com",
            "OVERLEAF_PASSWORD": "overleaf_pass",
        }
        with patch.dict("os.environ", env_vars, clear=True):
            with pytest.raises(ValueError) as exc_info:
                Config.validate()
            assert "NOTIFICATION_SENDER_EMAIL" in str(exc_info.value)

    def test_validate_raises_when_multiple_keys_missing(self):
        """Asserts that all 3 missing key names appear in the ValueError message."""
        env_vars = {
            "OVERLEAF_EMAIL": "overleaf@example.com",
        }
        with patch.dict("os.environ", env_vars, clear=True):
            with pytest.raises(ValueError) as exc_info:
                Config.validate()
            error_message = str(exc_info.value)
            assert "GROQ_API_KEY" in error_message
            assert "NOTIFICATION_SENDER_EMAIL" in error_message
            assert "OVERLEAF_PASSWORD" in error_message

    def test_validate_raises_when_value_is_empty_string(self):
        """Asserts that ValueError is raised when GROQ_API_KEY is empty string (falsy)."""
        env_vars = {
            "GROQ_API_KEY": "",
            "NOTIFICATION_SENDER_EMAIL": "sender@example.com",
            "NOTIFICATION_SENDER_PASSWORD": "pass123",
            "OVERLEAF_EMAIL": "overleaf@example.com",
            "OVERLEAF_PASSWORD": "overleaf_pass",
        }
        with patch.dict("os.environ", env_vars, clear=False):
            with pytest.raises(ValueError):
                Config.validate()

    def test_base_dir_is_absolute_path(self):
        """Asserts that Config.BASE_DIR is an absolute path."""
        assert Path(Config.BASE_DIR).is_absolute()
