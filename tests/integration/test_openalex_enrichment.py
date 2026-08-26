"""Integration tests for OpenAlex enrichment layer in LiteratureFetcher."""

from unittest.mock import MagicMock, patch

import pytest

from config import Config
from agents.literature_research_agent import LiteratureResearchAgent
from domain.schemas import PaperData
from tests.fixtures.mock_responses import MOCK_SEMANTIC_SCHOLAR_RESULTS, VALID_LITERATURE_JSON
from utils.library_manager import LibraryManager
from utils.literature_fetcher import LiteratureFetcher


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fetcher():
    return LiteratureFetcher()


@pytest.fixture
def sample_papers():
    return [
        {
            "title": "Test Paper on Network Traffic",
            "link": "https://example.com/paper1",
            "snippet": "Abstract text about classification.",
            "year": "2024",
            "citationCount": "10",
            "venue": "IEEE Journal",
        }
    ]


@pytest.fixture
def openalex_success_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "topics": [
                    {"display_name": "Network Security"},
                    {"display_name": "Machine Learning"},
                ],
                "keywords": [
                    {"display_name": "IoT"},
                    {"display_name": "traffic classification"},
                ],
                "open_access": {"is_oa": False, "oa_url": None},
            }
        ]
    }
    return mock_resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenAlexEnrichment:
    """Tests for _enrich_with_openalex / enrich_with_openalex in LiteratureFetcher."""

    def test_enrich_with_openalex_returns_topics_and_keywords(
        self, fetcher, sample_papers, openalex_success_response
    ):
        """OpenAlex response with topics/keywords is correctly mapped into paper dict."""
        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch("utils.literature_fetcher.requests.get", return_value=openalex_success_response):
                with patch("utils.literature_fetcher.time.sleep"):
                    result = fetcher.enrich_with_openalex(sample_papers)

        assert len(result[0]["openalex"]["topics"]) > 0
        assert isinstance(result[0]["openalex"]["keywords"], list)

    def test_enrich_with_openalex_empty_api_key_skips_enrichment(
        self, fetcher, sample_papers
    ):
        """When OPENALEX_API_KEY is empty, no HTTP request is made and openalex=={} for each paper."""
        with patch.object(Config, "OPENALEX_API_KEY", ""):
            with patch("utils.literature_fetcher.requests.get") as mock_get:
                result = fetcher.enrich_with_openalex(sample_papers)
                mock_get.assert_not_called()

        assert result[0]["openalex"] == {}

    def test_enrich_with_openalex_http_error_returns_empty_dict(
        self, fetcher, sample_papers
    ):
        """A non-200 HTTP response sets openalex=={} without raising."""
        error_response = MagicMock()
        error_response.status_code = 500

        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch("utils.literature_fetcher.requests.get", return_value=error_response):
                with patch("utils.literature_fetcher.time.sleep"):
                    result = fetcher.enrich_with_openalex(sample_papers)

        assert result[0]["openalex"] == {}

    def test_enrich_with_openalex_no_results_returns_empty_dict(
        self, fetcher, sample_papers
    ):
        """An empty results list from OpenAlex sets openalex=={} without raising."""
        empty_response = MagicMock()
        empty_response.status_code = 200
        empty_response.json.return_value = {"results": []}

        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch("utils.literature_fetcher.requests.get", return_value=empty_response):
                with patch("utils.literature_fetcher.time.sleep"):
                    result = fetcher.enrich_with_openalex(sample_papers)

        assert result[0]["openalex"] == {}

    def test_enrich_with_openalex_429_enters_cooldown_and_stops_calling(
        self, fetcher, sample_papers
    ):
        """A 429 must stop enrichment for the rest of the batch instead of retrying
        every remaining paper against a provider already known to be rate limited."""
        rate_limited_response = MagicMock()
        rate_limited_response.status_code = 429
        two_papers = sample_papers + [{**sample_papers[0], "title": "Second Paper"}]

        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch(
                "utils.literature_fetcher.requests.get", return_value=rate_limited_response
            ) as mock_get:
                with patch("utils.literature_fetcher.time.sleep"):
                    result = fetcher.enrich_with_openalex(two_papers)

        assert result[0]["openalex"] == {}
        assert result[1]["openalex"] == {}
        assert fetcher._cooldown_remaining("openalex") > 0
        # Second paper's iteration should see the cooldown and skip its own call.
        assert mock_get.call_count == 1

    def test_enrich_with_openalex_is_oa_true_sets_oa_url(
        self, fetcher, sample_papers
    ):
        """When is_oa is True, the oa_url is correctly stored in the openalex dict."""
        oa_response = MagicMock()
        oa_response.status_code = 200
        oa_response.json.return_value = {
            "results": [
                {
                    "topics": [],
                    "keywords": [],
                    "open_access": {
                        "is_oa": True,
                        "oa_url": "https://arxiv.org/pdf/test",
                    },
                }
            ]
        }

        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch("utils.literature_fetcher.requests.get", return_value=oa_response):
                with patch("utils.literature_fetcher.time.sleep"):
                    result = fetcher.enrich_with_openalex(sample_papers)

        assert result[0]["openalex"]["is_oa"] is True
        assert result[0]["openalex"]["oa_url"] == "https://arxiv.org/pdf/test"


# ---------------------------------------------------------------------------
# Schema / CSV column tests
# ---------------------------------------------------------------------------


class TestDataRepresentationColumn:
    """Tests for the new 'data representation' CSV column and schema field."""

    def test_data_representation_column_in_csv(self, tmp_path):
        """Appending a paper with 'data representation' writes and reads the column correctly."""
        library = LibraryManager(base_dir=str(tmp_path))
        paper = {
            "paper name": "Sample Paper",
            "cited": "5",
            "source": "https://example.com",
            "year published": "2024",
            "types of available data": "network traffic",
            "number of samples": "N/A",
            "number of features": "N/A",
            "number of classes": "N/A",
            "location": "N/A",
            "for how long": "N/A",
            "is it reproducible?": "No",
            "how complicated is it?": "",
            "is there privacy issues?": "No",
            "data representation": "feature vector",
            "can i control the application collected?": "",
        }
        library.append_to_project_literature_table("TestProject", paper)

        import pandas as pd
        csv_path = tmp_path / "comparison_tables" / "TestProject" / "TestProject_rolling_table.csv"
        df = pd.read_csv(csv_path)

        assert "data representation" in df.columns
        assert df.iloc[0]["data representation"] == "feature vector"

    def test_paper_data_schema_data_representation_field(self):
        """PaperData accepts 'data representation' alias and maps to data_representation."""
        paper = PaperData.model_validate({"data representation": "graph"})
        assert paper.data_representation == "graph"

    def test_paper_data_empty_columns_are_empty_string(self):
        """how_complicated and can_control default to 'N/A' when not provided (changed
        from '' — see domain/schemas.py's comment on these two fields: the previous ''
        default combined with a prompt/schema key mismatch left these two columns
        blank in 100% of rows across both real test projects' CSVs)."""
        paper = PaperData()
        assert paper.how_complicated == "N/A"
        assert paper.can_control == "N/A"


# ---------------------------------------------------------------------------
# End-to-end: agent.run() calls enrich_with_openalex
# ---------------------------------------------------------------------------


class TestRunCallsEnrichWithOpenalex:
    """Verifies that LiteratureResearchAgent.run() passes results through enrich_with_openalex."""

    def test_run_calls_enrich_with_openalex(self, mock_notifier, sample_project_name):
        """enrich_with_openalex is called exactly once per run cycle, after search accumulation."""
        two_papers = [
            {
                "title": "Paper Alpha",
                "link": "https://example.com/alpha",
                "snippet": "Alpha abstract.",
                "year": "2024",
                "citationCount": "3",
                "venue": "Journal A",
            },
            {
                "title": "Paper Beta",
                "link": "https://example.com/beta",
                "snippet": "Beta abstract.",
                "year": "2023",
                "citationCount": "7",
                "venue": "Journal B",
            },
        ]
        enriched_papers = [dict(p, openalex={}) for p in two_papers]

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            agent = LiteratureResearchAgent(
                active_projects=[sample_project_name],
                notifier=mock_notifier,
            )

        with patch.object(agent, "_read_project_text", return_value="Sample research text"):
            with patch.object(agent.fetcher, "search", return_value=two_papers):
                with patch.object(
                    agent.fetcher, "enrich_with_openalex", return_value=enriched_papers
                ) as mock_enrich:
                    with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
                        agent.run()

        mock_enrich.assert_called_once()
        call_args = mock_enrich.call_args[0][0]
        assert isinstance(call_args, list)
        assert len(call_args) > 0


# ---------------------------------------------------------------------------
# OpenAlex year filter and relevance filtering tests
# ---------------------------------------------------------------------------


class TestOpenAlexYearFilterAndRelevance:
    """Tests for the year filter param and keyword overlap relevance filtering."""

    def test_openalex_url_includes_year_filter(self, fetcher):
        """requests.get is called with params dict containing publication_year:>2021 filter."""
        empty_response = MagicMock()
        empty_response.status_code = 200
        empty_response.json.return_value = {"results": []}

        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch(
                "utils.literature_fetcher.requests.get", return_value=empty_response
            ) as mock_get:
                fetcher._fetch_from_openalex("some keywords")

        assert mock_get.call_args.kwargs["params"]["filter"] == "publication_year:>2021"

    def test_keyword_overlap_filters_irrelevant_papers(self, fetcher):
        """Papers with <2 keyword token matches are removed; relevant papers are kept."""
        irrelevant_work = {
            "display_name": "Road to 6G",
            "abstract_inverted_index": {"5G": [0], "network": [1]},
            "cited_by_count": 5,
            "publication_year": 2024,
            "topics": [],
            "keywords": [],
            "open_access": {"is_oa": False, "oa_url": None},
            "primary_location": None,
        }
        relevant_work = {
            "display_name": "Finite blocklength WSN energy",
            "abstract_inverted_index": {
                "wireless": [0], "sensor": [1], "network": [2], "optimization": [3]
            },
            "cited_by_count": 10,
            "publication_year": 2023,
            "topics": [],
            "keywords": [],
            "open_access": {"is_oa": False, "oa_url": None},
            "primary_location": None,
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": [irrelevant_work, relevant_work]}

        with patch.object(Config, "OPENALEX_API_KEY", "test_key"):
            with patch(
                "utils.literature_fetcher.requests.get", return_value=mock_response
            ):
                result = fetcher._fetch_from_openalex("WSN finite blocklength")

        assert len(result) == 1
        assert result[0]["title"] == "Finite blocklength WSN energy"
