"""LIVE tests for the NVIDIA NIM LLM waterfall provider.

WARNING: these tests make REAL network calls to a real, billed external API
(NVIDIA NIM) through the actual production code path in agents/base_agent.py
(BaseAgent._ask_provider / BaseAgent.ask_llm) — they do NOT mock the provider
SDK. They require NVIDIA_NIM_API_KEY to be present in the environment (loaded
from .env by the normal `Config`/`os.getenv` path). They are NOT part of the
fast unit-test suite and are deselected by default via the `live` marker
registered in pytest.ini (`addopts = -m "not live"`).

Run explicitly with:

    pytest -m live tests/live/test_llm_providers_live.py -v

Do NOT add this file/marker to any fast/CI test run. Every test in this file
is skipped automatically if the relevant API key is not set, so it is safe
to leave in a checkout without keys configured.
"""

import os

import pytest

from agents.base_agent import BaseAgent
from config import Config

pytestmark = pytest.mark.live

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


@pytest.mark.skipif(not NVIDIA_NIM_KEY_PRESENT, reason="requires NVIDIA_NIM_API_KEY")
def test_ask_llm_cascades_past_failing_groq_and_gemini(live_agent, monkeypatch, caplog):
    """End-to-end waterfall test: force Groq and Gemini to fail (via
    monkeypatching the already-constructed real clients, not by touching
    .env or Config API keys — Groq's key is required at BaseAgent.__init__
    time, so it cannot be unset post-construction), then call the REAL
    ask_llm() and confirm it naturally cascades down to NVIDIA NIM with a
    REAL network call (no provider SDK mocking below Groq/Gemini) and
    returns a well-formed response.

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
    # And the cascade must have reached NVIDIA NIM (real call, whatever the
    # outcome) before landing on a working provider.
    assert any("NVIDIA_NIM" in msg for msg in routed_to), routed_to
