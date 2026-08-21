"""TDD tests for Task 1 (stability hardening): 413 reclassification, waterfall
exhaustion alert dedup, cross-run cooldown persistence, and the new Cerebras /
NVIDIA NIM provider paths (all mocked — no real network calls, no real keys)."""

from unittest.mock import MagicMock, patch

import pytest


def _make_stub_agent():
    """Concrete BaseAgent instance with manually wired mock internals — bypasses
    __init__ so no real API keys / network / DB are required."""
    from agents.base_agent import BaseAgent

    class _Stub(BaseAgent):
        def run(self):
            pass

    agent = object.__new__(_Stub)
    agent.agent_name = "StubAgent"
    agent.logger = MagicMock()
    agent.groq_client = MagicMock()
    agent.gemini_available = False
    agent.cerebras_available = False
    agent.nvidia_nim_available = False
    agent.openai_available = False
    agent._providers_waterfall = ["groq"]
    return agent


# ---------------------------------------------------------------------------
# 1. 413 reclassification
# ---------------------------------------------------------------------------

class TestSizeErrorReclassification:
    def test_413_with_tpm_message_starts_cooldown(self):
        """A 413 whose message names a tokens-per-minute ceiling is rate-limit-shaped,
        not permanent — it must route through _start_provider_cooldown, not a bare
        break-and-skip with no cooldown recorded."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "Error code: 413 - Request too large for model, tokens per minute (TPM): Limit 8000"
        )
        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is not None

    def test_413_with_rate_word_starts_cooldown(self):
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "413 Payload Too Large: rate ceiling exceeded for this model"
        )
        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is not None

    def test_413_with_context_length_exceeded_stays_permanent(self):
        """A genuinely oversized single request must NOT start a cooldown — chunking
        is the real fix, not retry/backoff, and there is nothing to wait out."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "Error code: 413 - context_length_exceeded: reduce your prompt"
        )
        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is None

    def test_413_with_maximum_context_length_stays_permanent(self):
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "413 - This model's maximum context length is 8192 tokens"
        )
        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is None

    def test_generic_413_with_no_recognizable_signal_stays_permanent(self):
        """An ambiguous 413 with no TPM/rate wording keeps today's conservative
        (permanent) behavior — we only reclassify when the message actually says so."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "413 Request Entity Too Large"
        )
        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is None

    def test_rate_shaped_413_fails_over_to_next_provider(self):
        agent = _make_stub_agent()
        agent.gemini_available = True
        agent.gemini_model_name = "gemini-2.5-flash"
        agent._providers_waterfall = ["groq", "gemini"]
        agent.gemini_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = "from gemini"
        agent.gemini_client.models.generate_content.return_value = mock_resp

        agent.groq_client.chat.completions.create.side_effect = Exception(
            "413 - tokens per minute (TPM) exceeded"
        )
        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("prompt")

        assert result == "from gemini"
        assert agent._provider_cooldowns.get("groq") is not None


# ---------------------------------------------------------------------------
# 2. Waterfall exhaustion alert — single deduplicated alert per run per project
# ---------------------------------------------------------------------------

class TestWaterfallExhaustionAlert:
    def _make_agent_all_llm_down(self, mock_notifier):
        from agents.literature_research_agent import LiteratureResearchAgent
        agent = LiteratureResearchAgent(active_projects=["ProjectA"], notifier=mock_notifier)
        return agent

    def test_keyword_extraction_exhaustion_sends_one_alert(self, mock_notifier):
        agent = self._make_agent_all_llm_down(mock_notifier)
        with patch.object(agent, "ask_llm", side_effect=RuntimeError("CRITICAL: All LLM providers exhausted.")):
            agent.extract_keywords_from_text("ProjectA", "some manuscript text")

        assert mock_notifier.send_admin_alert.call_count == 1

    def test_summarization_exhaustion_sends_one_alert(self, mock_notifier):
        agent = self._make_agent_all_llm_down(mock_notifier)
        with patch.object(agent, "ask_llm", side_effect=RuntimeError("CRITICAL: All LLM providers exhausted.")):
            agent.process_results_with_llm("ProjectA", "kw", [{"title": "Paper"}])

        assert mock_notifier.send_admin_alert.call_count == 1

    def test_exhaustion_across_both_call_sites_same_project_sends_only_one_alert(self, mock_notifier):
        """The dedup guard is per-run/per-project, not per call-site or per paper — if
        both keyword extraction AND summarization exhaust the waterfall for the same
        project in the same run, that's still exactly one alert."""
        agent = self._make_agent_all_llm_down(mock_notifier)
        with patch.object(agent, "ask_llm", side_effect=RuntimeError("CRITICAL: All LLM providers exhausted.")):
            agent.extract_keywords_from_text("ProjectA", "some manuscript text")
            agent.process_results_with_llm("ProjectA", "kw", [{"title": "Paper"}])

        assert mock_notifier.send_admin_alert.call_count == 1

    def test_exhaustion_for_different_projects_sends_separate_alerts(self, mock_notifier):
        agent = self._make_agent_all_llm_down(mock_notifier)
        with patch.object(agent, "ask_llm", side_effect=RuntimeError("CRITICAL: All LLM providers exhausted.")):
            agent.extract_keywords_from_text("ProjectA", "text")
            agent.extract_keywords_from_text("ProjectB", "text")

        assert mock_notifier.send_admin_alert.call_count == 2

    def test_non_runtime_error_does_not_trigger_waterfall_alert(self, mock_notifier):
        """A Pydantic validation failure or bad JSON is a different failure mode (the
        LLM answered, just badly) — it must not be conflated with waterfall exhaustion."""
        agent = self._make_agent_all_llm_down(mock_notifier)
        with patch.object(agent, "ask_llm", return_value="not valid json {{{{"):
            agent.process_results_with_llm("ProjectA", "kw", [{"title": "Paper"}])

        assert mock_notifier.send_admin_alert.call_count == 0

    def test_alert_failure_does_not_crash_or_mask_degraded_output(self, mock_notifier):
        agent = self._make_agent_all_llm_down(mock_notifier)
        mock_notifier.send_admin_alert.side_effect = Exception("SMTP down")
        with patch.object(agent, "ask_llm", side_effect=RuntimeError("CRITICAL: All LLM providers exhausted.")):
            topic, method = agent.extract_keywords_from_text("ProjectA", "text")

        assert topic == "ProjectA"


# ---------------------------------------------------------------------------
# 3. Cross-run cooldown persistence
# ---------------------------------------------------------------------------

class TestCooldownPersistence:
    def test_start_cooldown_persists_to_db(self, db_in_memory):
        from agents.base_agent import BaseAgent
        BaseAgent._shared_db_manager = db_in_memory

        BaseAgent._start_provider_cooldown("groq", seconds=120)

        persisted = db_in_memory.get_all_cooldowns()
        assert "groq" in persisted
        assert persisted["groq"] > 0

    def test_fresh_agent_cold_start_skips_provider_still_cooling_down(self, db_in_memory, mock_notifier):
        """Simulates a cold restart: an earlier process persisted a cooldown, this
        process's in-memory dict is empty, and a brand-new BaseAgent instance must
        still see and honor the persisted cooldown at __init__ time."""
        import time
        from agents.base_agent import BaseAgent
        from agents.literature_research_agent import LiteratureResearchAgent

        BaseAgent._shared_db_manager = db_in_memory
        db_in_memory.set_cooldown("groq", time.time() + 120)

        # Simulate the "cold start": in-process dict is empty, as it would be in a
        # brand-new process/container.
        BaseAgent._provider_cooldowns.clear()

        agent = LiteratureResearchAgent(active_projects=["ProjectA"], notifier=mock_notifier)

        assert agent._provider_cooldown_remaining("groq") > 0

    def test_expired_persisted_cooldown_does_not_block_provider(self, db_in_memory, mock_notifier):
        from agents.base_agent import BaseAgent
        from agents.literature_research_agent import LiteratureResearchAgent
        import time

        BaseAgent._shared_db_manager = db_in_memory
        db_in_memory.set_cooldown("groq", time.time() - 10)  # already expired
        BaseAgent._provider_cooldowns.clear()

        agent = LiteratureResearchAgent(active_projects=["ProjectA"], notifier=mock_notifier)

        assert agent._provider_cooldown_remaining("groq") == 0

    def test_db_read_failure_during_load_does_not_crash_agent_init(self, mock_notifier):
        """Persistence is best-effort — a DB error at __init__ must not prevent the
        agent from starting."""
        from agents.base_agent import BaseAgent
        from agents.literature_research_agent import LiteratureResearchAgent

        broken_db = MagicMock()
        broken_db.get_all_cooldowns.side_effect = Exception("disk full")
        BaseAgent._shared_db_manager = broken_db

        # Should not raise.
        agent = LiteratureResearchAgent(active_projects=["ProjectA"], notifier=mock_notifier)
        assert agent is not None

    def test_db_write_failure_during_cooldown_start_does_not_break_in_memory_cooldown(self):
        from agents.base_agent import BaseAgent

        broken_db = MagicMock()
        broken_db.set_cooldown.side_effect = Exception("disk full")
        BaseAgent._shared_db_manager = broken_db

        # Should not raise, and the in-process cooldown must still be recorded.
        BaseAgent._start_provider_cooldown("groq", seconds=60)
        assert BaseAgent._provider_cooldown_remaining("groq") > 0


# ---------------------------------------------------------------------------
# 4. Cerebras / NVIDIA NIM provider paths (mocked clients only)
# ---------------------------------------------------------------------------

class TestNewProviders:
    def _agent_with_cerebras(self):
        agent = _make_stub_agent()
        agent.cerebras_available = True
        agent.cerebras_client = MagicMock()
        agent._providers_waterfall = ["groq", "cerebras"]
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "cerebras response"
        agent.cerebras_client.chat.completions.create.return_value = mock_resp
        return agent

    def _agent_with_nvidia_nim(self):
        agent = _make_stub_agent()
        agent.nvidia_nim_available = True
        agent.nvidia_nim_client = MagicMock()
        agent._providers_waterfall = ["groq", "nvidia_nim"]
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "nim response"
        agent.nvidia_nim_client.chat.completions.create.return_value = mock_resp
        return agent

    def test_groq_fails_falls_over_to_cerebras(self):
        agent = self._agent_with_cerebras()
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("prompt")

        assert result == "cerebras response"

    def test_cerebras_uses_configured_model_and_base_url_client(self):
        from config import Config
        agent = self._agent_with_cerebras()
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")

        with patch("agents.base_agent.time.sleep"):
            agent.ask_llm("prompt")

        _, kwargs = agent.cerebras_client.chat.completions.create.call_args
        assert kwargs["model"] == Config.CEREBRAS_MODEL_NAME
        assert Config.CEREBRAS_MODEL_NAME == "gpt-oss-120b"

    def test_groq_fails_falls_over_to_nvidia_nim(self):
        agent = self._agent_with_nvidia_nim()
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("prompt")

        assert result == "nim response"

    def test_nvidia_nim_uses_configured_model(self):
        from config import Config
        agent = self._agent_with_nvidia_nim()
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")

        with patch("agents.base_agent.time.sleep"):
            agent.ask_llm("prompt")

        _, kwargs = agent.nvidia_nim_client.chat.completions.create.call_args
        assert kwargs["model"] == Config.NVIDIA_NIM_MODEL_NAME

    def test_full_waterfall_order_groq_gemini_cerebras_nvidia_openai(self):
        """Exercises the full 5-tier waterfall in order when every provider is
        configured and every provider but the last fails."""
        agent = _make_stub_agent()
        agent.gemini_available = True
        agent.gemini_model_name = "gemini-2.5-flash"
        agent.gemini_client = MagicMock()
        agent.gemini_client.models.generate_content.side_effect = Exception("Gemini down")

        agent.cerebras_available = True
        agent.cerebras_client = MagicMock()
        agent.cerebras_client.chat.completions.create.side_effect = Exception("Cerebras down")

        agent.nvidia_nim_available = True
        agent.nvidia_nim_client = MagicMock()
        agent.nvidia_nim_client.chat.completions.create.side_effect = Exception("NIM down")

        agent.openai_available = True
        agent.openai_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "openai last resort"
        agent.openai_client.chat.completions.create.return_value = mock_resp

        agent._providers_waterfall = ["groq", "gemini", "cerebras", "nvidia_nim", "openai"]
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("prompt")

        assert result == "openai last resort"


# ---------------------------------------------------------------------------
# 5. Cerebras-specific proactive rate limiter
# ---------------------------------------------------------------------------

class TestCerebrasRateLimiter:
    def test_sliding_window_limiter_rejects_beyond_capacity(self):
        from agents.base_agent import _SlidingWindowLimiter
        limiter = _SlidingWindowLimiter(max_requests=4, window_seconds=60.0)
        results = [limiter.try_acquire() for _ in range(5)]
        assert results == [True, True, True, True, False]

    def test_sliding_window_limiter_allows_again_after_window_elapses(self):
        from agents.base_agent import _SlidingWindowLimiter
        limiter = _SlidingWindowLimiter(max_requests=1, window_seconds=60.0)
        with patch("agents.base_agent.time.time", return_value=1000.0):
            assert limiter.try_acquire() is True
            assert limiter.try_acquire() is False
        with patch("agents.base_agent.time.time", return_value=1061.0):
            assert limiter.try_acquire() is True

    def test_burst_beyond_safe_rpm_raises_rate_limit_shaped_error_not_real_call(self):
        """Simulates the concrete failure mode from the extension brief: several
        concurrent calls fall back to Cerebras in a burst. Once the proactive guard's
        capacity is exhausted, further calls must be rejected as rate-limit-shaped
        (handled by the existing cooldown/failover machinery) WITHOUT ever reaching
        the real Cerebras client — that's the whole point of the guard."""
        from config import Config

        agent = _make_stub_agent()
        agent.cerebras_available = True
        agent.cerebras_client = MagicMock()
        agent._providers_waterfall = ["cerebras"]
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "ok"
        agent.cerebras_client.chat.completions.create.return_value = mock_resp

        # Exhaust the shared limiter's capacity directly (it's class-level/shared,
        # matching production: all agent instances in the process share one guard).
        from agents.base_agent import BaseAgent
        for _ in range(Config.CEREBRAS_SAFE_RPM):
            assert BaseAgent._cerebras_rate_limiter.try_acquire() is True

        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        agent.cerebras_client.chat.completions.create.assert_not_called()
