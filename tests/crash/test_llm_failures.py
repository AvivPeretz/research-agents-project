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


class TestRateLimitCooldown:
    """Tests for the shared rate-limit cooldown (ground-truth failure: the LLM
    waterfall itself, the system's own safety net, failed under rate limits alongside
    Stanford and the literature search)."""

    def _make_two_provider_agent(self):
        agent = _make_stub_agent()
        agent.gemini_available = True
        agent.gemini_model_name = "gemini-1.5-flash"
        agent._providers_waterfall = ["groq", "gemini"]
        agent.gemini_client = MagicMock()
        mock_gemini_response = MagicMock()
        mock_gemini_response.text = "Valid response from Gemini"
        agent.gemini_client.models.generate_content.return_value = mock_gemini_response
        return agent

    def test_429_fails_over_immediately_without_exhausting_retries(self):
        """A rate-limit error must skip the remaining same-provider retries (no point
        retrying a 429 with a 2-second backoff) and fail over to the next provider."""
        agent = self._make_two_provider_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "Error code: 429 - Rate limit reached for requests"
        )

        with patch("agents.base_agent.time.sleep") as mock_sleep:
            result = agent.ask_llm("test prompt")

        assert result == "Valid response from Gemini"
        # Only one attempt against Groq — the 429 short-circuits the retry loop.
        assert agent.groq_client.chat.completions.create.call_count == 1
        mock_sleep.assert_not_called()

    def test_rate_limited_provider_is_skipped_on_subsequent_call(self):
        """After a 429, a second ask_llm() call (simulating the next agent step in the
        same run) must skip straight past the rate-limited provider instead of
        re-discovering the same 429 and burning another retry cycle against it."""
        agent = self._make_two_provider_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception("429 quota exceeded")

        with patch("agents.base_agent.time.sleep"):
            agent.ask_llm("first call")  # triggers cooldown on groq

        agent.groq_client.chat.completions.create.reset_mock()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "should never be called while cooling down"
        )

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("second call")

        assert result == "Valid response from Gemini"
        agent.groq_client.chat.completions.create.assert_not_called()

    def test_cooldown_shared_across_different_agent_instances(self):
        """The mission's exact scenario: multiple agents run in the same pipeline
        cycle. If one agent's call to Groq gets rate-limited, a second, independent
        agent instance must not waste a call rediscovering it."""
        agent_a = self._make_two_provider_agent()
        agent_a.groq_client.chat.completions.create.side_effect = Exception("429 too many requests")
        with patch("agents.base_agent.time.sleep"):
            agent_a.ask_llm("prompt from agent A")

        agent_b = self._make_two_provider_agent()
        agent_b.groq_client.chat.completions.create.side_effect = Exception("should not be called")

        with patch("agents.base_agent.time.sleep"):
            result = agent_b.ask_llm("prompt from agent B")

        assert result == "Valid response from Gemini"
        agent_b.groq_client.chat.completions.create.assert_not_called()

    def test_all_providers_in_cooldown_raises_clear_error_with_no_wasted_calls(self):
        """When every provider in the waterfall is already cooling down, ask_llm must
        fail fast with a message that says so — not silently hang or make wasted
        calls against providers already known to be rate-limited."""
        from agents.base_agent import BaseAgent

        agent = self._make_two_provider_agent()
        BaseAgent._start_provider_cooldown("groq", seconds=120)
        BaseAgent._start_provider_cooldown("gemini", seconds=120)

        with pytest.raises(RuntimeError, match="cooldown"):
            agent.ask_llm("test prompt")

        agent.groq_client.chat.completions.create.assert_not_called()
        agent.gemini_client.models.generate_content.assert_not_called()

    def test_model_override_reaches_groq_client_call(self):
        """A model_override passed to ask_llm() must reach the actual Groq API call —
        this is how extraction calls (keyword generation, relevance filtering) use a
        cheaper model while synthesis calls keep the default."""
        from config import Config

        agent = _make_stub_agent()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "fast response"
        agent.groq_client.chat.completions.create.return_value = mock_response

        result = agent.ask_llm("test prompt", model_override="llama-3.1-8b-instant")

        assert result == "fast response"
        _, kwargs = agent.groq_client.chat.completions.create.call_args
        assert kwargs["model"] == "llama-3.1-8b-instant"
        assert kwargs["model"] != Config.LLM_MODEL_NAME

    def test_no_model_override_uses_default_synthesis_model(self):
        """Without an override, ask_llm() must use the configured default model —
        synthesis calls (reviews, feedback, reports) must not accidentally downgrade."""
        from config import Config

        agent = _make_stub_agent()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "synthesis response"
        agent.groq_client.chat.completions.create.return_value = mock_response

        agent.ask_llm("test prompt")

        _, kwargs = agent.groq_client.chat.completions.create.call_args
        assert kwargs["model"] == Config.LLM_MODEL_NAME

    def test_cooldown_expires_and_provider_is_retried(self):
        """Once the cooldown window has passed, the provider must be tried again."""
        from agents.base_agent import BaseAgent

        agent = _make_stub_agent()
        BaseAgent._start_provider_cooldown("groq", seconds=-1)  # already expired

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Groq is back"
        agent.groq_client.chat.completions.create.return_value = mock_response

        result = agent.ask_llm("test prompt")

        assert result == "Groq is back"
