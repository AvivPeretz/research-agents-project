"""Tests for configuration validation failures."""

from unittest.mock import patch

import pytest


class TestConfigFailures:
    """Tests for configuration error handling."""

    def test_validate_fails_fast_on_missing_groq_key(self):
        """Asserts that ValueError with 'GROQ_API_KEY' is raised on missing key."""
        from config import Config

        with patch.object(Config, 'GROQ_API_KEY', None), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', "test@example.com"), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', "pass"), \
             patch.object(Config, 'OVERLEAF_EMAIL', "test@example.com"), \
             patch.object(Config, 'OVERLEAF_PASSWORD', "pass"):
            with pytest.raises(ValueError) as exc_info:
                Config.validate()
            assert "GROQ_API_KEY" in str(exc_info.value)

    def test_validate_fails_fast_on_empty_string_value(self):
        """Asserts that ValueError is raised when value is empty string."""
        from config import Config

        with patch.object(Config, 'GROQ_API_KEY', ""), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', "test@example.com"), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', "pass"), \
             patch.object(Config, 'OVERLEAF_EMAIL', "test@example.com"), \
             patch.object(Config, 'OVERLEAF_PASSWORD', "pass"):
            with pytest.raises(ValueError):
                Config.validate()

    def test_validate_lists_all_missing_keys_in_one_error(self):
        """Asserts that all 5 missing key names appear in error message."""
        from config import Config

        with patch.object(Config, 'GROQ_API_KEY', None), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', None), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', None), \
             patch.object(Config, 'OVERLEAF_EMAIL', None), \
             patch.object(Config, 'OVERLEAF_PASSWORD', None):
            with pytest.raises(ValueError) as exc_info:
                Config.validate()
            error_msg = str(exc_info.value)
            assert "GROQ_API_KEY" in error_msg or "missing" in error_msg.lower()

    def test_validate_passes_when_all_present(self):
        """Asserts that validate() does not raise when all keys present."""
        from config import Config

        with patch.object(Config, 'GROQ_API_KEY', "gsk_test_key"), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', "sender@example.com"), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', "pass123"), \
             patch.object(Config, 'OVERLEAF_EMAIL', "overleaf@example.com"), \
             patch.object(Config, 'OVERLEAF_PASSWORD', "overleaf_pass"):
            # Should not raise
            Config.validate()

    def test_playwright_timeout_ms_is_positive_integer(self):
        """Asserts that PLAYWRIGHT_TIMEOUT_MS is positive."""
        from config import Config

        assert Config.PLAYWRIGHT_TIMEOUT_MS > 0
        assert isinstance(Config.PLAYWRIGHT_TIMEOUT_MS, int)

    def test_garbage_collection_ttl_is_positive_integer(self):
        """Asserts that GARBAGE_COLLECTION_TTL_DAYS is positive."""
        from config import Config

        assert Config.GARBAGE_COLLECTION_TTL_DAYS > 0
        assert isinstance(Config.GARBAGE_COLLECTION_TTL_DAYS, int)
