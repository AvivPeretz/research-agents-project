"""LIVE tests for the Cerebras and NVIDIA NIM LLM waterfall providers.

WARNING: these tests make REAL network calls to real, billed external APIs
(Cerebras and NVIDIA NIM) through the actual production code path in
agents/base_agent.py (BaseAgent._ask_provider / BaseAgent.ask_llm) — they do
NOT mock the provider SDKs. They require CEREBRAS_API_KEY and
NVIDIA_NIM_API_KEY to be present in the environment (loaded from .env by the
normal `Config`/`os.getenv` path). They are NOT part of the fast unit-test
suite and are deselected by default via the `live` marker registered in
pytest.ini (`addopts = -m "not live"`).

Run explicitly with:

    pytest -m live tests/live/test_llm_providers_live.py -v

Do NOT add this file/marker to any fast/CI test run. Every test in this file
is skipped automatically if the relevant API key is not set, so it is safe
to leave in a checkout without keys configured.
"""

import os

import pytest

from agents.base_agent import BaseAgent, _CerebrasProactiveRateLimitError
from config import Config

pytestmark = pytest.mark.live

CEREBRAS_KEY_PRESENT = bool(os.getenv("CEREBRAS_API_KEY"))
NVIDIA_NIM_KEY_PRESENT = bool(os.getenv("NVIDIA_NIM_API_KEY"))


class _LiveProbeAgent(BaseAgent):
    """Minimal concrete BaseAgent subclass — BaseAgent is abstract (requires
    `run()`), so a live test needs some concrete subclass to instantiate. This
    one has no behavior beyond what BaseAgent itself provides; it exists only
    so these tests exercise the REAL BaseAgent.__init__ / ask_llm / _ask_provider
    code path rather than a bypass script that calls the provider SDKs directly."""

    def run(self):
        pass


@pytest.fixture
def live_agent():
    return _LiveProbeAgent(agent_name="LiveProbeAgent")


@pytest.mark.skipif(not CEREBRAS_KEY_PRESENT, reason="CEREBRAS_API_KEY not set")
def test_cerebras_direct_call_well_formed_response(live_agent):
    """Real call to Cerebras through BaseAgent._ask_provider('cerebras', ...).

    Confirms: (1) a well-formed, non-empty response comes back, and (2) the
    proactive _SlidingWindowLimiter (_cerebras_rate_limiter) does not
    false-trigger a synthetic _CerebrasProactiveRateLimitError on a single
    legitimate call — i.e. whatever happens, it must not be the limiter that
    rejected the call before a real request was even sent.

    NOTE: as of 2026-08-21 this test fails with a real APIStatusError 402
    "Payment required to access this resource" from Cerebras's own API on
    both models available to this account (gpt-oss-120b and gemma-4-31b) —
    an account billing/quota issue, not a code defect. The assertions below
    still hold diagnostic value: they confirm the limiter allowed the call
    through and a REAL network round-trip to Cerebras occurred (a genuine
    HTTP-level provider error, not the limiter's synthetic exception).
    """
    before = len(live_agent._cerebras_rate_limiter._timestamps)
    try:
        response = live_agent._ask_provider(
            "cerebras", "Reply with exactly the single word: PONG"
        )
    except _CerebrasProactiveRateLimitError:
        pytest.fail(
            "Proactive rate limiter false-triggered on a single legitimate "
            "call — this is the exact bug class the limiter's cooldown-scope "
            "fix was meant to prevent."
        )
    except Exception as e:
        # A real provider-side error (e.g. billing/quota) is NOT a limiter
        # false-trigger, but it does mean we cannot assert a well-formed
        # response body here. Surface it clearly rather than silently
        # swallowing it, and confirm the limiter still reserved a slot
        # (proving the call was actually attempted, not synthetically
        # blocked pre-flight).
        after = len(live_agent._cerebras_rate_limiter._timestamps)
        assert after == before + 1, (
            "Limiter did not reserve a slot for this call attempt — "
            "unexpected limiter state."
        )
        pytest.fail(
            f"Cerebras call reached the real API and failed with a genuine "
            f"provider-side error (not a limiter false-trigger): {e!r}"
        )
    else:
        assert isinstance(response, str)
        assert response.strip() != ""
        after = len(live_agent._cerebras_rate_limiter._timestamps)
        assert after == before + 1


@pytest.mark.skipif(not NVIDIA_NIM_KEY_PRESENT, reason="NVIDIA_NIM_API_KEY not set")
def test_nvidia_nim_direct_call_well_formed_response(live_agent):
    """Real call to NVIDIA NIM through BaseAgent._ask_provider('nvidia_nim', ...),
    using the exact Config.NVIDIA_NIM_MODEL_NAME / Config.NVIDIA_NIM_BASE_URL
    values wired into the production waterfall."""
    response = live_agent._ask_provider(
        "nvidia_nim", "Reply with exactly the single word: PONG"
    )
    assert isinstance(response, str)
    assert response.strip() != ""


@pytest.mark.skipif(
    not (CEREBRAS_KEY_PRESENT and NVIDIA_NIM_KEY_PRESENT),
    reason="requires both CEREBRAS_API_KEY and NVIDIA_NIM_API_KEY",
)
def test_ask_llm_cascades_past_failing_groq_and_gemini(live_agent, monkeypatch, caplog):
    """End-to-end waterfall test: force Groq and Gemini to fail (via
    monkeypatching the already-constructed real clients, not by touching
    .env or Config API keys — Groq's key is required at BaseAgent.__init__
    time, so it cannot be unset post-construction), then call the REAL
    ask_llm() and confirm it naturally cascades down to Cerebras and/or
    NVIDIA NIM with REAL network calls (no provider SDK mocking below Groq/
    Gemini) and returns a well-formed response.

    This is the actual selection/fallback logic in BaseAgent.ask_llm, not a
    bypass — it proves the waterfall order and retry/cooldown classification
    work end-to-end with real providers, not just that each provider works in
    isolation via _ask_provider.
    """

    def _broken_groq_create(*args, **kwargs):
        raise RuntimeError("simulated Groq outage — forced fallback for live cascade test")

    def _broken_gemini_generate(*args, **kwargs):
        raise RuntimeError("simulated Gemini outage — forced fallback for live cascade test")

    monkeypatch.setattr(
        live_agent.groq_client.chat.completions, "create", _broken_groq_create
    )
    monkeypatch.setattr(
        live_agent.gemini_client.models, "generate_content", _broken_gemini_generate
    )

    import logging

    with caplog.at_level(logging.INFO):
        response = live_agent.ask_llm(
            "Reply with exactly the single word: PONG"
        )

    assert isinstance(response, str)
    assert response.strip() != ""

    routed_to = [
        rec.message for rec in caplog.records if "Routing request to LLM provider" in rec.message
    ]
    # Groq must have been tried and failed first (proves we didn't skip it).
    assert any("GROQ" in msg for msg in routed_to), routed_to
    assert any("GEMINI" in msg for msg in routed_to), routed_to
    # And the cascade must have reached at least Cerebras (real call, whatever
    # the outcome) before landing on a working provider.
    assert any("CEREBRAS" in msg for msg in routed_to), routed_to
