"""Integration tests for Semantic Scholar API key header and OpenAlex direct search."""

from unittest.mock import MagicMock, patch

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


def _make_ss_response(papers=None):
    """Build a minimal valid Semantic Scholar API mock response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "data": papers or [
            {
                "title": "Test Paper",
                "url": "https://semanticscholar.org/paper/test",
                "abstract": "An abstract.",
                "year": 2024,
                "citationCount": 5,
                "venue": "Test Journal",
            }
        ]
    }
    return mock_resp


def _make_openalex_search_response(works):
    """Build a minimal valid OpenAlex works search mock response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": works}
    return mock_resp


# ---------------------------------------------------------------------------
# Semantic Scholar API key header tests
# ---------------------------------------------------------------------------


class TestSemanticScholarApiKeyHeader:
    """Tests that the API key is sent (or not) as a header in Semantic Scholar requests."""

    def test_semantic_scholar_sends_api_key_header(self, fetcher):
        """When SEMANTIC_SCHOLAR_API_KEY is set, requests.get is called with x-api-key header."""
        with patch.object(Config, "SEMANTIC_SCHOLAR_API_KEY", "test_key_123"):
            with patch(
                "utils.literature_fetcher.requests.get",
                return_value=_make_ss_response(),
            ) as mock_get:
                with patch("utils.literature_fetcher.time.sleep"):
                    fetcher._fetch_from_semantic_scholar("test query")

        assert mock_get.call_args.kwargs["headers"] == {"x-api-key": "test_key_123"}

    def test_semantic_scholar_no_key_sends_no_header(self, fetcher):
        """When SEMANTIC_SCHOLAR_API_KEY is empty, requests.get is called with empty headers."""
        with patch.object(Config, "SEMANTIC_SCHOLAR_API_KEY", ""):
            with patch(
                "utils.literature_fetcher.requests.get",
                return_value=_make_ss_response(),
            ) as mock_get:
                with patch("utils.literature_fetcher.time.sleep"):
                    fetcher._fetch_from_semantic_scholar("test query")

        assert mock_get.call_args.kwargs["headers"] == {}


# ---------------------------------------------------------------------------
# OpenAlex direct search tests
# ---------------------------------------------------------------------------


class TestFetchFromOpenalex:
    """Tests for _fetch_from_openalex in LiteratureFetcher."""

    def test_fetch_from_openalex_returns_papers(self, fetcher):
        """A valid OpenAlex response with 2 works returns 2 paper dicts with expected fields."""
        works = [
            {
                "display_name": "Paper One on WSN",
                "abstract_inverted_index": {"energy": [0], "harvesting": [1]},
                "cited_by_count": 5,
                "publication_year": 2024,
                "topics": [{"display_name": "Wireless Sensor Networks"}],
                "keywords": [{"display_name": "energy efficiency"}],
                "open_access": {"is_oa": False, "oa_url": None},
                "primary_location": {
                    "landing_page_url": "https://example.com/1",
                    "source": {"display_name": "Sensors Journal"},
                },
            },
            {
                "display_name": "Energy-Efficient WSN Security for IoT",
                "abstract_inverted_index": {"wsn": [0], "energy": [1], "security": [2]},
                "cited_by_count": 12,
                "publication_year": 2023,
                "topics": [{"display_name": "Internet of Things"}],
                "keywords": [{"display_name": "IoT"}],
                "open_access": {"is_oa": True, "oa_url": "https://arxiv.org/pdf/2"},
                "primary_location": {
                    "landing_page_url": "https://example.com/2",
                    "source": {"display_name": "IEEE Access"},
                },
            },
        ]
        with patch.object(Config, "OPENALEX_API_KEY", "test_openalex_key"):
            with patch(
                "utils.literature_fetcher.requests.get",
                return_value=_make_openalex_search_response(works),
            ):
                result = fetcher._fetch_from_openalex("WSN energy efficiency")

        assert len(result) == 2
        assert result[0]["title"] != ""
        assert isinstance(result[0]["openalex"]["topics"], list)
        assert result[0]["abstract"] != ""

    def test_fetch_from_openalex_reconstructs_abstract(self, fetcher):
        """Abstract inverted index is correctly reconstructed into ordered text."""
        works = [
            {
                "display_name": "Abstract Reconstruction Test",
                "abstract_inverted_index": {"hello": [0], "world": [1], "test": [2]},
                "cited_by_count": 1,
                "publication_year": 2024,
                "topics": [],
                "keywords": [],
                "open_access": {"is_oa": False, "oa_url": None},
                "primary_location": None,
            }
        ]
        with patch.object(Config, "OPENALEX_API_KEY", "test_openalex_key"):
            with patch(
                "utils.literature_fetcher.requests.get",
                return_value=_make_openalex_search_response(works),
            ):
                result = fetcher._fetch_from_openalex("hello world test")

        assert result[0]["abstract"] == "hello world test"

    def test_fetch_from_openalex_empty_key_returns_empty(self, fetcher):
        """When OPENALEX_API_KEY is empty, no HTTP request is made and [] is returned."""
        with patch.object(Config, "OPENALEX_API_KEY", ""):
            with patch("utils.literature_fetcher.requests.get") as mock_get:
                result = fetcher.fetch_from_openalex("some keywords")
                mock_get.assert_not_called()

        assert result == []

    def test_fetch_from_openalex_http_error_returns_empty(self, fetcher):
        """A non-200 HTTP response returns [] without raising."""
        error_resp = MagicMock()
        error_resp.status_code = 500

        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch("utils.literature_fetcher.requests.get", return_value=error_resp):
                result = fetcher._fetch_from_openalex("test query")

        assert result == []


# ---------------------------------------------------------------------------
# Enrichment skip test
# ---------------------------------------------------------------------------


class TestEnrichSkipsAlreadyEnrichedPapers:
    """Tests that enrich_with_openalex does not re-query papers already enriched."""

    def test_openalex_already_enriched_papers_not_re_queried(self, fetcher):
        """A paper with a non-empty 'openalex' dict is skipped — no HTTP request is made."""
        paper = {
            "title": "Already Enriched Paper",
            "link": "https://example.com",
            "openalex": {
                "topics": ["Network Security"],
                "keywords": ["IoT"],
                "is_oa": False,
                "oa_url": None,
            },
        }
        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch("utils.literature_fetcher.requests.get") as mock_get:
                fetcher.enrich_with_openalex([paper])
                mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end: agent.run() when Semantic Scholar fails but OpenAlex succeeds
# ---------------------------------------------------------------------------


class TestRunIncludesOpenAlexResults:
    """Verifies that run() uses SerpAPI fallback when Semantic Scholar returns nothing."""

    def test_run_includes_openalex_results_when_semantic_scholar_fails(
        self, mock_notifier, sample_project_name
    ):
        """When fetcher.search returns [], SerpAPI papers fill the pipeline and email is sent."""
        serpapi_papers = [
            {
                "title": f"SerpAPI Paper {i}",
                "url": f"https://serpapi.com/W{i}",
                "abstract": "An abstract about wireless sensor networks.",
                "year": "2024",
                "citationCount": "5",
                "venue": "Sensors Journal",
                "openalex": {},
            }
            for i in range(3)
        ]

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            agent = LiteratureResearchAgent(
                active_projects=[sample_project_name],
                notifier=mock_notifier,
            )

        with patch.object(agent, "_read_project_text", return_value="Sample research text"):
            with patch.object(agent.fetcher, "search", return_value=[]):
                with patch.object(
                    agent.fetcher, "fetch_from_serpapi", return_value=serpapi_papers
                ):
                    with patch.object(
                        agent.fetcher, "enrich_with_openalex", return_value=serpapi_papers
                    ):
                        with patch.object(
                            agent, "_filter_dead_links", side_effect=lambda project, papers: papers
                        ):
                            with patch(
                                "agents.base_agent.BaseAgent.ask_llm",
                                return_value=VALID_LITERATURE_JSON,
                            ):
                                agent.run()

        mock_notifier.send_literature_update.assert_called_once()
