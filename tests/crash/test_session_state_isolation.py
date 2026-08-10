"""Regression tests for the Overleaf/Google-Scholar Playwright session-file collision.

Ground-truth incident: Config.OVERLEAF_STATE_PATH and Config.SCHOLAR_STATE_PATH pointed at
the same file (scholar_state.json). DataIngestionAgent (Overleaf) and LiteratureFetcher
(Google Scholar) each call context.storage_state(path=self.state_file) after a manual
login, so whichever ran most recently silently clobbered the other's session cookies.
The next Overleaf sync would then load Google-Scholar cookies while navigating to
overleaf.com, fail authentication, and force a manual reCAPTCHA relogin -- with no error
raised anywhere, since a valid-looking (but wrong-site) session file was present.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from config import Config
from ingestion.data_ingestion_agent import DataIngestionAgent
from utils.literature_fetcher import LiteratureFetcher


class TestSessionStateIsolation:

    def test_overleaf_and_scholar_state_paths_are_distinct(self):
        """Direct guard against the exact config typo that caused the incident."""
        assert Config.OVERLEAF_STATE_PATH != Config.SCHOLAR_STATE_PATH

    def test_scholar_login_does_not_clobber_overleaf_session(self, tmp_path, monkeypatch):
        """Simulates the real failure sequence and proves it no longer happens.

        1. DataIngestionAgent saves an Overleaf session to OVERLEAF_STATE_PATH.
        2. LiteratureFetcher's Google Scholar fallback saves its own session to
           SCHOLAR_STATE_PATH.
        3. The Overleaf session file must still contain the Overleaf cookies.
        """
        overleaf_state_file = tmp_path / "overleaf_state.json"
        scholar_state_file = tmp_path / "scholar_state.json"
        monkeypatch.setattr(Config, "OVERLEAF_STATE_PATH", overleaf_state_file)
        monkeypatch.setattr(Config, "SCHOLAR_STATE_PATH", scholar_state_file)
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path / "overleaf_projects"))
        monkeypatch.setattr(Config, "PLAYWRIGHT_HEADLESS", True)

        overleaf_storage_state = {
            "cookies": [{"name": "overleaf_session2", "domain": ".overleaf.com", "value": "abc"}],
            "origins": [],
        }
        scholar_storage_state = {
            "cookies": [{"name": "SID", "domain": ".google.com", "value": "xyz"}],
            "origins": [],
        }

        # Step 1: DataIngestionAgent "saves" its session (mirrors context.storage_state()).
        agent = DataIngestionAgent(db=MagicMock(), notifier=MagicMock())
        Path(agent.state_file).write_text(json.dumps(overleaf_storage_state))
        assert agent.state_file == str(overleaf_state_file) or Path(agent.state_file) == overleaf_state_file

        # Step 2: LiteratureFetcher's Google Scholar fallback "saves" its own session.
        fetcher = LiteratureFetcher()
        Path(fetcher.state_file).write_text(json.dumps(scholar_storage_state))

        # Step 3: the Overleaf session file must be untouched by the Scholar login.
        saved_overleaf_state = json.loads(overleaf_state_file.read_text())
        assert saved_overleaf_state == overleaf_storage_state
        domains = {c["domain"] for c in saved_overleaf_state["cookies"]}
        assert ".overleaf.com" in domains
        assert ".google.com" not in domains
