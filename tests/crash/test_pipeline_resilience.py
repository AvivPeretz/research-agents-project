"""Tests for pipeline resilience and error recovery."""

from unittest.mock import MagicMock, patch

import pytest


class TestPipelineResilience:
    """Tests for pipeline fault tolerance and recovery."""

    def test_run_agent_safely_returns_false_on_exception(self):
        """run_agent_safely returns False when agent.run() raises an exception."""
        from main import run_agent_safely

        failing_agent = MagicMock()
        failing_agent.__class__.__name__ = "FailingTestAgent"
        failing_agent.run.side_effect = Exception("Agent blew up")

        result = run_agent_safely(failing_agent)

        assert result is False

    def test_run_agent_safely_returns_true_on_success(self):
        """run_agent_safely returns True when agent.run() completes without error."""
        from main import run_agent_safely

        good_agent = MagicMock()
        good_agent.__class__.__name__ = "GoodTestAgent"
        good_agent.run.return_value = None  # run() returns None on success

        result = run_agent_safely(good_agent)

        assert result is True

    def test_run_agent_safely_alerts_admin_on_agent_crash(self):
        """An unhandled exception escaping an entire agent's run() is the most severe
        failure mode a scheduled run can have -- it must never be reported by log file
        alone. run_agent_safely must alert an admin when given a notifier."""
        from main import run_agent_safely

        failing_agent = MagicMock()
        failing_agent.__class__.__name__ = "FailingTestAgent"
        failing_agent.run.side_effect = Exception("Agent blew up")
        notifier = MagicMock()

        result = run_agent_safely(failing_agent, notifier=notifier)

        assert result is False
        notifier.send_admin_alert.assert_called_once()
        call_str = str(notifier.send_admin_alert.call_args)
        assert "FailingTestAgent" in call_str

    def test_run_agent_safely_success_does_not_alert(self):
        """A successful run must never trigger an admin alert."""
        from main import run_agent_safely

        good_agent = MagicMock()
        good_agent.__class__.__name__ = "GoodTestAgent"
        good_agent.run.return_value = None
        notifier = MagicMock()

        run_agent_safely(good_agent, notifier=notifier)

        notifier.send_admin_alert.assert_not_called()

    def test_run_agent_safely_survives_notifier_itself_failing(self):
        """If sending the crash alert email itself fails (e.g. SMTP down), that must
        not raise out of run_agent_safely and break the rest of the pipeline."""
        from main import run_agent_safely

        failing_agent = MagicMock()
        failing_agent.__class__.__name__ = "FailingTestAgent"
        failing_agent.run.side_effect = Exception("Agent blew up")
        notifier = MagicMock()
        notifier.send_admin_alert.side_effect = Exception("SMTP is also down")

        result = run_agent_safely(failing_agent, notifier=notifier)  # must not raise

        assert result is False


class TestPerProjectFailureAlerting:
    """A single project raising inside an agent's ThreadPoolExecutor must not be
    silent: it must reach the DB (for the dashboard) and an admin alert (since nobody
    is watching log files). One test per agent that runs projects in a thread pool."""

    def test_literature_agent_alerts_on_project_failure(self, mock_notifier):
        from agents.literature_research_agent import LiteratureResearchAgent

        db = MagicMock()
        agent = LiteratureResearchAgent(active_projects=["BadProject"], notifier=mock_notifier, db=db)

        with patch.object(agent, "_process_project", side_effect=Exception("boom")):
            agent.run()

        mock_notifier.send_admin_alert.assert_called_once()
        assert "BadProject" in str(mock_notifier.send_admin_alert.call_args)
        failure_calls = [c for c in db.log_agent_run.call_args_list if c.kwargs.get("status") == "FAILURE"]
        assert len(failure_calls) == 1

    def test_progress_agent_alerts_on_project_failure(self, mock_notifier):
        from agents.progress_tracking_agent import ProgressTrackingAgent

        db = MagicMock()
        agent = ProgressTrackingAgent(overleaf_projects=["BadProject"], notifier=mock_notifier, db=db)

        with patch.object(agent, "_process_project", side_effect=Exception("boom")):
            agent.run()

        mock_notifier.send_admin_alert.assert_called_once()
        assert "BadProject" in str(mock_notifier.send_admin_alert.call_args)
        failure_calls = [c for c in db.log_agent_run.call_args_list if c.kwargs.get("status") == "FAILURE"]
        assert len(failure_calls) == 1

    def test_enhancement_agent_alerts_on_project_failure(self, mock_notifier):
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        db = MagicMock()
        agent = ResearchEnhancementAgent(overleaf_projects=["BadProject"], notifier=mock_notifier, db=db)

        with patch.object(agent, "_process_project", side_effect=Exception("boom")):
            agent.run()

        mock_notifier.send_admin_alert.assert_called_once()
        assert "BadProject" in str(mock_notifier.send_admin_alert.call_args)
        failure_calls = [c for c in db.log_agent_run.call_args_list if c.kwargs.get("status") == "FAILURE"]
        assert len(failure_calls) == 1

    def test_alert_failure_does_not_mask_original_project_error(self, mock_notifier):
        """If send_admin_alert itself throws (e.g. SMTP down), the agent's run() must
        still complete instead of crashing the whole pipeline over a failed alert."""
        from agents.literature_research_agent import LiteratureResearchAgent

        db = MagicMock()
        mock_notifier.send_admin_alert.side_effect = Exception("SMTP also down")
        agent = LiteratureResearchAgent(active_projects=["BadProject"], notifier=mock_notifier, db=db)

        with patch.object(agent, "_process_project", side_effect=Exception("boom")):
            agent.run()  # must not raise


class TestPipelineResilienceMisc:
    """General pipeline fault-tolerance tests unrelated to failure alerting
    (pre-existing tests, relocated here after TestPerProjectFailureAlerting was
    inserted above them)."""

    def test_pipeline_continues_after_agent_failure(self, db_in_memory, mock_notifier):
        from agents.progress_tracking_agent import ProgressTrackingAgent
        from utils.overleaf_connector import OverleafConnector

        db_in_memory.add_project("TestProject", "test@example.com")
        agent = ProgressTrackingAgent(
            overleaf_projects=["TestProject"],
            db=db_in_memory,
            notifier=mock_notifier
        )
        with patch.object(OverleafConnector, "read_and_clean_tex_file", return_value="Test content"):
            with patch.object(agent, "ask_llm", return_value="Feedback"):
                agent.run()  # Should not crash

    def test_empty_project_list_skips_agents_gracefully(self, db_in_memory, mock_notifier):
        """Asserts that empty project list doesn't crash pipeline."""
        from agents.literature_research_agent import LiteratureResearchAgent

        agent = LiteratureResearchAgent(active_projects=[], notifier=mock_notifier)
        # Should not crash
        agent.run()

    def test_invalid_project_name_via_argparse_exits_gracefully(self,tmp_path):
        """Asserts that non-existent project in overleaf_projects/ exits cleanly."""
        from agents.literature_research_agent import LiteratureResearchAgent
        from config import Config
        with patch.object(Config,"OVERLEAF_DIR" ,str(tmp_path)):
            # Project doesn't exist in filesystem
            from main import get_all_active_projects
            result = get_all_active_projects()
            # Should exit cleanly without unhandled exception
            assert isinstance(result,list)
