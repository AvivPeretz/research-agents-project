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

    def test_extract_keywords_retries_and_recovers_from_preamble_response(
        self, literature_agent, sample_project_name
    ):
        """Reproduces the real production failure: the cheap extraction model replied
        with a single explanatory sentence instead of two keyword lines (e.g. 'Based
        on the provided excerpt from the academic manuscript, the topic is...'),
        which used to get sent to Semantic Scholar verbatim as the search query.
        A retry with a stricter prompt must recover real keywords instead."""
        preamble_response = (
            "Based on the provided excerpt from the academic manuscript, the topic "
            "query relates to wireless sensor networks and the method query relates "
            "to energy harvesting techniques."
        )
        good_response = "wireless sensor networks energy\nenergy harvesting circuit design"

        with patch.object(
            literature_agent, "ask_llm", side_effect=[preamble_response, good_response]
        ) as mock_ask:
            topic, method = literature_agent.extract_keywords_from_text(
                sample_project_name, "Sample research text about wireless sensors"
            )

        assert mock_ask.call_count == 2
        assert topic == "wireless sensor networks energy"
        assert method == "energy harvesting circuit design"
        assert "Based on" not in topic
        assert "manuscript" not in topic

    def test_extract_keywords_filters_preamble_line_among_valid_lines(
        self, literature_agent, sample_project_name
    ):
        """A response that mixes an unwanted preamble line with two real keyword
        lines must use the real lines, not the preamble."""
        response = (
            "Here are the two search queries you requested:\n"
            "deep learning image classification\n"
            "convolutional neural network training"
        )

        with patch.object(literature_agent, "ask_llm", return_value=response):
            topic, method = literature_agent.extract_keywords_from_text(
                sample_project_name, "Sample research text"
            )

        assert topic == "deep learning image classification"
        assert method == "convolutional neural network training"

    def test_extract_keywords_no_unnecessary_retry_on_clean_response(
        self, literature_agent, sample_project_name
    ):
        """A well-formatted first response must not trigger a second LLM call."""
        with patch.object(
            literature_agent, "ask_llm", return_value="topic keywords here\nmethod keywords here"
        ) as mock_ask:
            literature_agent.extract_keywords_from_text(sample_project_name, "Sample text")

        assert mock_ask.call_count == 1

    def test_extract_keywords_falls_back_to_project_name_after_failed_retry(
        self, literature_agent, sample_project_name
    ):
        """If even the stricter retry fails to produce usable keywords, fall back to
        the project name rather than ever sending prose as a search query."""
        bad_response = "I'm sorry, I cannot generate keywords without more context about this manuscript and its specific research contributions to the field."

        with patch.object(literature_agent, "ask_llm", return_value=bad_response) as mock_ask:
            topic, method = literature_agent.extract_keywords_from_text(
                sample_project_name, "Sample text"
            )

        assert mock_ask.call_count == 2
        assert topic == sample_project_name
        assert "cannot generate" not in topic

    def test_extract_keywords_uses_cheap_extraction_model(self, literature_agent, sample_project_name):
        """Keyword extraction is lightweight — it must use the cheaper extraction-tier
        model, not the same model reserved for synthesis (reviews/feedback/reports)."""
        from config import Config

        with patch.object(literature_agent, "ask_llm", return_value="k1 k2") as mock_ask:
            literature_agent.extract_keywords_from_text(sample_project_name, "Sample research text about AI")

        _, kwargs = mock_ask.call_args
        assert kwargs.get("model_override") == Config.LLM_EXTRACTION_MODEL_NAME

    def test_filter_relevant_papers_uses_cheap_extraction_model(self, literature_agent, sample_project_name):
        """Relevance filtering is a simple classification task — same reasoning as
        keyword extraction: it should use the cheap extraction-tier model."""
        from config import Config

        papers = [{"title": "Paper A", "snippet": "about AI"}]
        with patch.object(literature_agent, "ask_llm", return_value="1") as mock_ask:
            literature_agent._filter_relevant_papers(sample_project_name, "project text", papers)

        _, kwargs = mock_ask.call_args
        assert kwargs.get("model_override") == Config.LLM_EXTRACTION_MODEL_NAME

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

    def test_process_project_truncates_abstracts_before_final_llm_call(self, mock_notifier, sample_project_name):
        """Asserts the JSON payload sent to the final LLM call has abstracts capped, not the raw 5000+ chars."""
        from tests.fixtures.mock_responses import VALID_LITERATURE_JSON

        long_abstract_papers = [
            {"title": f"Paper {i}", "abstract": "x" * 5000, "year": "2024", "citationCount": "1", "venue": "V", "link": "http://example.com"}
            for i in range(10)
        ]

        captured_prompts = []

        def fake_ask_llm(prompt, model_override=None):
            captured_prompts.append(prompt)
            return VALID_LITERATURE_JSON

        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=fake_ask_llm), \
             patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=long_abstract_papers), \
             patch("utils.literature_fetcher.LiteratureFetcher.enrich_with_openalex", side_effect=lambda papers: papers), \
             patch.object(LiteratureResearchAgent, "_read_project_text", return_value="Sample research text"):

            agent = LiteratureResearchAgent(active_projects=[sample_project_name], notifier=mock_notifier)
            agent.run()

        # The keyword-extraction call happens first; the summarization call is the one
        # containing the paper data, identifiable by the "Here are the enriched results" marker.
        summarization_prompts = [p for p in captured_prompts if "Here are the enriched results" in p]
        assert summarization_prompts, "Expected a summarization prompt containing paper data"
        payload_prompt = summarization_prompts[0]

        assert "xxxxxxxxxx" * 500 not in payload_prompt  # the raw 5000-char abstract must not survive intact
        assert "…" in payload_prompt  # truncation marker present
