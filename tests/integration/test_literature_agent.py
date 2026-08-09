"""Integration tests for LiteratureResearchAgent."""

from unittest.mock import MagicMock, patch

import pytest

from agents.literature_research_agent import LiteratureResearchAgent
from tests.fixtures.mock_responses import (
    BROKEN_JSON,
    MOCK_SEMANTIC_SCHOLAR_RESULTS,
    VALID_LITERATURE_JSON,
)


class TestLiteratureResearchAgent:
    """Integration tests for LiteratureResearchAgent functionality."""

    @pytest.fixture
    def literature_agent(self, mock_notifier, sample_project_name):
        """Create a LiteratureResearchAgent with mocked dependencies."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            with patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=MOCK_SEMANTIC_SCHOLAR_RESULTS):
                agent = LiteratureResearchAgent(
                    active_projects=[sample_project_name],
                    notifier=mock_notifier,
                )
                yield agent

    def test_extract_keywords_returns_string(self, literature_agent, sample_project_name):
        """Asserts that extract_keywords_from_text returns a non-empty tuple of two strings."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value="keyword1 keyword2"):
            result = literature_agent.extract_keywords_from_text(sample_project_name, "Sample research text about AI")
            assert isinstance(result, tuple)
            assert len(result) == 2
            assert result[0] != ""
            assert result[1] != ""

    def test_extract_keywords_falls_back_to_project_name_on_empty_text(self, literature_agent, sample_project_name):
        """Asserts that extract_keywords returns project name when text input is empty."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=""):
            result = literature_agent.extract_keywords_from_text(sample_project_name, "")
            assert sample_project_name in result or result == sample_project_name

    def test_extract_keywords_strips_quotes(self, literature_agent, sample_project_name):
        """Asserts that quotes returned by LLM are stripped from result."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value='"deep learning" "neural networks"'):
            result = literature_agent.extract_keywords_from_text(sample_project_name, "Test text")
            assert isinstance(result, tuple)
            assert '"' not in result[0]
            assert "deep learning" in result[0].lower() or "neural" in result[0].lower()

    def test_process_results_with_llm_returns_valid_dict(self, literature_agent, sample_project_name):
        """Asserts that process_results_with_llm returns dict with 'summary' and 'papers' keys."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            result = literature_agent.process_results_with_llm(sample_project_name, "test keyword", MOCK_SEMANTIC_SCHOLAR_RESULTS)
            assert isinstance(result, dict)
            assert "summary" in result
            assert "papers" in result

    def test_process_results_with_llm_handles_broken_json(self, literature_agent, sample_project_name):
        """Asserts that broken JSON triggers fallback dict without crash."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=BROKEN_JSON):
            result = literature_agent.process_results_with_llm(sample_project_name, "test keyword", [])
            assert isinstance(result, dict)
            assert "summary" in result
            assert "papers" in result

    def test_process_results_with_llm_handles_empty_data(self, literature_agent, sample_project_name):
        """Asserts that empty data list returns fallback dict."""
        result = literature_agent.process_results_with_llm(sample_project_name, "test keyword", [])
        assert isinstance(result, dict)
        assert "summary" in result
        assert "papers" in result

    def test_process_results_with_llm_pydantic_failure(self, literature_agent, sample_project_name):
        """Asserts that JSON failing Pydantic validation returns fallback dict."""
        invalid_json = '{"summary": "short", "papers": []}'  # Short summary, empty papers
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=invalid_json):
            result = literature_agent.process_results_with_llm(sample_project_name, "test keyword", MOCK_SEMANTIC_SCHOLAR_RESULTS)
            assert isinstance(result, dict)

    def test_run_calls_notifier_send(self, literature_agent, mock_notifier):
        """Asserts that full run() calls mock_notifier.send_literature_update once."""
        with patch.object(literature_agent, "_read_project_text", return_value="Sample research text"):
            literature_agent.run()
            mock_notifier.send_literature_update.assert_called()

    def test_run_with_no_projects_does_not_crash(self, mock_notifier):
        """Asserts that instantiating with empty project list and running completes without error."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            agent = LiteratureResearchAgent(
                active_projects=[],
                notifier=mock_notifier,
            )
            # Should not crash
            agent.run()

    def test_read_project_text_uses_structural_sampling(self, literature_agent, sample_project_name, tmp_path, monkeypatch):
        """Asserts _read_project_text samples across the whole document, not just the prefix."""
        from config import Config

        project_dir = tmp_path / sample_project_name
        project_dir.mkdir()
        long_structured_doc = r"""
\documentclass{article}
\begin{document}
\section{Introduction}
""" + ("Filler introduction text. " * 200) + r"""
\section{Conclusion}
This conclusion mentions a unique marker: ZEBRA_MARKER_TOKEN.
\end{document}
"""
        (project_dir / "main.tex").write_text(long_structured_doc)
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))

        result = literature_agent._read_project_text(sample_project_name)

        assert "ZEBRA_MARKER_TOKEN" in result
        assert len(result) <= Config.MAX_PROJECT_TEXT_CHARS
