"""Regression guard for a real drift bug found in a fresh-eyes architecture review:
dashboard.py's "LLM Waterfall Status" table used to be an independently-hardcoded
list that omitted Gemma 4 and NVIDIA NIM entirely and mislabeled OpenAI's fallback
position, out of sync with the real waterfall BaseAgent._setup_llm() actually builds.

The fix makes this structurally impossible rather than just tested-for: both
BaseAgent._setup_llm() and dashboard.py now derive the provider order from the same
single source of truth, BaseAgent.PROVIDER_ORDER. This test runs the actual dashboard
script (via Streamlit's AppTest, not a mock) and confirms the rendered table's
provider list genuinely matches that constant — so if either dashboard.py or
BaseAgent.PROVIDER_ORDER is ever changed without the other, this test fails.
"""

import os

from agents.base_agent import BaseAgent

_DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "dashboard.py")


class TestDashboardProviderSync:
    def _get_waterfall_table(self):
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(_DASHBOARD_PATH)
        at.run(timeout=30)
        assert not at.exception, f"dashboard.py raised on initial load: {at.exception}"
        at.radio[0].set_value("Config").run(timeout=30)
        assert not at.exception, f"dashboard.py raised on Config page: {at.exception}"

        # The "LLM Waterfall Status" table is the last st.table on the Config page —
        # find it by its actual columns rather than a hardcoded index, so this test
        # doesn't silently start checking the wrong table if another table is added
        # above it later.
        for t in at.table:
            if list(t.value.columns) == ["Provider", "Role", "Model", "Configured"]:
                return t.value
        raise AssertionError("Could not find the LLM Waterfall Status table on the Config page.")

    def test_dashboard_provider_count_and_order_matches_base_agent(self):
        """The dashboard's rendered provider list, in order, must exactly match
        BaseAgent.PROVIDER_ORDER — the same constant the real waterfall is built
        from — both in count and in sequence."""
        df = self._get_waterfall_table()
        expected_display_names = {
            "groq": "Groq",
            "gemini": "Gemini",
            "gemma": "Gemma 4",
            "nvidia_nim": "NVIDIA NIM",
            "openai": "OpenAI",
        }
        expected = [expected_display_names[p] for p in BaseAgent.PROVIDER_ORDER]
        actual = df["Provider"].tolist()
        assert actual == expected, (
            f"Dashboard provider list {actual} does not match BaseAgent.PROVIDER_ORDER "
            f"{expected} — this is exactly the drift class this test exists to catch."
        )

    def test_dashboard_fallback_labels_match_position(self):
        """Role labels must correctly reflect each provider's real position in the
        waterfall (Primary, then Fallback 1..N in order) — regression test for the
        specific old bug where OpenAI was mislabeled 'Fallback 2' when it's actually
        the 4th/last fallback tier."""
        df = self._get_waterfall_table()
        roles = df["Role"].tolist()
        assert roles[0] == "Primary"
        assert roles[1:] == [f"Fallback {i}" for i in range(1, len(BaseAgent.PROVIDER_ORDER))]
        # The specific historical bug: OpenAI must be labeled as the LAST fallback,
        # not "Fallback 2".
        openai_idx = df["Provider"].tolist().index("OpenAI")
        assert roles[openai_idx] == f"Fallback {len(BaseAgent.PROVIDER_ORDER) - 1}"

    def test_base_agent_provider_order_has_no_duplicates_and_is_nonempty(self):
        """Sanity check on the source-of-truth constant itself."""
        assert len(BaseAgent.PROVIDER_ORDER) == len(set(BaseAgent.PROVIDER_ORDER))
        assert len(BaseAgent.PROVIDER_ORDER) >= 1
        assert BaseAgent.PROVIDER_ORDER[0] == "groq"  # Groq is always the mandatory primary
