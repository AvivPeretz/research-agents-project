"""Tests for rate-limit handling across the literature search fallback chain
(Semantic Scholar -> SerpAPI -> scholarly). Ground-truth incident: the literature
search step hit rate limits, and there was no coordination -- each project's queries
independently re-discovered and re-retried the same 429 with no shared memory.
"""

from unittest.mock import MagicMock, patch

import pytest

from utils.literature_fetcher import LiteratureFetcher


@pytest.fixture
def fetcher():
    return LiteratureFetcher()


def _make_response(status_code, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if status_code == 200:
        resp.raise_for_status.return_value = None
    else:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestSemanticScholarRateLimit:

    def test_429_enters_cooldown_and_does_not_raise(self, fetcher):
        """A 429 from Semantic Scholar must be treated as 'try later', not retried
        immediately or allowed to raise up to the caller."""
        with patch("utils.literature_fetcher.requests.get", return_value=_make_response(429)), \
             patch("utils.literature_fetcher.time.sleep"):
            result = fetcher._fetch_from_semantic_scholar("test query")

        assert result == []
        assert fetcher._cooldown_remaining("semantic_scholar") > 0

    def test_second_call_during_cooldown_skips_http_request_entirely(self, fetcher):
        """Once cooling down, a second query (simulating the next project in the same
        run) must not make another HTTP request at all."""
        with patch("utils.literature_fetcher.requests.get", return_value=_make_response(429)), \
             patch("utils.literature_fetcher.time.sleep"):
            fetcher._fetch_from_semantic_scholar("first query")

        with patch("utils.literature_fetcher.requests.get") as mock_get, \
             patch("utils.literature_fetcher.time.sleep"):
            result = fetcher._fetch_from_semantic_scholar("second query")

        assert result == []
        mock_get.assert_not_called()

    def test_no_cooldown_on_normal_success(self, fetcher):
        """A normal 200 response must not trigger a cooldown."""
        with patch(
            "utils.literature_fetcher.requests.get",
            return_value=_make_response(200, {"data": []}),
        ), patch("utils.literature_fetcher.time.sleep"):
            fetcher._fetch_from_semantic_scholar("test query")

        assert fetcher._cooldown_remaining("semantic_scholar") == 0


class TestSerpApiRateLimit:

    def test_429_enters_cooldown_and_returns_empty(self, fetcher):
        from config import Config
        with patch.object(Config, "SERPAPI_API_KEY", "test_key"), \
             patch("utils.literature_fetcher.requests.get", return_value=_make_response(429)):
            result = fetcher._fetch_from_serpapi("test query")

        assert result == []
        assert fetcher._cooldown_remaining("serpapi") > 0

    def test_second_call_during_cooldown_skips_http_request(self, fetcher):
        from config import Config
        with patch.object(Config, "SERPAPI_API_KEY", "test_key"):
            with patch("utils.literature_fetcher.requests.get", return_value=_make_response(429)):
                fetcher._fetch_from_serpapi("first query")

            with patch("utils.literature_fetcher.requests.get") as mock_get:
                result = fetcher._fetch_from_serpapi("second query")

        assert result == []
        mock_get.assert_not_called()


class TestScholarlyRateLimit:

    def test_rate_limit_like_exception_enters_cooldown(self, fetcher):
        """scholarly has no clean HTTP status -- a 429/blocked-style exception message
        must still be recognized and trigger a cooldown."""
        with patch("utils.literature_fetcher.SCHOLARLY_AVAILABLE", True), \
             patch("utils.literature_fetcher._scholarly_lib") as mock_lib:
            mock_lib.search_pubs.side_effect = Exception("Cannot Fetch from Google Scholar (429 Too Many Requests)")
            result = fetcher._fetch_from_scholarly("test query")

        assert result == []
        assert fetcher._cooldown_remaining("scholarly") > 0

    def test_unrelated_exception_does_not_enter_cooldown(self, fetcher):
        """A normal, non-rate-limit failure must not falsely trigger a cooldown --
        that would needlessly disable the last-resort fallback."""
        with patch("utils.literature_fetcher.SCHOLARLY_AVAILABLE", True), \
             patch("utils.literature_fetcher._scholarly_lib") as mock_lib:
            mock_lib.search_pubs.side_effect = Exception("connection reset by peer")
            result = fetcher._fetch_from_scholarly("test query")

        assert result == []
        assert fetcher._cooldown_remaining("scholarly") == 0

    def test_second_call_during_cooldown_never_calls_scholarly(self, fetcher):
        with patch("utils.literature_fetcher.SCHOLARLY_AVAILABLE", True), \
             patch("utils.literature_fetcher._scholarly_lib") as mock_lib:
            mock_lib.search_pubs.side_effect = Exception("429 too many requests")
            fetcher._fetch_from_scholarly("first query")

            mock_lib.search_pubs.reset_mock(side_effect=True)
            result = fetcher._fetch_from_scholarly("second query")

        assert result == []
        mock_lib.search_pubs.assert_not_called()


class TestCooldownsAreIndependentPerProvider:

    def test_semantic_scholar_cooldown_does_not_block_serpapi(self, fetcher):
        """A rate limit on one provider must not affect the others in the chain --
        each is an independent external service with its own limits."""
        with patch("utils.literature_fetcher.requests.get", return_value=_make_response(429)), \
             patch("utils.literature_fetcher.time.sleep"):
            fetcher._fetch_from_semantic_scholar("test query")

        assert fetcher._cooldown_remaining("semantic_scholar") > 0
        assert fetcher._cooldown_remaining("serpapi") == 0
        assert fetcher._cooldown_remaining("scholarly") == 0
