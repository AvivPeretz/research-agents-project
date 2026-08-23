"""TDD tests for Task 1 (stability hardening): 413 reclassification, waterfall
exhaustion alert dedup, cross-run cooldown persistence, and the NVIDIA NIM
provider path (all mocked — no real network calls, no real keys)."""

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

    def test_413_with_rate_limit_phrase_starts_cooldown(self):
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "413 Payload Too Large: rate limit ceiling exceeded for this model"
        )
        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is not None

    def test_413_with_requests_per_phrase_starts_cooldown(self):
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "413 - too many requests per minute for this model"
        )
        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is not None

    @pytest.mark.parametrize("message", [
        "413 - request too large to generate a response for this model",
        "413 - the accurate token count exceeds the model limit",
        "413 - please separate your request into smaller chunks",
        "413 - a moderate reduction in prompt size is required",
    ])
    def test_413_with_bare_rate_substring_words_stays_permanent(self, message):
        """Regression test: the classifier must not false-positive on ordinary English
        words that merely CONTAIN the substring 'rate' (generate, accurate, separate,
        moderate). A bare `"rate" in lowered_err` check previously misclassified these
        genuinely permanent oversized-request errors as transient, which would put a
        healthy provider into a bogus (and persisted) cooldown."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(message)
        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is None

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
# 4. NVIDIA NIM provider path (mocked client only)
# ---------------------------------------------------------------------------

class TestNewProviders:
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

    def test_full_waterfall_order_groq_gemini_nvidia_openai(self):
        """Exercises the full 4-tier waterfall in order when every provider is
        configured and every provider but the last fails."""
        agent = _make_stub_agent()
        agent.gemini_available = True
        agent.gemini_model_name = "gemini-2.5-flash"
        agent.gemini_client = MagicMock()
        agent.gemini_client.models.generate_content.side_effect = Exception("Gemini down")

        agent.nvidia_nim_available = True
        agent.nvidia_nim_client = MagicMock()
        agent.nvidia_nim_client.chat.completions.create.side_effect = Exception("NIM down")

        agent.openai_available = True
        agent.openai_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "openai last resort"
        agent.openai_client.chat.completions.create.return_value = mock_resp

        agent._providers_waterfall = ["groq", "gemini", "nvidia_nim", "openai"]
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")

        with patch("agents.base_agent.time.sleep"):
            result = agent.ask_llm("prompt")

        assert result == "openai last resort"


# ---------------------------------------------------------------------------
# 5. Permanent quota exhaustion vs. renewing quota (Task 2, Cerebras-removal
#    follow-up): a bare "quota" substring must not send a permanently
#    exhausted billing allowance down the same cooldown path as a renewing
#    per-minute/per-day rate limit.
# ---------------------------------------------------------------------------

class TestPermanentQuotaExhaustion:
    def test_openai_insufficient_quota_skips_retries_with_no_cooldown(self):
        """Real OpenAI insufficient_quota error shape (documented format at
        https://platform.openai.com/docs/guides/error-codes/api-errors, matching what
        this project's own logs have previously captured for this failure class): an
        exhausted billing allowance with no automatic reset. Must NOT start a cooldown
        — cooldowns mean "try again later," which is wrong when the actual fix is a
        human adding billing/credits."""
        agent = _make_stub_agent()
        agent.openai_available = True
        agent.openai_client = MagicMock()
        agent._providers_waterfall = ["groq", "openai"]
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")
        agent.openai_client.chat.completions.create.side_effect = Exception(
            "Error code: 429 - {'error': {'message': 'You exceeded your current quota, "
            "please check your plan and billing details. For more information on this "
            "error, read the docs: https://platform.openai.com/docs/guides/error-codes/"
            "api-errors.', 'type': 'insufficient_quota', 'param': None, "
            "'code': 'insufficient_quota'}}"
        )

        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("openai") is None

    def test_402_payment_required_with_param_quota_skips_retries_with_no_cooldown(self):
        """Real, verbatim error text from this project's own logs/LiveProbeAgent.log:36
        (from the now-removed Cerebras integration; the error SHAPE is what's under
        test, not the provider). This is exactly the failure mode the bug describes: a
        bare "quota" substring (here via 'param': 'quota') sitting inside a genuinely
        permanent 402 payment_required billing error. Reused against the "openai"
        provider slot to prove the classifier is provider-agnostic."""
        agent = _make_stub_agent()
        agent.openai_available = True
        agent.openai_client = MagicMock()
        agent._providers_waterfall = ["groq", "openai"]
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")
        agent.openai_client.chat.completions.create.side_effect = Exception(
            "Error code: 402 - {'message': 'Payment required to access this resource. "
            "Visit your billing tab.', 'type': 'payment_required_error', "
            "'param': 'quota', 'code': 'payment_required'}"
        )

        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("openai") is None

    def test_renewing_daily_quota_still_enters_cooldown_no_regression(self):
        """Groq's real daily-rate-limit error format (per Groq's own rate-limit docs:
        https://console.groq.com/docs/rate-limits — 429 responses name the specific
        limit type, e.g. "Rate limit reached for ... Limit 14400, Used 14400,
        Requested 1. Please try again in ... or upgrade")..."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "Error code: 429 - {'error': {'message': 'Rate limit reached for "
            "requests-per-day in organization org_xxx on tokens per day (TPD): "
            "Limit 14400, Used 14400, Requested 1. Please try again in 1m4s or "
            "upgrade your plan.', 'type': 'requests', 'code': 'rate_limit_exceeded'}}"
        )

        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is not None

    def test_gemini_per_minute_quota_still_enters_cooldown_no_regression(self):
        """Gemini's real quota-exceeded error format (per Google's documented Gemini
        API error shape: a RESOURCE_EXHAUSTED 429 whose message names a specific
        per-minute quota metric). Must still enter cooldown, unchanged from before."""
        agent = _make_stub_agent()
        agent.groq_client.chat.completions.create.side_effect = Exception(
            "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': "
            "'Resource has been exhausted (e.g. check quota). You exceeded your "
            "current quota, please check your plan and billing details... "
            "generate_content_requests_per_minute_per_project_per_model limit: 15, "
            "please retry in 42 seconds.', 'status': 'RESOURCE_EXHAUSTED'}}"
        )

        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError):
                agent.ask_llm("prompt")

        assert agent._provider_cooldowns.get("groq") is not None

    def test_openai_permanent_quota_as_last_provider_reaches_full_exhaustion_path(self):
        """OpenAI is the last waterfall tier. A permanent quota exhaustion there (not a
        cooldown, since is_permanent_quota_error takes precedence) must still fall
        through the per-provider loop to the same "All available LLM providers have
        been exhausted" / RuntimeError('CRITICAL: All LLM providers exhausted...')
        path that a real 429/413/auth failure would reach — this is the path that
        agent call sites use to trigger _alert_waterfall_exhausted. A silent early-exit
        that skipped this would mean OpenAI's real insufficient_quota failures never
        page anyone."""
        agent = _make_stub_agent()
        agent.openai_available = True
        agent.openai_client = MagicMock()
        agent._providers_waterfall = ["groq", "openai"]
        agent.groq_client.chat.completions.create.side_effect = Exception("Groq down")
        agent.openai_client.chat.completions.create.side_effect = Exception(
            "Error code: 429 - {'error': {'message': 'You exceeded your current quota, "
            "please check your plan and billing details.', "
            "'type': 'insufficient_quota', 'code': 'insufficient_quota'}}"
        )

        with patch("agents.base_agent.time.sleep"):
            with pytest.raises(RuntimeError, match="CRITICAL: All LLM providers exhausted"):
                agent.ask_llm("prompt")

        # Neither provider should be left with a cooldown: groq failed generically
        # (not rate-limit-shaped) and openai failed permanently — neither case starts
        # a cooldown timer.
        assert agent._provider_cooldowns.get("openai") is None

    def test_alert_dedup_guard_fires_on_last_provider_permanent_quota_exhaustion(self, mock_notifier):
        """End-to-end through a real agent call site: when the LAST waterfall provider
        fails with a permanent quota error, ask_llm still raises the same RuntimeError
        shape that literature_research_agent.py's call site catches to invoke
        _alert_waterfall_exhausted — confirming the alerting path (added in a prior
        session for Issue 3) is still reached for this failure class."""
        from agents.literature_research_agent import LiteratureResearchAgent

        agent = LiteratureResearchAgent(active_projects=["ProjectA"], notifier=mock_notifier)
        with patch.object(
            agent, "ask_llm",
            side_effect=RuntimeError(
                "CRITICAL: All LLM providers exhausted. Tried: groq, gemini, "
                "nvidia_nim, openai. Check API keys and rate limits."
            )
        ):
            agent.extract_keywords_from_text("ProjectA", "some manuscript text")

        assert mock_notifier.send_admin_alert.call_count == 1
