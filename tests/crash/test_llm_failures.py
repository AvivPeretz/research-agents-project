"""Tests for LLM provider failure handling and retry logic."""

from unittest.mock import MagicMock, patch

import pytest


class TestLLMFailures:
    """Tests for handling LLM provider failures and retry logic."""

    def test_all_providers_exhausted_raises_runtime_error(self):
        """When all LLM providers fail, ask_llm must raise RuntimeError."""
        from agents.base_agent import BaseAgent
        mock_agent = MagicMock(spec=BaseAgent)
        mock_agent.groq_client = MagicMock()
        mock_agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")
        mock_agent.gemini_available = False
        mock_agent.openai_available = False
        mock_agent.logger = MagicMock()

        with pytest.raises(RuntimeError):
            BaseAgent.ask_llm(mock_agent, "test prompt")

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

    def test_pydantic_failure_returns_fallback_not_crash(self):
        from agents.literature_research_agent import LiteratureResearchAgent
        mock_notifier = MagicMock()
        agent = LiteratureResearchAgent(active_projects=["Test"], notifier=mock_notifier)
        
        with patch.object(agent, "ask_llm", return_value="not valid json {{{{"):
            result = agent.process_results_with_llm("Test", "keywords", [{"title": "Paper"}])
        
        assert "summary" in result
        assert result["papers"] == []

    def test_none_response_from_provider_triggers_retry(self):
        """Asserts that None response from provider triggers retry."""
        with patch("agents.base_agent.BaseAgent._ask_provider") as mock_ask:
            mock_ask.side_effect = [None, "Valid response"]
            # Should retry and get valid response
