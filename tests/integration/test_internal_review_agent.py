"""Comprehensive tests for the internal peer review fallback mechanism."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest

from agents.research_enhancement_agent import ResearchEnhancementAgent, MINIMUM_REVIEW_LENGTH


class TestInternalReviewAgent:
    """Tests for the internal fallback review pipeline in ResearchEnhancementAgent."""

    @pytest.fixture
    def enhancement_agent(self, db_in_memory, mock_notifier, sample_project_name):
        """Create a ResearchEnhancementAgent with in-memory DB and mocked notifier."""
        agent = ResearchEnhancementAgent(
            overleaf_projects=[sample_project_name],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        return agent

    # ------------------------------------------------------------------
    # 1. test_internal_review_skips_short_paper
    # ------------------------------------------------------------------
    def test_internal_review_skips_short_paper(
        self, enhancement_agent, db_in_memory, sample_project_name
    ):
        """Paper text shorter than MINIMUM_REVIEW_LENGTH causes skip with correct DB status."""
        db_in_memory.add_project(sample_project_name, "test@example.com")

        short_text = "x" * (MINIMUM_REVIEW_LENGTH - 1)

        with patch("agents.research_enhancement_agent.OverleafConnector") as mock_cls:
            mock_connector = MagicMock()
            mock_connector.read_and_clean_tex_file.return_value = short_text
            mock_cls.return_value = mock_connector

            result = enhancement_agent._run_internal_review(sample_project_name)

        assert result is False
        state = db_in_memory.get_project_state(sample_project_name)
        assert state["stanford_status"] == "SKIPPED_INSUFFICIENT_TEXT"
        enhancement_agent.notifier.send_stanford_tasks.assert_not_called()

    # ------------------------------------------------------------------
    # 2. test_internal_review_skips_empty_paper
    # ------------------------------------------------------------------
    def test_internal_review_skips_empty_paper(
        self, enhancement_agent, db_in_memory, sample_project_name
    ):
        """Empty paper text causes skip with correct DB status and no email sent."""
        db_in_memory.add_project(sample_project_name, "test@example.com")

        with patch("agents.research_enhancement_agent.OverleafConnector") as mock_cls:
            mock_connector = MagicMock()
            mock_connector.read_and_clean_tex_file.return_value = ""
            mock_cls.return_value = mock_connector

            result = enhancement_agent._run_internal_review(sample_project_name)

        assert result is False
        state = db_in_memory.get_project_state(sample_project_name)
        assert state["stanford_status"] == "SKIPPED_INSUFFICIENT_TEXT"
        enhancement_agent.notifier.send_stanford_tasks.assert_not_called()

    # ------------------------------------------------------------------
    # 3. test_internal_review_success_full_flow
    # ------------------------------------------------------------------
    def test_internal_review_success_full_flow(
        self, enhancement_agent, db_in_memory, sample_project_name
    ):
        """Full happy path: long paper triggers LLM call, saves file, updates DB, sends email."""
        db_in_memory.add_project(sample_project_name, "test@example.com")

        long_text = "This is a research paper about deep learning. " * 100  # >> 3000 chars
        mock_review = (
            "## Summary\nThis paper presents a novel approach.\n\n"
            "## Action Plan\n1. Fix the baseline comparison by 2026-05-15.\n"
        )

        with patch("agents.research_enhancement_agent.OverleafConnector") as mock_cls, \
             patch("agents.base_agent.BaseAgent.ask_llm", return_value=mock_review):

            mock_connector = MagicMock()
            mock_connector.read_and_clean_tex_file.return_value = long_text
            mock_cls.return_value = mock_connector

            result = enhancement_agent._run_internal_review(sample_project_name)

        assert result is True

        state = db_in_memory.get_project_state(sample_project_name)
        assert state["stanford_status"] == "INTERNAL_REVIEW_COMPLETED"

        enhancement_agent.notifier.send_stanford_tasks.assert_called_once()
        _, kwargs = enhancement_agent.notifier.send_stanford_tasks.call_args
        assert kwargs["project_name"] == sample_project_name
        assert mock_review in kwargs["md_content"]

    # ------------------------------------------------------------------
    # 4. test_internal_review_llm_failure_returns_false
    # ------------------------------------------------------------------
    def test_internal_review_llm_failure_returns_false(
        self, enhancement_agent, db_in_memory, sample_project_name
    ):
        """When ask_llm raises RuntimeError the method returns False and sends no email."""
        db_in_memory.add_project(sample_project_name, "test@example.com")

        long_text = "Research content. " * 200  # >> 3000 chars

        with patch("agents.research_enhancement_agent.OverleafConnector") as mock_cls, \
             patch(
                 "agents.base_agent.BaseAgent.ask_llm",
                 side_effect=RuntimeError("All LLM providers failed"),
             ):

            mock_connector = MagicMock()
            mock_connector.read_and_clean_tex_file.return_value = long_text
            mock_cls.return_value = mock_connector

            result = enhancement_agent._run_internal_review(sample_project_name)

        assert result is False

        state = db_in_memory.get_project_state(sample_project_name)
        assert state["stanford_status"] != "INTERNAL_REVIEW_COMPLETED"

        enhancement_agent.notifier.send_stanford_tasks.assert_not_called()

    # ------------------------------------------------------------------
    # 5. test_truncate_paper_text_short_paper_unchanged
    # ------------------------------------------------------------------
    def test_truncate_paper_text_short_paper_unchanged(self, enhancement_agent):
        """Text shorter than max_chars is returned unchanged."""
        text = "a" * 5000
        result = enhancement_agent._truncate_paper_text(text)
        assert result == text

    # ------------------------------------------------------------------
    # 6. test_truncate_paper_text_long_paper_truncated
    # ------------------------------------------------------------------
    def test_truncate_paper_text_long_paper_truncated(self, enhancement_agent):
        """Text of 20000 chars is truncated substantially and contains the truncation marker."""
        text = "b" * 20000
        result = enhancement_agent._truncate_paper_text(text)

        # Content is capped at max_chars=8000; separators add a small constant overhead
        assert len(result) < len(text)
        assert len(result) <= 8200  # 8000 content + ~200 chars of separator strings
        assert "[... middle section truncated" in result

    # ------------------------------------------------------------------
    # 7. test_load_related_papers_missing_csv_returns_empty
    # ------------------------------------------------------------------
    def test_load_related_papers_missing_csv_returns_empty(
        self, enhancement_agent, sample_project_name
    ):
        """Returns empty string gracefully when the rolling CSV does not exist."""
        result = enhancement_agent._load_related_papers_from_csv(sample_project_name)
        assert result == ""

    # ------------------------------------------------------------------
    # 8. test_load_related_papers_valid_csv_returns_formatted_string
    # ------------------------------------------------------------------
    def test_load_related_papers_valid_csv_returns_formatted_string(
        self, enhancement_agent, sample_project_name, tmp_path, monkeypatch
    ):
        """Returns formatted string containing paper titles when CSV is present."""
        from config import Config

        mock_df = pd.DataFrame(
            {
                "paper name": ["Deep Learning Survey", "Attention Is All You Need", "BERT"],
                "year published": ["2022", "2017", "2018"],
                "cited": ["500", "10000", "20000"],
                "source": ["NeurIPS", "NeurIPS", "NAACL"],
                "types of available data": ["text", "text", "text"],
            }
        )

        with patch("pandas.read_csv", return_value=mock_df), \
             patch("os.path.exists", return_value=True):

            result = enhancement_agent._load_related_papers_from_csv(sample_project_name)

        assert "Deep Learning Survey" in result
        assert "Attention Is All You Need" in result
        assert "BERT" in result

    # ------------------------------------------------------------------
    # 9. test_run_triggers_fallback_on_upload_failure
    # ------------------------------------------------------------------
    def test_run_triggers_fallback_on_upload_failure(
        self, enhancement_agent, db_in_memory, sample_project_name, tmp_path, monkeypatch
    ):
        """When upload_to_stanford returns False, _run_internal_review is called once."""
        from config import Config

        db_in_memory.add_project(sample_project_name, "test@example.com")

        # Create a fake PDF so the path check passes
        pdf_dir = tmp_path / sample_project_name
        pdf_dir.mkdir()
        fake_pdf = pdf_dir / "paper.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))

        with patch.object(enhancement_agent, "upload_to_stanford", return_value=False), \
             patch.object(enhancement_agent, "_run_internal_review", return_value=True) as mock_fallback:

            enhancement_agent.run()

        mock_fallback.assert_called_once_with(sample_project_name)

    # ------------------------------------------------------------------
    # 10. test_run_triggers_fallback_on_review_fetch_failure
    # ------------------------------------------------------------------
    def test_run_triggers_fallback_on_review_fetch_failure(
        self, enhancement_agent, db_in_memory, sample_project_name
    ):
        """When token found but _fetch_review_from_stanford returns None, fallback is called."""
        db_in_memory.add_project(sample_project_name, "test@example.com")
        recent_time = (datetime.now() - timedelta(hours=1)).isoformat()
        db_in_memory.update_project_state(
            sample_project_name,
            stanford_status="WAITING_FOR_REVIEW",
            last_upload_time=recent_time,
        )

        with patch.object(
            enhancement_agent, "_get_stanford_token_from_email", return_value="tok_abc123"
        ), patch.object(
            enhancement_agent, "_fetch_review_from_stanford", return_value=None
        ), patch.object(
            enhancement_agent, "_run_internal_review", return_value=True
        ) as mock_fallback:

            enhancement_agent.run()

        mock_fallback.assert_called_once_with(sample_project_name)

    # ------------------------------------------------------------------
    # 11. test_run_skips_project_with_insufficient_text_status
    # ------------------------------------------------------------------
    def test_run_skips_project_with_insufficient_text_status(
        self, enhancement_agent, db_in_memory, sample_project_name
    ):
        """Projects in SKIPPED_INSUFFICIENT_TEXT status are silently skipped each run."""
        db_in_memory.add_project(sample_project_name, "test@example.com")
        db_in_memory.update_project_state(
            sample_project_name, stanford_status="SKIPPED_INSUFFICIENT_TEXT"
        )

        with patch.object(
            enhancement_agent, "upload_to_stanford"
        ) as mock_upload, patch.object(
            enhancement_agent, "_run_internal_review"
        ) as mock_fallback:

            enhancement_agent.run()

        mock_upload.assert_not_called()
        mock_fallback.assert_not_called()

    # ------------------------------------------------------------------
    # 12. test_build_internal_review_prompt_contains_key_sections
    # ------------------------------------------------------------------
    def test_build_internal_review_prompt_contains_key_sections(self, enhancement_agent):
        """The generated prompt contains all required structural sections."""
        prompt = enhancement_agent._build_internal_review_prompt(
            project_name="Test Project",
            paper_text="Abstract: This paper proposes a new method...",
            related_papers_str="[1] Title: Related Paper\n    Year: 2024 | Citations: 100 | Venue: NeurIPS",
        )

        assert "RELATED WORK CONTEXT" in prompt
        assert "MANUSCRIPT TEXT" in prompt
        assert "Originality" in prompt
        assert "Soundness" in prompt
        assert "Action Plan" in prompt
        assert "Deadline" in prompt
        assert "Test Project" in prompt
        assert "Related Paper" in prompt
