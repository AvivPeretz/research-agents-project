"""Tests for the shared, generic Playwright stealth utility extracted from two
previously independent, duplicated implementations (DataIngestionAgent for
Overleaf, ResearchEnhancementAgent.upload_to_stanford for Stanford
paperreview.ai). See utils/playwright_stealth.py's module docstring for the
explicit auth/session scope boundary this consolidation respects.
"""

import inspect

from utils.playwright_stealth import (
    STEALTH_LAUNCH_ARGS,
    DEFAULT_USER_AGENT,
    default_stealth_context_kwargs,
    human_delay,
)


class TestPlaywrightStealthModule:
    def test_stealth_launch_args_contains_expected_flags(self):
        assert "--disable-blink-features=AutomationControlled" in STEALTH_LAUNCH_ARGS
        assert "--no-sandbox" in STEALTH_LAUNCH_ARGS
        assert "--disable-dev-shm-usage" in STEALTH_LAUNCH_ARGS

    def test_default_stealth_context_kwargs_has_no_auth_fields(self):
        """The shared default must NEVER include storage_state or any other
        auth/session field — that must stay caller-supplied and local to
        whichever consumer actually has a login/session concept (only
        DataIngestionAgent does)."""
        kwargs = default_stealth_context_kwargs()
        assert "storage_state" not in kwargs
        assert "user_agent" in kwargs
        assert kwargs["user_agent"] == DEFAULT_USER_AGENT
        assert "viewport" in kwargs
        assert "locale" in kwargs
        assert "timezone_id" in kwargs

    def test_default_stealth_context_kwargs_returns_a_fresh_dict_each_call(self):
        """Must not be a shared mutable default — mutating one caller's dict must
        not leak into another caller's."""
        kwargs_a = default_stealth_context_kwargs()
        kwargs_a["injected"] = "should not leak"
        kwargs_b = default_stealth_context_kwargs()
        assert "injected" not in kwargs_b

    def test_human_delay_sleeps_within_bounds(self, monkeypatch):
        """Real timing behavior, not just import-and-inspect — confirms the
        function actually sleeps a value between the given bounds."""
        captured = {}

        def fake_sleep(seconds):
            captured["seconds"] = seconds

        monkeypatch.setattr("utils.playwright_stealth.time.sleep", fake_sleep)
        human_delay(min_ms=100, max_ms=200)
        assert "seconds" in captured
        assert 0.1 <= captured["seconds"] <= 0.2


class TestStealthConsolidationSync:
    """Regression guard: both consumers must genuinely import and use the SAME
    shared constants from utils.playwright_stealth, not their own re-declared
    copies — otherwise this consolidation is cosmetic and the two
    implementations can silently drift apart again exactly like the dashboard
    provider list did before its own consolidation (see
    tests/unit/test_dashboard_provider_sync.py)."""

    def test_data_ingestion_agent_imports_shared_stealth_args(self):
        import ingestion.data_ingestion_agent as mod
        assert mod.STEALTH_LAUNCH_ARGS is STEALTH_LAUNCH_ARGS

    def test_research_enhancement_agent_imports_shared_stealth_args(self):
        import agents.research_enhancement_agent as mod
        assert mod.STEALTH_LAUNCH_ARGS is STEALTH_LAUNCH_ARGS

    def test_upload_to_stanford_source_uses_shared_helper_not_a_local_copy(self):
        """Source-level check that upload_to_stanford's launch call actually
        references the shared STEALTH_LAUNCH_ARGS symbol rather than a
        re-inlined literal list — catches a regression where someone "fixes" a
        bug by pasting the args back in locally instead of editing the shared
        module."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent
        source = inspect.getsource(ResearchEnhancementAgent.upload_to_stanford)
        assert "STEALTH_LAUNCH_ARGS" in source
        assert "--disable-blink-features=AutomationControlled" not in source

    def test_build_stealth_context_source_uses_shared_helper_not_a_local_copy(self):
        from ingestion.data_ingestion_agent import DataIngestionAgent
        source = inspect.getsource(DataIngestionAgent._build_stealth_context)
        assert "STEALTH_LAUNCH_ARGS" in source
        assert "--disable-blink-features=AutomationControlled" not in source
