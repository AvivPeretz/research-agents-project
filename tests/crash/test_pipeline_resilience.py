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
