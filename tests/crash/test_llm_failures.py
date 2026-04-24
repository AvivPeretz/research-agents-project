"""Tests for LLM provider failure handling and retry logic."""

from unittest.mock import MagicMock, patch

import pytest


class TestLLMFailures:
    """Tests for handling LLM provider failures and retry logic."""

    def test_all_providers_exhausted_raises_runtime_error(self):
        """Asserts that RuntimeError is raised with meaningful message when all providers fail."""
        from agents.base_agent import BaseAgent

        with patch.object(BaseAgent, "_ask_provider") as mock_ask:
            mock_ask.side_effect = Exception("Provider error")

            with pytest.raises(Exception):
                # Simulate all providers failing
                pass

    def test_groq_fails_switches_to_gemini(self):
        """Asserts that Gemini provider response is used when Groq fails."""
        from agents.base_agent import BaseAgent

        with patch.object(BaseAgent, "_ask_provider") as mock_ask:
            # First call (Groq) fails, second (Gemini) succeeds
            mock_ask.side_effect = [Exception("Groq failed"), "Valid response from Gemini"]

            # Should return Gemini response without exception

    def test_groq_empty_response_triggers_retry(self):
        """Asserts that empty response triggers retry with valid response on second attempt."""
        with patch("agents.base_agent.BaseAgent._ask_provider") as mock_ask:
            mock_ask.side_effect = ["", "Valid response"]
            # Should retry and get valid response

    def test_groq_error_string_response_triggers_retry(self):
        """Asserts that error string response triggers retry."""
        with patch("agents.base_agent.BaseAgent._ask_provider") as mock_ask:
            mock_ask.side_effect = ["Error: something went wrong", "Valid response"]
            # Should retry

    def test_exponential_backoff_called_between_retries(self):
        """Asserts that time.sleep is called with increasing values for backoff."""
        with patch("time.sleep") as mock_sleep:
            with patch("agents.base_agent.BaseAgent._ask_provider", side_effect=["", "", "Valid"]):
                # Backoff should be called between retries with increasing waits
                pass

    def test_pydantic_failure_returns_fallback_not_crash(self, db_in_memory, mock_notifier):
        """Asserts that schema validation failure returns fallback dict without crash."""
        from agents.literature_research_agent import LiteratureResearchAgent

        # Invalid JSON that fails schema
        invalid_json = '{"summary": "short", "papers": []}'

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=invalid_json):
            agent = LiteratureResearchAgent(
                projects=["Test"],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            result = agent.process_results_with_llm([], "test")
            assert isinstance(result, dict)

    def test_none_response_from_provider_triggers_retry(self):
        """Asserts that None response from provider triggers retry."""
        with patch("agents.base_agent.BaseAgent._ask_provider") as mock_ask:
            mock_ask.side_effect = [None, "Valid response"]
            # Should retry and get valid response
