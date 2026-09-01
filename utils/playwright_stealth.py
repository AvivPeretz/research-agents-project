"""Shared, GENERIC Playwright browser-automation hygiene — stealth launch args,
a consistent browser fingerprint (UA/viewport/locale/timezone), and human-like
delay timing. Extracted from two previously independent, duplicated
implementations: ingestion/data_ingestion_agent.py (Overleaf) and
agents/research_enhancement_agent.py (Stanford paperreview.ai).

Deliberately does NOT include anything login/session/auth-specific for either
service — that logic (storage_state loading, credential typing, session-health
checks, manual-login flows) stays local to ingestion/data_ingestion_agent.py,
which is the only one of the two that has any login/session concept at all
(Stanford's upload_to_stanford is a stateless, one-shot form submission with no
account/session concept whatsoever — confirmed by reading its full
implementation before this consolidation). This module is intentionally scoped
to what's genuinely shareable without touching auth/session behavior.
"""

import time
import random

# The exact stealth launch args both implementations already used, byte-for-byte
# identical before this consolidation — reducing Playwright's own automation
# fingerprint (not bypassing any login/CAPTCHA flow itself).
STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

# The exact browser context fingerprint DataIngestionAgent already used for
# Overleaf. Offered here as a shared, consistent default so any Playwright
# consumer in this codebase presents the same fingerprint rather than each
# service inventing (or omitting) its own — a real hygiene gap this
# consolidation closes: ResearchEnhancementAgent.upload_to_stanford previously
# used a bare browser.new_context() with no UA/viewport/locale at all.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


def default_stealth_context_kwargs() -> dict:
    """Returns a fresh dict of the shared context fingerprint (UA, viewport,
    locale, timezone) — a plain dict, not a mutable shared default, so each
    caller can safely add/override keys (storage_state, accept_downloads, etc.)
    without affecting other callers."""
    return {
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": {"width": 1440, "height": 900},
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }


def human_delay(min_ms: int = 800, max_ms: int = 2200) -> None:
    """Pauses for a randomized duration to mimic human interaction timing —
    generic timing hygiene, no auth/session logic."""
    time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))
