"""Integration tests for the SerpAPI + scholarly fallback search pipeline."""

from unittest.mock import MagicMock, patch, call

import pytest

from config import Config
from agents.literature_research_agent import LiteratureResearchAgent
from tests.fixtures.mock_responses import VALID_LITERATURE_JSON
from utils.literature_fetcher import LiteratureFetcher


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fetcher():
    return LiteratureFetcher()


def _make_serpapi_response(results):
    """Build a valid SerpAPI mock response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"organic_results": results}
    return mock_resp


def _make_serpapi_item(title, snippet, year_in_summary, cited_total, link):
    return {
        "title": title,
        "snippet": snippet,
        "publication_info": {"summary": f"Journal, {year_in_summary}"},
        "inline_links": {"cited_by": {"total": cited_total}},
        "link": link,
    }


def _make_scholarly_pub(title, abstract, pub_year, num_citations, venue, pub_url):
    return {
        "bib": {
            "title": title,
            "abstract": abstract,
            "pub_year": pub_year,
            "venue": venue,
        },
        "num_citations": num_citations,
        "pub_url": pub_url,
    }


# ---------------------------------------------------------------------------
# SerpAPI tests
# ---------------------------------------------------------------------------


class TestSerpAPIFetcher:
    """Unit tests for _fetch_from_serpapi in LiteratureFetcher."""

    def test_serpapi_returns_papers_on_success(self, fetcher):
        """A valid SerpAPI response with 3 results returns 3 paper dicts."""
        items = [
            _make_serpapi_item("Paper Alpha", "Alpha snippet", "2024", 10, "https://a.com"),
            _make_serpapi_item("Paper Beta", "Beta snippet", "2023", 25, "https://b.com"),
            _make_serpapi_item("Paper Gamma", "Gamma snippet", "2022", 5, "https://c.com"),
        ]

        with patch.object(Config, "SERPAPI_API_KEY", "test_serpapi_key"):
            with patch(
                "utils.literature_fetcher.requests.get",
                return_value=_make_serpapi_response(items),
            ):
                result = fetcher.fetch_from_serpapi("test query")

        assert len(result) == 3
        assert result[0]["title"] == "Paper Alpha"
        assert result[0]["abstract"] == "Alpha snippet"
        assert result[1]["citationCount"] == "25"
        assert result[2]["year"] == "2022"

    def test_serpapi_returns_empty_on_missing_key(self, fetcher):
        """When SERPAPI_API_KEY is empty, no HTTP request is made and [] is returned."""
        with patch.object(Config, "SERPAPI_API_KEY", ""):
            with patch("utils.literature_fetcher.requests.get") as mock_get:
                result = fetcher.fetch_from_serpapi("any keywords")
                mock_get.assert_not_called()

        assert result == []

    def test_serpapi_returns_empty_on_http_error(self, fetcher):
        """A non-200 HTTP response returns [] without raising."""
        error_resp = MagicMock()
        error_resp.status_code = 429

        with patch.object(Config, "SERPAPI_API_KEY", "test_key"):
            with patch(
                "utils.literature_fetcher.requests.get", return_value=error_resp
            ):
                result = fetcher.fetch_from_serpapi("some keywords")

        assert result == []

    def test_serpapi_skips_items_with_empty_title(self, fetcher):
        """Items with an empty title are excluded from results."""
        items = [
            _make_serpapi_item("", "No title snippet", "2024", 0, "https://x.com"),
            _make_serpapi_item("Valid Paper", "Valid snippet", "2024", 3, "https://y.com"),
        ]

        with patch.object(Config, "SERPAPI_API_KEY", "test_key"):
            with patch(
                "utils.literature_fetcher.requests.get",
                return_value=_make_serpapi_response(items),
            ):
                result = fetcher.fetch_from_serpapi("keywords")

        assert len(result) == 1
        assert result[0]["title"] == "Valid Paper"


# ---------------------------------------------------------------------------
# scholarly tests
# ---------------------------------------------------------------------------


class TestScholarlyFetcher:
    """Unit tests for _fetch_from_scholarly in LiteratureFetcher."""

    def test_scholarly_returns_papers_on_success(self, fetcher):
        """A valid scholarly search returns correctly mapped paper dicts."""
        pubs = [
            _make_scholarly_pub("Pub One", "Abstract one", 2024, 12, "Journal A", "https://one.com"),
            _make_scholarly_pub("Pub Two", "Abstract two", 2023, 4, "Journal B", "https://two.com"),
            _make_scholarly_pub("Pub Three", "Abstract three", 2022, 0, "", "https://three.com"),
        ]

        with patch("utils.literature_fetcher.SCHOLARLY_AVAILABLE", True):
            with patch(
                "utils.literature_fetcher._scholarly_lib"
            ) as mock_scholarly:
                mock_scholarly.search_pubs.return_value = iter(pubs)
                result = fetcher.fetch_from_scholarly("test keywords")

        assert len(result) == 3
        assert result[0]["title"] == "Pub One"
        assert result[0]["abstract"] == "Abstract one"
        assert result[0]["year"] == "2024"
        assert result[0]["citationCount"] == "12"
        assert result[0]["venue"] == "Journal A"
        assert result[0]["url"] == "https://one.com"

    def test_scholarly_returns_empty_when_unavailable(self, fetcher):
        """When SCHOLARLY_AVAILABLE is False, no scholarly call is made and [] is returned."""
        with patch("utils.literature_fetcher.SCHOLARLY_AVAILABLE", False):
            with patch("utils.literature_fetcher._scholarly_lib") as mock_scholarly:
                result = fetcher.fetch_from_scholarly("some keywords")
                mock_scholarly.search_pubs.assert_not_called()

        assert result == []

    def test_scholarly_caps_at_8_papers(self, fetcher):
        """Even if search_pubs yields 20 pubs, only 8 are returned."""
        pubs = [
            _make_scholarly_pub(f"Paper {i}", f"Abstract {i}", 2024, i, "Venue", f"https://p{i}.com")
            for i in range(20)
        ]

        with patch("utils.literature_fetcher.SCHOLARLY_AVAILABLE", True):
            with patch("utils.literature_fetcher._scholarly_lib") as mock_scholarly:
                mock_scholarly.search_pubs.return_value = iter(pubs)
                result = fetcher.fetch_from_scholarly("keywords")

        assert len(result) == 8

    def test_scholarly_handles_exception_gracefully(self, fetcher):
        """An exception from search_pubs is caught and [] is returned."""
        with patch("utils.literature_fetcher.SCHOLARLY_AVAILABLE", True):
            with patch("utils.literature_fetcher._scholarly_lib") as mock_scholarly:
                mock_scholarly.search_pubs.side_effect = Exception("blocked by bot detection")
                result = fetcher.fetch_from_scholarly("keywords")

        assert result == []

    def test_scholarly_skips_pubs_with_empty_title(self, fetcher):
        """Publications with empty title are excluded from results."""
        pubs = [
            _make_scholarly_pub("", "No title", 2024, 0, "", ""),
            _make_scholarly_pub("Real Paper", "Has title", 2024, 5, "J", "https://r.com"),
        ]

        with patch("utils.literature_fetcher.SCHOLARLY_AVAILABLE", True):
            with patch("utils.literature_fetcher._scholarly_lib") as mock_scholarly:
                mock_scholarly.search_pubs.return_value = iter(pubs)
                result = fetcher.fetch_from_scholarly("keywords")

        assert len(result) == 1
        assert result[0]["title"] == "Real Paper"


# ---------------------------------------------------------------------------
# Pipeline-level fallback tests (LiteratureResearchAgent.run())
# ---------------------------------------------------------------------------


def _make_fallback_papers(count=3, prefix="Paper"):
    return [
        {
            "title": f"{prefix} {i}",
            "abstract": f"Abstract about topic {i}.",
            "year": "2024",
            "citationCount": str(i * 3),
            "venue": "Journal",
            "url": f"https://example.com/{i}",
            "openalex": {},
        }
        for i in range(count)
    ]


class TestPipelineFallbackBehavior:
    """End-to-end tests verifying fallback chain in LiteratureResearchAgent.run()."""

    def test_pipeline_uses_serpapi_when_semantic_scholar_empty(
        self, mock_notifier, sample_project_name
    ):
        """When all Semantic Scholar queries return [], SerpAPI is tried and email is sent."""
        serpapi_papers = _make_fallback_papers(3, "SerpAPI")
        enriched = [dict(p, openalex={}) for p in serpapi_papers]

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            agent = LiteratureResearchAgent(
                active_projects=[sample_project_name],
                notifier=mock_notifier,
            )

        with patch.object(agent, "_read_project_text", return_value="Sample research text"):
            with patch.object(agent.fetcher, "search", return_value=[]):
                with patch.object(
                    agent.fetcher, "fetch_from_serpapi", return_value=serpapi_papers
                ) as mock_serpapi:
                    with patch.object(
                        agent.fetcher, "enrich_with_openalex", return_value=enriched
                    ):
                        with patch.object(agent.library, "save_literature_summary"):
                            with patch.object(
                                agent.library, "append_to_project_literature_table"
                            ):
                                with patch(
                                    "agents.base_agent.BaseAgent.ask_llm",
                                    return_value=VALID_LITERATURE_JSON,
                                ):
                                    agent.run()

        mock_serpapi.assert_called_once()
        mock_notifier.send_literature_update.assert_called_once()

    def test_pipeline_uses_scholarly_when_serpapi_also_empty(
        self, mock_notifier, sample_project_name
    ):
        """When Semantic Scholar and SerpAPI both return [], scholarly is tried and email is sent."""
        scholarly_papers = _make_fallback_papers(3, "Scholarly")
        enriched = [dict(p, openalex={}) for p in scholarly_papers]

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            agent = LiteratureResearchAgent(
                active_projects=[sample_project_name],
                notifier=mock_notifier,
            )

        with patch.object(agent, "_read_project_text", return_value="Sample research text"):
            with patch.object(agent.fetcher, "search", return_value=[]):
                with patch.object(agent.fetcher, "fetch_from_serpapi", return_value=[]):
                    with patch.object(
                        agent.fetcher, "fetch_from_scholarly", return_value=scholarly_papers
                    ) as mock_scholarly:
                        with patch.object(
                            agent.fetcher, "enrich_with_openalex", return_value=enriched
                        ):
                            with patch.object(agent.library, "save_literature_summary"):
                                with patch.object(
                                    agent.library, "append_to_project_literature_table"
                                ):
                                    with patch(
                                        "agents.base_agent.BaseAgent.ask_llm",
                                        return_value=VALID_LITERATURE_JSON,
                                    ):
                                        agent.run()

        mock_scholarly.assert_called_once()
        mock_notifier.send_literature_update.assert_called_once()

    def test_pipeline_logs_error_when_all_sources_fail(
        self, mock_notifier, sample_project_name
    ):
        """When all three search sources return [], logger.error is called and no email is sent."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            agent = LiteratureResearchAgent(
                active_projects=[sample_project_name],
                notifier=mock_notifier,
            )

        with patch.object(agent, "_read_project_text", return_value="Sample research text"):
            with patch.object(agent.fetcher, "search", return_value=[]):
                with patch.object(agent.fetcher, "fetch_from_serpapi", return_value=[]):
                    with patch.object(agent.fetcher, "fetch_from_scholarly", return_value=[]):
                        with patch.object(agent.logger, "error") as mock_error:
                            agent.run()

        mock_notifier.send_literature_update.assert_not_called()
        assert mock_error.called
        error_messages = [call.args[0] for call in mock_error.call_args_list]
        assert any("All search sources failed" in msg for msg in error_messages)
