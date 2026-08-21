"""Tests for BaseAgent._alert_waterfall_exhausted — the shared, deduplicated
admin-alert helper promoted from LiteratureResearchAgent so ProgressTrackingAgent
and ResearchEnhancementAgent can reuse the identical pattern instead of each
duplicating it (see agents/base_agent.py)."""

from unittest.mock import MagicMock

import pytest

from agents.base_agent import BaseAgent


def _make_stub_agent(notifier=None):
    """Return a concrete BaseAgent instance with __init__ bypassed (no real API
    keys / logging setup needed) but with the fields _alert_waterfall_exhausted
    actually depends on."""

    class _Stub(BaseAgent):
        def run(self):
            pass

    agent = object.__new__(_Stub)
    agent.agent_name = "StubAgent"
    agent.logger = MagicMock()
    agent._waterfall_exhausted_alerted = set()
    agent.notifier = notifier
    return agent


class TestAlertWaterfallExhausted:
    def test_sends_admin_alert_with_project_and_context(self):
        notifier = MagicMock()
        agent = _make_stub_agent(notifier=notifier)

        agent._alert_waterfall_exhausted("some operation", "ProjectA")

        notifier.send_admin_alert.assert_called_once()
        _, kwargs = notifier.send_admin_alert.call_args
        assert "ProjectA" in kwargs["subject"]
        assert "StubAgent" in kwargs["subject"]
        assert "ProjectA" in kwargs["message"]
        assert "some operation" in kwargs["message"]

    def test_second_failure_same_project_does_not_alert_again(self):
        notifier = MagicMock()
        agent = _make_stub_agent(notifier=notifier)

        agent._alert_waterfall_exhausted("op 1", "ProjectA")
        agent._alert_waterfall_exhausted("op 2", "ProjectA")

        notifier.send_admin_alert.assert_called_once()

    def test_different_projects_each_get_their_own_alert(self):
        notifier = MagicMock()
        agent = _make_stub_agent(notifier=notifier)

        agent._alert_waterfall_exhausted("op", "ProjectA")
        agent._alert_waterfall_exhausted("op", "ProjectB")

        assert notifier.send_admin_alert.call_count == 2

    def test_no_notifier_does_not_raise(self):
        agent = _make_stub_agent(notifier=None)
        # Should not raise even though there's no notifier to send through.
        agent._alert_waterfall_exhausted("op", "ProjectA")

    def test_notifier_exception_is_swallowed(self):
        notifier = MagicMock()
        notifier.send_admin_alert.side_effect = Exception("SMTP down")
        agent = _make_stub_agent(notifier=notifier)

        # Must not raise/propagate — alert failure must not mask the original
        # degraded-output path.
        agent._alert_waterfall_exhausted("op", "ProjectA")

    def test_dedup_still_applies_even_if_notifier_raised(self):
        """A failed send still marks the project as alerted (matches original
        LiteratureResearchAgent semantics: dedup guard is set before the send is
        attempted) — a broken SMTP server shouldn't cause alert-spam retries."""
        notifier = MagicMock()
        notifier.send_admin_alert.side_effect = Exception("SMTP down")
        agent = _make_stub_agent(notifier=notifier)

        agent._alert_waterfall_exhausted("op", "ProjectA")
        agent._alert_waterfall_exhausted("op", "ProjectA")

        assert notifier.send_admin_alert.call_count == 1
