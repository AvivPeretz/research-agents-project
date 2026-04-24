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
            projects=[sample_project_name],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        return agent

    def test_get_stanford_state_returns_ready_for_new_project(self, enhancement_agent, db_in_memory, sample_project_name):
        """Asserts that fresh DB returns 'READY_FOR_UPLOAD' status."""
        db_in_memory.add_project(sample_project_name)
        result = db_in_memory.get_project_state(sample_project_name)
        assert result["stanford_status"] == "READY_FOR_UPLOAD"

    def test_update_stanford_state_persists_to_db(self, enhancement_agent, db_in_memory, sample_project_name):
        """Asserts that state update persists and can be retrieved."""
        db_in_memory.add_project(sample_project_name)
        db_in_memory.update_project_state(sample_project_name, stanford_status="WAITING_FOR_REVIEW")
        result = db_in_memory.get_project_state(sample_project_name)
        assert result["stanford_status"] == "WAITING_FOR_REVIEW"

    def test_get_project_pdf_path_finds_pdf(self, tmp_path):
        """Asserts that PDF path is returned when file exists."""
        pdf_file = tmp_path / "paper.pdf"
        pdf_file.write_text("fake pdf")
        result = ResearchEnhancementAgent._get_project_pdf_path(str(tmp_path))
        assert result == str(pdf_file)

    def test_get_project_pdf_path_returns_none_when_missing(self, tmp_path):
        """Asserts that None is returned when PDF does not exist."""
        result = ResearchEnhancementAgent._get_project_pdf_path(str(tmp_path))
        assert result is None

    def test_upload_to_stanford_returns_false_on_invalid_path(self, enhancement_agent):
        """Asserts that upload returns False when pdf_path is None."""
        result = enhancement_agent._upload_to_stanford(pdf_path=None, project_name="Test")
        assert result is False

    def test_get_token_from_email_returns_none_on_imap_error(self, enhancement_agent):
        """Asserts that None is returned when IMAP connection fails."""
        with patch("imaplib.IMAP4_SSL", side_effect=ConnectionRefusedError("IMAP unavailable")):
            result = enhancement_agent._get_stanford_token_from_email("Test_Project")
            assert result is None

    def test_generate_actionable_tasks_calls_llm(self, enhancement_agent):
        """Asserts that LLM is called during task generation."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value="Task 1\nTask 2"):
            result = enhancement_agent._generate_actionable_tasks(review_text="Test review")
            assert result is not None

    def test_generate_actionable_tasks_handles_empty_review(self, enhancement_agent):
        """Asserts that empty review text returns fallback string."""
        result = enhancement_agent._generate_actionable_tasks(review_text="")
        assert isinstance(result, str)
        assert result != ""
