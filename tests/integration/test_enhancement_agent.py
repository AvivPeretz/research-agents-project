"""Integration tests for ResearchEnhancementAgent."""

from unittest.mock import MagicMock, patch

import pytest

from agents.research_enhancement_agent import ResearchEnhancementAgent


class TestResearchEnhancementAgent:
    """Integration tests for ResearchEnhancementAgent Stanford workflow."""

    @pytest.fixture
    def enhancement_agent(self, db_in_memory, mock_notifier, sample_project_name):
        """Create a ResearchEnhancementAgent with mocked dependencies."""
        agent = ResearchEnhancementAgent(
            overleaf_projects=[sample_project_name],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        return agent

    def test_get_stanford_state_returns_ready_for_new_project(self, enhancement_agent, db_in_memory, sample_project_name):
        """Asserts that fresh DB returns 'READY_FOR_UPLOAD' status."""
        db_in_memory.add_project(sample_project_name, "test@example.com")
        result = db_in_memory.get_project_state(sample_project_name)
        assert result["stanford_status"] == "READY_FOR_UPLOAD"

    def test_update_stanford_state_persists_to_db(self, enhancement_agent, db_in_memory, sample_project_name):
        """Asserts that state update persists and can be retrieved."""
        db_in_memory.add_project(sample_project_name, "test@example.com")
        db_in_memory.update_project_state(sample_project_name, stanford_status="WAITING_FOR_REVIEW")
        result = db_in_memory.get_project_state(sample_project_name)
        assert result["stanford_status"] == "WAITING_FOR_REVIEW"

    def test_stanford_token_persists_to_db(self, db_in_memory, sample_project_name):
        """Asserts that stanford_token can be written and read back via the slim state getter."""
        db_in_memory.add_project(sample_project_name, "test@example.com")
        db_in_memory.update_project_state(sample_project_name, stanford_token="tok_abc123xyz")
        result = db_in_memory.get_project_state_slim(sample_project_name)
        assert result["stanford_token"] == "tok_abc123xyz"

    def test_get_project_pdf_path_finds_pdf(self, enhancement_agent, tmp_path, monkeypatch):
        """Asserts that PDF path is returned when file exists."""
        from config import Config
        project_name = "test_project"
        project_dir = tmp_path / project_name
        project_dir.mkdir()
        pdf_file = project_dir / "paper.pdf"
        pdf_file.write_text("fake pdf")
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))
        result = enhancement_agent._get_project_pdf_path(project_name)
        assert result == str(pdf_file)

    def test_get_project_pdf_path_returns_none_when_missing(self, enhancement_agent, tmp_path, monkeypatch):
        """Asserts that None is returned when PDF does not exist."""
        from config import Config
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))
        result = enhancement_agent._get_project_pdf_path("empty_project")
        assert result is None

    def test_upload_to_stanford_returns_none_on_invalid_path(self, enhancement_agent):
        """Asserts that upload returns None when pdf_path is None."""
        result = enhancement_agent.upload_to_stanford(project_name="Test", pdf_path=None)
        assert result is None

    def test_generate_actionable_tasks_calls_llm(self, enhancement_agent, sample_project_name):
        """Asserts that LLM is called during task generation."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value="Task 1\nTask 2"):
            result = enhancement_agent._generate_actionable_tasks(project_name=sample_project_name, review_text="Test review")
            assert result is not None

    def test_generate_actionable_tasks_handles_empty_review(self, enhancement_agent, sample_project_name):
        """Asserts that empty review text returns None."""
        result = enhancement_agent._generate_actionable_tasks(project_name=sample_project_name, review_text="")
        assert result is None

    def test_generate_actionable_tasks_alerts_admin_on_waterfall_exhaustion(
        self, enhancement_agent, mock_notifier, sample_project_name
    ):
        """When ask_llm raises RuntimeError (full waterfall exhausted), the method must
        still return the degraded system-note placeholder (unchanged behavior) AND send
        exactly one admin alert for the project."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("All providers exhausted")):
            result = enhancement_agent._generate_actionable_tasks(
                project_name=sample_project_name, review_text="Test review"
            )

        assert "unable to generate actionable tasks" in result
        mock_notifier.send_admin_alert.assert_called_once()
        _, kwargs = mock_notifier.send_admin_alert.call_args
        assert sample_project_name in kwargs["subject"]

    def test_generate_actionable_tasks_dedups_alert_for_same_project(
        self, enhancement_agent, mock_notifier, sample_project_name
    ):
        """Two waterfall-exhaustion failures for the SAME project in one run produce
        only one admin alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            enhancement_agent._generate_actionable_tasks(project_name=sample_project_name, review_text="review 1")
            enhancement_agent._generate_actionable_tasks(project_name=sample_project_name, review_text="review 2")

        mock_notifier.send_admin_alert.assert_called_once()

    def test_generate_actionable_tasks_alerts_separately_for_different_projects(
        self, enhancement_agent, mock_notifier
    ):
        """Waterfall-exhaustion failures for DIFFERENT projects each get their own alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            enhancement_agent._generate_actionable_tasks(project_name="ProjectA", review_text="review")
            enhancement_agent._generate_actionable_tasks(project_name="ProjectB", review_text="review")

        assert mock_notifier.send_admin_alert.call_count == 2
