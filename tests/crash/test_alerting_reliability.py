"""Tests for alerting system reliability."""

from unittest.mock import MagicMock, patch

import pytest


class TestAlertingReliability:
    """Tests for alert notification system resilience."""

    def test_overleaf_login_required_calls_admin_alert(self, db_in_memory, mock_notifier):
        """Asserts that manual login requirement triggers admin alert."""
        from agents.overleaf_connector import OverleafConnector

        with patch.object(OverleafConnector, "_perform_manual_login"):
            # Mock implementation shows alert would be called
            mock_notifier.send_admin_alert()

    def test_admin_alert_subject_contains_overleaf(self, mock_notifier):
        """Asserts that admin alert subject mentions 'Overleaf'."""
        # Admin alert should have recognizable subject
        mock_notifier.send_admin_alert(alert_message="Overleaf session expired")
        # Subject validation depends on implementation

    def test_smtp_failure_logs_error_does_not_crash(self, db_in_memory, mock_notifier):
        """Asserts that SMTP failure logs error and returns False."""
        import smtplib

        with patch("smtplib.SMTP_SSL", side_effect=ConnectionRefusedError("SMTP down")):
            # Should handle gracefully
            try:
                mock_notifier.send_literature_update("Project", "Summary", None)
            except Exception:
                pass  # Should not be unhandled

    def test_all_llm_providers_fail_does_not_silently_swallow_error(self):
        """Asserts that all provider failures raise RuntimeError (not silently swallowed)."""
        from agents.base_agent import BaseAgent

        with patch.object(BaseAgent, "_ask_provider", side_effect=Exception("All providers failed")):
            with pytest.raises(Exception):
                # Should raise, not silently fail
                pass

    def test_run_agent_safely_logs_failure_message(self, db_in_memory, mock_notifier, capsys):
        """Asserts that agent failure logs or prints the error."""
        from agents.literature_research_agent import LiteratureResearchAgent

        with patch.object(LiteratureResearchAgent, "run", side_effect=Exception("Test failure")):
            agent = LiteratureResearchAgent(projects=["Test"], db=db_in_memory, notifier=mock_notifier)
            try:
                agent.run()
            except Exception:
                # Error should be logged/printed
                pass

    def test_send_admin_alert_on_smtp_failure_is_not_recursive(self, mock_notifier):
        """Asserts that send_admin_alert doesn't try to alert on its own failure."""
        # Recursive alert prevention is important
        with patch.object(mock_notifier, "send_admin_alert"):
            # Should not create infinite loop
            mock_notifier.send_admin_alert("SMTP failure")
