"""Tests for the cross-project Stanford circuit breaker in ResearchEnhancementAgent.

Ground-truth incident: Stanford's paperreview.ai hit rate limits when processing
longer papers. Projects are processed concurrently via ThreadPoolExecutor, but
Stanford has no API — the only signal available is "did an upload succeed." If
Stanford is failing systemically, launching a full Playwright browser session per
project (15-30s each) only to watch every one fail the same way wastes time and adds
unnecessary load on an already-struggling service.
"""

from unittest.mock import MagicMock, patch

import pytest

from config import Config
from agents.research_enhancement_agent import ResearchEnhancementAgent


@pytest.fixture
def agent(mock_notifier):
    return ResearchEnhancementAgent(overleaf_projects=[], db=MagicMock(), notifier=mock_notifier)


class TestStanfordCircuitBreaker:

    def test_no_cooldown_initially(self, agent):
        assert agent._stanford_cooldown_remaining() == 0

    def test_failures_below_threshold_do_not_trigger_cooldown(self, agent):
        for _ in range(Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD - 1):
            agent._record_stanford_outcome(success=False)
        assert agent._stanford_cooldown_remaining() == 0

    def test_reaching_threshold_triggers_cooldown(self, agent):
        for _ in range(Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD):
            agent._record_stanford_outcome(success=False)
        assert agent._stanford_cooldown_remaining() > 0

    def test_success_resets_the_counter(self, agent):
        for _ in range(Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD - 1):
            agent._record_stanford_outcome(success=False)
        agent._record_stanford_outcome(success=True)
        # After a reset, it should take a full new streak to trip the breaker again.
        for _ in range(Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD - 1):
            agent._record_stanford_outcome(success=False)
        assert agent._stanford_cooldown_remaining() == 0

    def test_success_clears_an_active_cooldown(self, agent):
        for _ in range(Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD):
            agent._record_stanford_outcome(success=False)
        assert agent._stanford_cooldown_remaining() > 0

        agent._record_stanford_outcome(success=True)
        assert agent._stanford_cooldown_remaining() == 0


class TestCircuitBreakerSkipsBrowserLaunch:
    """End-to-end: once tripped, subsequent projects must not launch Playwright at all."""

    def test_projects_after_threshold_skip_upload_attempt_entirely(
        self, db_in_memory, mock_notifier, tmp_path, monkeypatch
    ):
        n_projects = Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD + 2
        project_names = [f"Project_{i}" for i in range(n_projects)]
        for name in project_names:
            db_in_memory.add_project(name, "test@example.com")
            pdf_dir = tmp_path / name
            pdf_dir.mkdir()
            (pdf_dir / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))

        agent = ResearchEnhancementAgent(
            overleaf_projects=project_names, db=db_in_memory, notifier=mock_notifier
        )

        with patch.object(agent, "upload_to_stanford", return_value=None) as mock_upload, \
             patch.object(agent, "_run_internal_review", return_value=True):
            agent.run()

        # Every project attempted the browser exactly once until the breaker tripped;
        # projects processed after that must not have called upload_to_stanford at all.
        assert mock_upload.call_count <= Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD + 1

    def test_resumes_attempting_once_cooldown_expires(
        self, db_in_memory, mock_notifier, tmp_path, monkeypatch
    ):
        """Once the cooldown window has passed (not just once a success happens --
        while cooling down, no attempt is made at all, by design), the next project
        must resume attempting Stanford and a success must clear the failure streak."""
        db_in_memory.add_project("ProjA", "test@example.com")
        pdf_dir = tmp_path / "ProjA"
        pdf_dir.mkdir()
        (pdf_dir / "paper.pdf").write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))

        agent = ResearchEnhancementAgent(
            overleaf_projects=["ProjA"], db=db_in_memory, notifier=mock_notifier
        )
        for _ in range(Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD):
            agent._record_stanford_outcome(success=False)
        agent._stanford_cooldown_until = 0.0  # simulate the cooldown window having elapsed

        with patch.object(agent, "upload_to_stanford", return_value="tok_recovered") as mock_upload:
            agent.run()

        mock_upload.assert_called_once()
        assert agent._stanford_cooldown_remaining() == 0
