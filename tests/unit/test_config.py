"""Unit tests for Config validation and environment variables."""

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from config import Config

_REPO_ROOT = Path(__file__).parent.parent.parent
_ENV_EXAMPLE_PATH = _REPO_ROOT / ".env.example"
_CONFIG_PATH = _REPO_ROOT / "config.py"


class TestEnvExampleSync:
    """Regression guard for a real documentation-drift bug found in a fresh-eyes
    architecture review: .env.example only documented the API-key-style variables
    and silently omitted 10 other real, functioning env vars config.py reads
    (LLM_EXTRACTION_MODEL_NAME, PLAYWRIGHT_HEADLESS, STANFORD_MAX_UPLOAD_RETRIES,
    etc.) — not broken (all have working defaults) but a real onboarding gap for
    anyone setting up a fresh .env from the example. This test parses both files'
    real current content, so it fails the moment a new os.getenv() name is added to
    config.py without a matching mention in .env.example, instead of relying on
    someone remembering to update both files by hand."""

    @staticmethod
    def _get_config_env_var_names() -> set:
        """Every environment variable name config.py actually reads via
        os.getenv("NAME", ...)."""
        content = _CONFIG_PATH.read_text(encoding="utf-8")
        return set(re.findall(r'os\.getenv\(\s*["\']([A-Z0-9_]+)["\']', content))

    @staticmethod
    def _get_env_example_var_names() -> set:
        """Every environment variable name mentioned in .env.example, whether live
        (UNCOMMENTED=value) or documented-but-commented-out (# NAME=value)."""
        content = _ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        return set(re.findall(r'^#?\s*([A-Z0-9_]+)=', content, re.MULTILINE))

    def test_env_example_exists(self):
        assert _ENV_EXAMPLE_PATH.exists(), ".env.example is missing from the repo root."

    def test_every_config_env_var_is_documented_in_env_example(self):
        """Every real os.getenv("NAME") in config.py must appear somewhere in
        .env.example — live or commented-out-but-documented — so a fresh
        contributor copying .env.example can discover every tunable that exists."""
        config_vars = self._get_config_env_var_names()
        documented_vars = self._get_env_example_var_names()
        missing = config_vars - documented_vars
        assert not missing, (
            f"config.py reads these env vars via os.getenv() that are NOT "
            f"documented anywhere in .env.example: {sorted(missing)}. "
            f"Add them to .env.example (commented-out with a default/description "
            f"is fine for optional ones)."
        )

    def test_env_example_has_no_stale_vars_config_no_longer_reads(self):
        """The inverse check: catches the case where a var is removed from
        config.py but the stale line is left behind in .env.example, silently
        instructing users to set something that no longer does anything."""
        config_vars = self._get_config_env_var_names()
        documented_vars = self._get_env_example_var_names()
        stale = documented_vars - config_vars
        assert not stale, (
            f".env.example documents these env vars that config.py no longer "
            f"reads via os.getenv(): {sorted(stale)}. Remove the stale line(s) or "
            f"confirm config.py should still read them."
        )


class TestLLMExtractionModelName:
    """P3 follow-up: LLM_EXTRACTION_MODEL_NAME used to default to the exact same
    value as LLM_MODEL_NAME (because Groq deleted the previously-used lightweight
    model and no replacement had been confirmed), meaning the "cheap/fast
    extraction model" override had zero real effect anywhere it was used. Now
    defaults to openai/gpt-oss-20b, a real, live-verified (via this account's own
    Groq /v1/models endpoint and an actual completions call) smaller/faster
    sibling of the primary model."""

    def test_extraction_model_defaults_to_a_real_lightweight_model(self):
        assert Config.LLM_EXTRACTION_MODEL_NAME == "openai/gpt-oss-20b"

    def test_extraction_model_is_genuinely_different_from_primary_model(self):
        """The core bug this closes: the override must not silently equal the
        primary model (which made it a no-op override in practice)."""
        assert Config.LLM_EXTRACTION_MODEL_NAME != Config.LLM_MODEL_NAME

    def test_extraction_model_env_override_still_works(self):
        """An operator-set LLM_EXTRACTION_MODEL_NAME env var must still take
        priority over the new default — the override mechanism itself is
        unchanged, only the default value changed.

        Deliberately runs in a fresh subprocess rather than
        importlib.reload(config)-ing the module in-process: a reload rebinds
        `config.Config` to a brand-new class object, but every module that did
        `from config import Config` at its own import time (e.g.
        utils/database_manager.py) keeps its stale reference to the OLD class
        forever. Any later test's `monkeypatch.setattr(Config, "LIBRARY_DIR",
        tmp_path)` then patches the new (reloaded) class while
        DatabaseManager keeps reading the old, unpatched one -- silently
        writing to the real production research_library/system.db for the
        rest of the process. Confirmed as the actual root cause of a real
        production-DB pollution incident (see utils/garbage_collector.py's
        test suite). A subprocess can't leak module identity back into the
        parent process, so it's used here instead."""
        import subprocess
        import sys

        env = dict(os.environ, LLM_EXTRACTION_MODEL_NAME="some/other-model")
        result = subprocess.run(
            [sys.executable, "-c", "from config import Config; print(Config.LLM_EXTRACTION_MODEL_NAME)"],
            cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "some/other-model"


class TestConfigValidation:
    """Tests for Config.validate() and environment variable checking."""

    def test_validate_passes_with_all_keys_present(self):
        """Asserts that Config.validate() does not raise when all required env vars are present."""
        with patch.object(Config, 'GROQ_API_KEY', "gsk_test_key_123"), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', "sender@example.com"), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', "pass123"), \
             patch.object(Config, 'OVERLEAF_EMAIL', "overleaf@example.com"), \
             patch.object(Config, 'OVERLEAF_PASSWORD', "overleaf_pass"):
            # Should not raise
            Config.validate()

    def test_validate_raises_when_groq_key_missing(self):
        """Asserts that ValueError is raised with key name when GROQ_API_KEY is missing."""
        with patch.object(Config, 'GROQ_API_KEY', None), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', "sender@example.com"), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', "pass123"), \
             patch.object(Config, 'OVERLEAF_EMAIL', "overleaf@example.com"), \
             patch.object(Config, 'OVERLEAF_PASSWORD', "overleaf_pass"):
            with pytest.raises(ValueError) as exc_info:
                Config.validate()
            assert "GROQ_API_KEY" in str(exc_info.value)

    def test_validate_raises_when_sender_email_missing(self):
        """Asserts that ValueError is raised when NOTIFICATION_SENDER_EMAIL is missing."""
        with patch.object(Config, 'GROQ_API_KEY', "test_key"), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', None), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', "pass123"), \
             patch.object(Config, 'OVERLEAF_EMAIL', "overleaf@example.com"), \
             patch.object(Config, 'OVERLEAF_PASSWORD', "overleaf_pass"):
            with pytest.raises(ValueError) as exc_info:
                Config.validate()
            assert "NOTIFICATION_SENDER_EMAIL" in str(exc_info.value)

    def test_validate_raises_when_multiple_keys_missing(self):
        """Asserts that all 3 missing key names appear in the ValueError message."""
        with patch.object(Config, 'GROQ_API_KEY', None), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', None), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', None), \
             patch.object(Config, 'OVERLEAF_EMAIL', "overleaf@example.com"), \
             patch.object(Config, 'OVERLEAF_PASSWORD', None):
            with pytest.raises(ValueError) as exc_info:
                Config.validate()
            error_message = str(exc_info.value)
            assert "GROQ_API_KEY" in error_message
            assert "NOTIFICATION_SENDER_EMAIL" in error_message
            assert "OVERLEAF_PASSWORD" in error_message

    def test_validate_raises_when_value_is_empty_string(self):
        """Asserts that ValueError is raised when GROQ_API_KEY is empty string (falsy)."""
        with patch.object(Config, 'GROQ_API_KEY', ""), \
             patch.object(Config, 'NOTIFICATION_SENDER_EMAIL', "sender@example.com"), \
             patch.object(Config, 'NOTIFICATION_SENDER_PASSWORD', "pass123"), \
             patch.object(Config, 'OVERLEAF_EMAIL', "overleaf@example.com"), \
             patch.object(Config, 'OVERLEAF_PASSWORD', "overleaf_pass"):
            with pytest.raises(ValueError):
                Config.validate()

    def test_base_dir_is_absolute_path(self):
        """Asserts that Config.BASE_DIR is an absolute path."""
        assert Path(Config.BASE_DIR).is_absolute()
