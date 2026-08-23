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
GEMINI_KEY_PRESENT = bool(os.getenv("GEMINI_API_KEY"))
GROQ_KEY_PRESENT = bool(os.getenv("GROQ_API_KEY"))


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


@pytest.mark.skipif(not GROQ_KEY_PRESENT, reason="GROQ_API_KEY not set")
def test_groq_direct_call_well_formed_response(live_agent):
    """Real call to Groq (the primary/default provider) through
    BaseAgent._ask_provider('groq', ...). Groq's success path is exercised
    indirectly by nearly every other test in this file (it's always tried
    first), but nothing here asserted a real, successful Groq response on its
    own — the other tests only ever force it to fail so the cascade can be
    observed. This closes that gap."""
    response = live_agent._ask_provider(
        "groq", "Reply with exactly the single word: PONG"
    )
    assert isinstance(response, str)
    assert response.strip() != ""


@pytest.mark.skipif(not GEMINI_KEY_PRESENT, reason="GEMINI_API_KEY not set")
def test_gemini_direct_call_well_formed_response(live_agent):
    """Real call to Gemini through BaseAgent._ask_provider('gemini', ...)."""
    response = live_agent._ask_provider(
        "gemini", "Reply with exactly the single word: PONG"
    )
    assert isinstance(response, str)
    assert response.strip() != ""


@pytest.mark.skipif(not GEMINI_KEY_PRESENT, reason="requires GEMINI_API_KEY (Gemma 4 shares Gemini's key/client)")
def test_gemma_direct_call_well_formed_response(live_agent):
    """Real call to Gemma 4 through BaseAgent._ask_provider('gemma', ...). Gemma
    reuses the same genai.Client/GEMINI_API_KEY as plain Gemini (see base_agent.py's
    _ask_provider docstring) — only the model id differs."""
    response = live_agent._ask_provider(
        "gemma", "Reply with exactly the single word: PONG"
    )
    assert isinstance(response, str)
    assert response.strip() != ""


@pytest.mark.skipif(not GEMINI_KEY_PRESENT, reason="requires GEMINI_API_KEY")
def test_ask_llm_cascades_to_gemma_when_groq_and_gemini_excluded(live_agent):
    """Confirms the real waterfall selection logic lands specifically on Gemma 4
    (not NVIDIA NIM, not OpenAI) when Groq and Gemini are excluded — a real call
    served by exactly the Gemma tier, not a bypass.

    NOTE on mechanism: Gemma's availability in BaseAgent._setup_llm() is gated on
    gemini_available (they deliberately share the same genai.Client/GEMINI_API_KEY —
    see _setup_llm()'s Gemma 4 comment block). Because of that coupling,
    monkeypatching Config.GEMINI_API_KEY to None BEFORE construction would disable
    Gemma too, not just Gemini, making it impossible to isolate "Gemini excluded,
    Gemma still available" that way. Instead this test lets _setup_llm() run for
    real (so the real shared gemini_client and both availability flags come up
    genuinely true) and then prunes the already-computed, cached
    self._providers_waterfall list post-construction — the exact list ask_llm()
    iterates — to drop "groq" and "gemini". Gemma's own client/model wiring is
    completely untouched, so its response below is a genuinely real call."""
    assert live_agent.gemini_available and live_agent.gemma_available
    live_agent._providers_waterfall = [
        p for p in live_agent._providers_waterfall if p not in ("groq", "gemini")
    ]
    assert live_agent._providers_waterfall[0] == "gemma"

    served_by = {}
    orig_ask_provider = live_agent._ask_provider

    def _tracking_ask_provider(provider_name, prompt, model_override=None):
        result = orig_ask_provider(provider_name, prompt, model_override=model_override)
        served_by["provider"] = provider_name
        return result

    live_agent._ask_provider = _tracking_ask_provider

    response = live_agent.ask_llm("Reply with exactly the single word: PONG")

    assert served_by.get("provider") == "gemma", served_by
    assert isinstance(response, str)
    assert response.strip() != ""


def test_waterfall_exhaustion_raises_and_alerts_with_five_tiers(monkeypatch, tmp_path):
    """End-to-end: force all 5 waterfall providers to fail (mocked — this is the one
    sub-test in this file that intentionally mocks provider SDK calls, since the
    point is to prove the exhaustion/alert PATH, not each provider's live success),
    confirm the real ask_llm() raises RuntimeError, and confirm
    ProgressTrackingAgent.analyze_delta's real call site fires exactly one deduplicated
    _alert_waterfall_exhausted admin email reflecting the current 5-tier roster.

    No real network calls happen here at all (every provider SDK call is mocked to
    fail before any request goes out, and NotificationAgent._dispatch_email is
    mocked), so unlike the rest of this file this test costs zero API quota per run
    — it's included here (not left as a throwaway script) specifically to catch a
    future regression where a 6th tier is added but the alert/roster wiring isn't
    updated, or where the dedup-per-project guard breaks.

    Still requires GROQ_API_KEY (mandatory at BaseAgent.__init__) — hence still
    living under `-m live` / this skip-if-key-missing file, even though no bytes
    cross the network."""
    if not GROQ_KEY_PRESENT:
        pytest.skip("GROQ_API_KEY not set")

    monkeypatch.setattr(Config, "LIBRARY_DIR", str(tmp_path))

    from agents.progress_tracking_agent import ProgressTrackingAgent
    from agents.notification_agent import NotificationAgent

    BaseAgent._provider_cooldowns.clear()
    BaseAgent._shared_db_manager = None

    notifier = NotificationAgent()
    dispatch_calls = []

    def _fake_dispatch_email(self, msg, recipient):
        dispatch_calls.append({"subject": msg.get("Subject"), "to": recipient})
        return True

    monkeypatch.setattr(NotificationAgent, "_dispatch_email", _fake_dispatch_email)

    agent = ProgressTrackingAgent(overleaf_projects=["FakeProject"], notifier=notifier, db=None)
    assert agent._providers_waterfall == ["groq", "gemini", "gemma", "nvidia_nim", "openai"]

    def _fail(*args, **kwargs):
        raise RuntimeError("Error code: 429 - rate_limit_exceeded: simulated outage")

    monkeypatch.setattr(agent.groq_client.chat.completions, "create", _fail)
    if agent.gemini_available:
        monkeypatch.setattr(agent.gemini_client.models, "generate_content", _fail)
    if agent.nvidia_nim_available:
        monkeypatch.setattr(agent.nvidia_nim_client.chat.completions, "create", _fail)
    if agent.openai_available:
        monkeypatch.setattr(agent.openai_client.chat.completions, "create", _fail)

    with pytest.raises(RuntimeError):
        agent.ask_llm("test prompt")

    feedback, suggestions = agent.analyze_delta("some new delta text", project="FakeProject")
    assert "unable to generate feedback" in feedback

    assert len(dispatch_calls) == 1
    assert "FakeProject" in dispatch_calls[0]["subject"]

    # Dedup: a second exhaustion for the same project must not send a second alert.
    agent.analyze_delta("more delta text", project="FakeProject")
    assert len(dispatch_calls) == 1
