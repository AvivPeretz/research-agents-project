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

    def test_filter_relevant_papers_alerts_admin_on_waterfall_exhaustion(
        self, literature_agent, mock_notifier, sample_project_name
    ):
        """Amit's feedback: irrelevant papers reached the output because a full LLM
        waterfall exhaustion during relevance filtering fell back to 'use all papers'
        SILENTLY (confirmed in production logs for both real test projects on
        2026-08-19). This must now alert an admin, matching every other waterfall-
        exhaustion site in this codebase, AND must no longer degrade to unfiltered
        papers — it must raise RelevanceFilterExhausted so the caller
        (_process_project) skips this project's update for the cycle entirely
        instead of writing/emailing unvetted data (the alone-alerting fix still left
        that gap open)."""
        from agents.literature_research_agent import RelevanceFilterExhausted

        papers = [{"title": "Paper A", "snippet": "..."}, {"title": "Paper B", "snippet": "..."}]
        with patch.object(literature_agent, "ask_llm", side_effect=RuntimeError("All providers exhausted")):
            with pytest.raises(RelevanceFilterExhausted):
                literature_agent._filter_relevant_papers(sample_project_name, "project text", papers)

        mock_notifier.send_admin_alert.assert_called_once()
        _, kwargs = mock_notifier.send_admin_alert.call_args
        assert sample_project_name in kwargs["subject"]

    def test_filter_relevant_papers_no_alert_on_non_waterfall_error(
        self, literature_agent, mock_notifier, sample_project_name
    ):
        """A non-waterfall failure (e.g. an unexpected parsing edge case) still
        degrades to unfiltered papers, but does NOT page an admin — only a full
        waterfall exhaustion does, since that's the systemic failure mode."""
        papers = [{"title": "Paper A", "snippet": "..."}]
        with patch.object(literature_agent, "ask_llm", side_effect=ValueError("unexpected")):
            result = literature_agent._filter_relevant_papers(sample_project_name, "project text", papers)

        assert result == papers
        mock_notifier.send_admin_alert.assert_not_called()

    def test_filter_relevant_papers_dedups_alert_for_same_project(
        self, literature_agent, mock_notifier, sample_project_name
    ):
        """Two waterfall-exhaustion failures for the SAME project in one run produce
        only one admin alert (shared BaseAgent dedup guard, same as every other
        waterfall-exhaustion call site)."""
        from agents.literature_research_agent import RelevanceFilterExhausted

        papers = [{"title": "Paper A", "snippet": "..."}]
        with patch.object(literature_agent, "ask_llm", side_effect=RuntimeError("exhausted")):
            with pytest.raises(RelevanceFilterExhausted):
                literature_agent._filter_relevant_papers(sample_project_name, "text", papers)
            with pytest.raises(RelevanceFilterExhausted):
                literature_agent._filter_relevant_papers(sample_project_name, "text", papers)

        mock_notifier.send_admin_alert.assert_called_once()


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
        with patch.object(literature_agent, "_read_project_text", return_value="Sample research text"), \
             patch.object(LiteratureResearchAgent, "_filter_dead_links", side_effect=lambda project, papers: papers):
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

    def test_read_project_text_excludes_hl_editorial_annotations(
        self, literature_agent, sample_project_name, tmp_path, monkeypatch
    ):
        """Amit's feedback originally targeted ProgressTrackingAgent specifically, but
        the same leakage affects this agent: an \\hl{...} editorial note (e.g. a
        collaborator's inline comment) left in the manuscript must not pollute the
        text used for keyword extraction — it isn't real manuscript content."""
        from config import Config

        project_dir = tmp_path / sample_project_name
        project_dir.mkdir()
        doc = (
            r"\section{Introduction} Real sentence about the core methodology used. "
            r"\hl{A: reviewer note, please rewrite this paragraph before submission}"
        )
        (project_dir / "main.tex").write_text(doc)
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))

        result = literature_agent._read_project_text(sample_project_name)

        assert "Real sentence about the core methodology" in result
        assert "reviewer note" not in result
        assert "\\hl" not in result

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
             patch.object(LiteratureResearchAgent, "_read_project_text", return_value="Sample research text"), \
             patch.object(LiteratureResearchAgent, "_filter_dead_links", side_effect=lambda project, papers: papers):

            agent = LiteratureResearchAgent(active_projects=[sample_project_name], notifier=mock_notifier)
            agent.run()

        # The keyword-extraction call happens first; the summarization call is the one
        # containing the paper data, identifiable by the "Here are the enriched results" marker.
        summarization_prompts = [p for p in captured_prompts if "Here are the enriched results" in p]
        assert summarization_prompts, "Expected a summarization prompt containing paper data"
        payload_prompt = summarization_prompts[0]

        assert "xxxxxxxxxx" * 500 not in payload_prompt  # the raw 5000-char abstract must not survive intact
        assert "…" in payload_prompt  # truncation marker present

    def test_run_respects_configured_max_workers(self, mock_notifier, sample_project_name, monkeypatch):
        """Config.LITERATURE_MAX_WORKERS must actually control ThreadPoolExecutor's
        concurrency, matching the pattern already used in ProgressTrackingAgent."""
        from config import Config

        monkeypatch.setattr(Config, "LITERATURE_MAX_WORKERS", 7)

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON), \
             patch("agents.literature_research_agent.ThreadPoolExecutor") as mock_executor_cls:
            # Make the mocked executor behave enough like the real one to not crash run().
            mock_executor_cls.return_value.__enter__.return_value.submit.return_value = MagicMock()
            mock_executor_cls.return_value.__exit__.return_value = False

            with patch("agents.literature_research_agent.as_completed", return_value=[]):
                agent = LiteratureResearchAgent(active_projects=[sample_project_name], notifier=mock_notifier)
                agent.run()

        mock_executor_cls.assert_called_once_with(max_workers=7)

    def test_process_project_caps_papers_at_configured_max(self, mock_notifier, sample_project_name, monkeypatch):
        """Config.MAX_LITERATURE_PAPERS must actually cap the number of papers carried
        forward, not the hardcoded literal 15."""
        from config import Config

        monkeypatch.setattr(Config, "MAX_LITERATURE_PAPERS", 3)

        many_papers = [
            {"title": f"Paper {i}", "abstract": "abstract text", "year": "2024",
             "citationCount": "1", "venue": "V", "link": "http://example.com"}
            for i in range(10)
        ]

        captured = {}

        def fake_filter_relevant(project, text, papers):
            captured["count_before_cap"] = len(papers)
            return papers

        def fake_enrich(papers):
            captured["count_after_cap"] = len(papers)
            return papers

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON), \
             patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=many_papers), \
             patch("utils.literature_fetcher.LiteratureFetcher.enrich_with_openalex", side_effect=fake_enrich), \
             patch.object(LiteratureResearchAgent, "_read_project_text", return_value="Sample research text"), \
             patch.object(LiteratureResearchAgent, "_filter_relevant_papers", side_effect=fake_filter_relevant):

            agent = LiteratureResearchAgent(active_projects=[sample_project_name], notifier=mock_notifier)
            agent.run()

        assert captured["count_before_cap"] > 3
        assert captured["count_after_cap"] == 3

    def test_process_project_skips_csv_and_email_on_relevance_filter_exhaustion(
        self, mock_notifier, sample_project_name
    ):
        """Part B fix: when the relevance filter itself waterfall-exhausts
        (RelevanceFilterExhausted), _process_project must skip the whole update for
        that cycle — no CSV append, no summary email — not just alert-and-degrade to
        unfiltered papers. Mirrors the existing 'all search sources failed' pattern
        (log_agent_run FAILURE, then return)."""
        from agents.literature_research_agent import RelevanceFilterExhausted

        papers = [{"title": "Paper A", "abstract": "...", "year": "2024",
                   "citationCount": "1", "venue": "V", "link": "http://example.com"}]

        mock_db = MagicMock()

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON), \
             patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=papers), \
             patch.object(LiteratureResearchAgent, "_read_project_text", return_value="Sample research text"), \
             patch.object(
                 LiteratureResearchAgent, "_filter_relevant_papers",
                 side_effect=RelevanceFilterExhausted("All providers exhausted")
             ), \
             patch.object(LiteratureResearchAgent, "process_results_with_llm") as mock_process_llm, \
             patch("utils.library_manager.LibraryManager.batch_append_to_project_literature_table") as mock_batch_append, \
             patch("utils.library_manager.LibraryManager.save_literature_summary") as mock_save_summary:

            agent = LiteratureResearchAgent(active_projects=[sample_project_name], notifier=mock_notifier, db=mock_db)
            agent._process_project(sample_project_name)

        # No downstream processing, no CSV write, no summary file, no email.
        mock_process_llm.assert_not_called()
        mock_batch_append.assert_not_called()
        mock_save_summary.assert_not_called()
        mock_notifier.send_literature_update.assert_not_called()

        # FAILURE logged the same way the "all search sources failed" branch does.
        failure_calls = [
            call for call in mock_db.log_agent_run.call_args_list
            if call.kwargs.get("status") == "FAILURE"
        ]
        assert failure_calls, "Expected a FAILURE status logged via db.log_agent_run"


class TestLinkValidityFilter:
    """Part C: a standing link-validity check applied to all_papers before they
    reach the CSV/email, independent of and in addition to the relevance filter."""

    @pytest.fixture
    def literature_agent(self, mock_notifier, sample_project_name):
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            with patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=MOCK_SEMANTIC_SCHOLAR_RESULTS):
                agent = LiteratureResearchAgent(
                    active_projects=[sample_project_name],
                    notifier=mock_notifier,
                )
                yield agent

    def _mock_response(self, status_code=200, text="", url="http://example.com/paper"):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        resp.url = url
        return resp

    def test_dead_link_connection_error_excluded(self, literature_agent, sample_project_name):
        """A genuinely dead link (connection error) must be excluded, not crash."""
        import requests as requests_module

        paper = {"title": "Some Real Paper Title", "link": "https://this-domain-does-not-resolve.invalid/paper"}

        with patch.object(
            requests_module, "get", side_effect=requests_module.exceptions.ConnectionError("refused")
        ):
            result = literature_agent._filter_dead_links(sample_project_name, [paper])

        assert result == []

    def test_dead_link_404_excluded(self, literature_agent, sample_project_name):
        """A live server responding 404 must be excluded."""
        paper = {"title": "Some Real Paper Title", "link": "https://example.com/gone"}

        with patch("agents.literature_research_agent.requests.get", return_value=self._mock_response(status_code=404)):
            result = literature_agent._filter_dead_links(sample_project_name, [paper])

        assert result == []

    def test_200_but_empty_content_excluded(self, literature_agent, sample_project_name):
        """A 200 response with a near-empty/garbage body (e.g. a blank parked page)
        must still be excluded — status alone is not enough."""
        paper = {"title": "Some Real Paper Title", "link": "https://example.com/blank"}

        with patch("agents.literature_research_agent.requests.get", return_value=self._mock_response(status_code=200, text="ok")):
            result = literature_agent._filter_dead_links(sample_project_name, [paper])

        assert result == []

    def test_200_boilerplate_error_page_excluded(self, literature_agent, sample_project_name):
        """A 200 response whose body is actually a 'page not found' style error page
        (some CMSes return 200 for these) must be excluded via the content-signal check."""
        paper = {"title": "Some Real Paper Title", "link": "https://example.com/soft404"}
        body = "<html><body>" + ("Sorry, the requested page could not be found. " * 20) + "</body></html>"

        with patch("agents.literature_research_agent.requests.get", return_value=self._mock_response(status_code=200, text=body)):
            result = literature_agent._filter_dead_links(sample_project_name, [paper])

        assert result == []

    def test_good_link_kept(self, literature_agent, sample_project_name):
        """A real, healthy link (200 status, substantial real-looking body) must be kept."""
        paper = {"title": "Deep Learning for Network Traffic Classification", "link": "https://example.com/real-paper"}
        body = (
            "<html><head><title>Deep Learning for Network Traffic Classification</title></head>"
            "<body><h1>Deep Learning for Network Traffic Classification</h1>"
            + ("This paper presents a novel approach to network traffic classification. " * 20)
            + "</body></html>"
        )

        with patch("agents.literature_research_agent.requests.get", return_value=self._mock_response(status_code=200, text=body)):
            result = literature_agent._filter_dead_links(sample_project_name, [paper])

        assert result == [paper]

    def test_missing_link_excluded(self, literature_agent, sample_project_name):
        """A paper with no link field at all can't be validated, so it's excluded
        (fail-closed) rather than passed through unverified."""
        paper = {"title": "No Link Paper"}
        result = literature_agent._filter_dead_links(sample_project_name, [paper])
        assert result == []

    def test_mixed_batch_keeps_only_valid_ones_and_preserves_order(self, literature_agent, sample_project_name):
        """Given a mix of dead and good links, only the good ones survive, in their
        original relative order (not scrambled by the thread pool)."""
        good_body = (
            "<html><body>" + ("Real paper content about a real research topic. " * 20) + "</body></html>"
        )
        papers = [
            {"title": "Paper 1 Good", "link": "https://example.com/1"},
            {"title": "Paper 2 Dead", "link": "https://example.com/2"},
            {"title": "Paper 3 Good", "link": "https://example.com/3"},
        ]

        def fake_get(url, headers=None, timeout=None, allow_redirects=None):
            if url == "https://example.com/2":
                return self._mock_response(status_code=404)
            return self._mock_response(status_code=200, text=good_body)

        with patch("agents.literature_research_agent.requests.get", side_effect=fake_get):
            result = literature_agent._filter_dead_links(sample_project_name, papers)

        assert [p["title"] for p in result] == ["Paper 1 Good", "Paper 3 Good"]

    def test_process_project_excludes_dead_link_before_csv_write(self, mock_notifier, sample_project_name):
        """End-to-end: a paper with a dead link must not survive into the papers
        passed to process_results_with_llm (and therefore not into the CSV)."""
        papers_from_search = [
            {"title": "Dead Link Paper", "abstract": "abstract", "year": "2024",
             "citationCount": "1", "venue": "V", "link": "https://example.com/dead"},
        ]

        captured = {}

        def fake_process_llm(project, keywords, scholar_data):
            captured["scholar_data"] = scholar_data
            return {"summary": "s", "papers": []}

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON), \
             patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=papers_from_search), \
             patch("utils.literature_fetcher.LiteratureFetcher.enrich_with_openalex", side_effect=lambda papers: papers), \
             patch.object(LiteratureResearchAgent, "_read_project_text", return_value="Sample research text"), \
             patch.object(LiteratureResearchAgent, "process_results_with_llm", side_effect=fake_process_llm), \
             patch("agents.literature_research_agent.requests.get", return_value=self._mock_response(status_code=404)):

            agent = LiteratureResearchAgent(active_projects=[sample_project_name], notifier=mock_notifier)
            agent.run()

        # process_results_with_llm never got called at all because the only
        # paper was excluded by the link check before reaching that stage.
        assert "scholar_data" not in captured
        mock_notifier.send_literature_update.assert_not_called()

    def test_semanticscholar_org_empty_body_2xx_is_kept_not_excluded(self, literature_agent, sample_project_name):
        """Regression test for a real production bug found during review: Semantic
        Scholar (this agent's PRIMARY search source, see utils/literature_fetcher.py)
        serves its paper pages as a client-rendered SPA that returns a 2xx status
        with an EMPTY body to a plain scripted request (confirmed live: a real,
        valid semanticscholar.org/paper/<id> URL returns HTTP 202 with a 0-byte
        body to requests.get()). Applying the generic body-length content check to
        this domain would exclude nearly every paper the pipeline finds through its
        default path, silently defeating the whole literature pipeline. This paper
        must be KEPT despite the empty body, because semanticscholar.org is a
        structurally-trusted domain (see _LINK_CHECK_SPA_TRUSTED_DOMAINS)."""
        paper = {
            "title": "Analysis and Evaluation of Post-Quantum Cryptography for DNSSEC",
            "link": "https://www.semanticscholar.org/paper/1089a3f5c8a7844e6acb3ce822390a280d1d4033",
        }

        with patch("agents.literature_research_agent.requests.get", return_value=self._mock_response(status_code=202, text="")):
            result = literature_agent._filter_dead_links(sample_project_name, [paper])

        assert result == [paper]

    def test_semanticscholar_org_dead_status_still_excluded(self, literature_agent, sample_project_name):
        """The trusted-domain exception only skips the CONTENT check, not the
        status check — a genuinely broken semanticscholar.org link (non-2xx) must
        still be excluded like any other domain."""
        paper = {"title": "Some Paper", "link": "https://www.semanticscholar.org/paper/doesnotexist"}

        with patch("agents.literature_research_agent.requests.get", return_value=self._mock_response(status_code=404, text="")):
            result = literature_agent._filter_dead_links(sample_project_name, [paper])

        assert result == []

    def test_non_semanticscholar_empty_body_2xx_still_excluded(self, literature_agent, sample_project_name):
        """The trusted-domain exception is narrowly scoped to semanticscholar.org —
        a random other domain returning a 2xx with an empty body is still excluded
        by the content check, exactly as before this fix."""
        paper = {"title": "Some Paper", "link": "https://example.com/spa-clone"}

        with patch("agents.literature_research_agent.requests.get", return_value=self._mock_response(status_code=200, text="")):
            result = literature_agent._filter_dead_links(sample_project_name, [paper])

        assert result == []


class TestPaperMetadataKeyAlignment:
    """Amit's feedback: the generated CSV's per-paper metadata was very sparse. Two
    of the fields ("how complicated is it?" and "can i control the application
    collected?") were blank in 100% of rows across BOTH real test projects' actual
    rolling CSVs — not because the source data was unavailable, but because the LLM
    prompt asked for JSON keys ("complexity", and "can i control the application
    collected" with no trailing "?") that didn't match domain.schemas.PaperData's
    aliases, so Pydantic silently applied the (previously blank) field defaults on
    every single paper regardless of what the LLM actually knew."""

    @pytest.fixture
    def literature_agent(self, mock_notifier, sample_project_name):
        """Same construction as TestLiteratureResearchAgent.literature_agent — this
        class needs its own copy since pytest fixtures aren't inherited across
        sibling test classes."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            with patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=MOCK_SEMANTIC_SCHOLAR_RESULTS):
                agent = LiteratureResearchAgent(
                    active_projects=[sample_project_name],
                    notifier=mock_notifier,
                )
                yield agent

    def test_prompt_requests_keys_matching_the_schema_aliases(self, literature_agent, sample_project_name):
        """The exact JSON keys asked for in the prompt must match
        domain.schemas.PaperData's aliases byte-for-byte, or Pydantic will never
        populate them from the LLM's response."""
        from domain.schemas import PaperData

        captured = {}

        def _capture(prompt, *a, **kw):
            captured["prompt"] = prompt
            return '{"summary": "' + ("x" * 60) + '", "papers": []}'

        with patch.object(literature_agent, "ask_llm", side_effect=_capture):
            literature_agent.process_results_with_llm(sample_project_name, "kw", [{"title": "t"}])

        prompt = captured["prompt"]
        for name, field_info in PaperData.model_fields.items():
            # PaperData has model_config = ConfigDict(populate_by_name=True), so a
            # prompt key matching EITHER the alias (e.g. "year published") OR the
            # bare field name (e.g. "year_published") populates the field correctly
            # — the prompt legitimately mixes both forms (paper_name, year_published
            # use the field-name form; most others use the alias form). Only a key
            # matching NEITHER is the drift bug this test guards against.
            candidates = {f'"{name}"'}
            if field_info.alias:
                candidates.add(f'"{field_info.alias}"')
            assert any(c in prompt for c in candidates), (
                f"prompt uses neither the field name nor the alias for {name!r} "
                f"(tried {candidates}) — Pydantic will never populate this field"
            )
        # The two keys that previously drifted must be the EXACT alias strings,
        # not the old mismatched ones.
        assert '"how complicated is it?"' in prompt
        assert '"complexity"' not in prompt
        assert '"can i control the application collected?"' in prompt
        # old key (no trailing "?") must not appear as a JSON key of its own
        assert '"can i control the application collected"' not in prompt.replace(
            '"can i control the application collected?"', ""
        )

    def test_paper_data_defaults_to_na_not_blank_for_previously_drifted_fields(self):
        """Even if a future prompt/schema drift happens again, the default for every
        field (including these two) should read as 'not provided' (N/A), not as a
        truly blank cell that looks like a malfunction."""
        from domain.schemas import PaperData

        paper = PaperData(**{"paper name": "Some Paper"})
        assert paper.how_complicated == "N/A"
        assert paper.can_control == "N/A"

    def test_paper_data_populates_from_corrected_alias_keys(self):
        """End-to-end: JSON using the corrected (schema-matching) keys actually
        populates the fields — this is what was broken before the key fix."""
        from domain.schemas import PaperData

        paper = PaperData(**{
            "paper name": "Some Paper",
            "how complicated is it?": "High",
            "can i control the application collected?": "Yes",
        })
        assert paper.how_complicated == "High"
        assert paper.can_control == "Yes"

    def test_prompt_instructs_explicit_not_available_marker_instead_of_fabrication(
        self, literature_agent, sample_project_name
    ):
        """Presentation fix: genuinely-unavailable source fields must be marked
        explicitly ('N/A (Not available at source)') rather than left blank or
        guessed — this is a clarity change, not a relaxation of the no-fabrication
        rule (the instruction explicitly forbids guessing)."""
        captured = {}

        def _capture(prompt, *a, **kw):
            captured["prompt"] = prompt
            return '{"summary": "' + ("x" * 60) + '", "papers": []}'

        with patch.object(literature_agent, "ask_llm", side_effect=_capture):
            literature_agent.process_results_with_llm(sample_project_name, "kw", [{"title": "t"}])

        prompt = captured["prompt"]
        assert "N/A (Not available at source)" in prompt
        assert "NEVER invent" in prompt or "never invent" in prompt.lower()
