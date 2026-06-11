"""Tests for LLM provider failure handling and retry logic."""

from unittest.mock import MagicMock, patch

import pytest


def _make_stub_agent():
    """Return a concrete BaseAgent instance with manually wired mock internals.

    Bypasses __init__ so no real API keys are required.
    """
    from agents.base_agent import BaseAgent

    class _Stub(BaseAgent):
        def run(self):
            pass

    agent = object.__new__(_Stub)
    agent.agent_name = "StubAgent"
    agent.logger = MagicMock()
    agent.groq_client = MagicMock()
    agent.gemini_available = False
    agent.openai_available = False
    agent._providers_waterfall = ["groq"]
    return agent


class TestLLMFailures:
    """Tests for handling LLM provider failures and retry logic."""

    def test_all_providers_exhausted_raises_runtime_error(self):
        """When all LLM providers fail, ask_llm must raise RuntimeError."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")
        agent._providers_waterfall = []
        # gemini and openai both disabled

        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("test prompt")

    def test_groq_fails_switches_to_gemini(self):
        """Gemini provider response is returned when Groq raises an exception."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq failed")

        # Enable Gemini fallback
        agent.gemini_available = True
        agent.gemini_model_name = "gemini-1.5-flash"
        agent._providers_waterfall = ["groq", "gemini"]
        mock_gemini_response = MagicMock()
        mock_gemini_response.text = "Valid response from Gemini"
        agent.gemini_client = MagicMock()
        agent.gemini_client.models.generate_content.return_value = mock_gemini_response

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("test prompt")

        assert result == "Valid response from Gemini"

    def test_groq_empty_response_triggers_retry(self):
        """Empty string response from Groq causes a retry; valid response on retry is returned."""
        agent = _make_stub_agent()

        # First call → empty (raises ValueError inside ask_llm), second call → valid
        mock_response_empty = MagicMock()
        mock_response_empty.choices = [MagicMock()]
        mock_response_empty.choices[0].message.content = ""

        mock_response_valid = MagicMock()
        mock_response_valid.choices = [MagicMock()]
        mock_response_valid.choices[0].message.content = "Valid response"

        agent.groq_client.chat.completions.create.side_effect = [
            mock_response_empty,
            mock_response_valid,
        ]

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("test prompt")

        assert result == "Valid response"
        assert agent.groq_client.chat.completions.create.call_count == 2

    def test_groq_error_string_response_triggers_retry(self):
        """Response starting with 'Error:' from Groq triggers a retry."""
        agent = _make_stub_agent()

        mock_response_error = MagicMock()
        mock_response_error.choices = [MagicMock()]
        mock_response_error.choices[0].message.content = "Error: something went wrong"

        mock_response_valid = MagicMock()
        mock_response_valid.choices = [MagicMock()]
        mock_response_valid.choices[0].message.content = "Good answer"

        agent.groq_client.chat.completions.create.side_effect = [
            mock_response_error,
            mock_response_valid,
        ]

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("test prompt")

        assert result == "Good answer"
        assert agent.groq_client.chat.completions.create.call_count == 2

    def test_exponential_backoff_called_between_retries(self):
        """time.sleep is called between retries and sleep value increases (backoff)."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception("Always fails")

        sleep_calls = []

        with patch("agents.base_agent.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            with patch("agents.base_agent.random.uniform", return_value=0.0):
                with pytest.raises(RuntimeError):
                    agent.ask_llm("test prompt")

        # At least 2 sleep calls (between retries), and values should be increasing
        assert len(sleep_calls) >= 2
        # First sleep ≤ second sleep (exponential backoff: 2^1, 2^2, …)
        assert sleep_calls[0] <= sleep_calls[1]

    def test_pydantic_failure_returns_fallback_not_crash(self):
        from agents.literature_research_agent import LiteratureResearchAgent
        mock_notifier = MagicMock()
        agent = LiteratureResearchAgent(active_projects=["Test"], notifier=mock_notifier)

        with patch.object(agent, "ask_llm", return_value="not valid json {{{{"):
            result = agent.process_results_with_llm("Test", "keywords", [{"title": "Paper"}])

        assert "summary" in result
        assert result["papers"] == []

    def test_none_response_from_provider_triggers_retry(self):
        """None content from Groq triggers a retry; valid response is returned."""
        agent = _make_stub_agent()

        mock_response_none = MagicMock()
        mock_response_none.choices = [MagicMock()]
        mock_response_none.choices[0].message.content = None

        mock_response_valid = MagicMock()
        mock_response_valid.choices = [MagicMock()]
        mock_response_valid.choices[0].message.content = "Valid answer"

        agent.groq_client.chat.completions.create.side_effect = [
            mock_response_none,
            mock_response_valid,
        ]

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("test prompt")

        assert result == "Valid answer"
        assert agent.groq_client.chat.completions.create.call_count == 2
