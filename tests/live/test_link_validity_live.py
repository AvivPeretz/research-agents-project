"""LIVE test for LiteratureResearchAgent's standing link-validity filter (Part C).

WARNING: this test makes a REAL network connection attempt through the actual
production code path (LiteratureResearchAgent._paper_link_is_valid /
_filter_dead_links). It targets a constructed, non-existent domain designed to
reliably fail DNS resolution / connection — NOT a live real paper/publisher
site — so it never hammers a real third-party service. Deselected by default
via the `live` marker registered in pytest.ini (`addopts = -m "not live"`),
matching the convention already used by tests/live/test_llm_providers_live.py.

Run explicitly with:

    pytest -m live tests/live/test_link_validity_live.py -v
"""

import pytest

from agents.literature_research_agent import LiteratureResearchAgent
from agents.notification_agent import NotificationAgent

pytestmark = pytest.mark.live


@pytest.fixture
def live_link_check_agent():
    # A real NotificationAgent-shaped object isn't needed for this check (the
    # link filter never touches self.notifier), but LiteratureResearchAgent's
    # constructor requires a notifier argument, so a minimal real instance is
    # used rather than a mock, keeping this test's LiteratureResearchAgent
    # construction path as close to the real one as reasonably possible.
    return LiteratureResearchAgent(active_projects=[], notifier=NotificationAgent())


def test_genuinely_dead_url_excluded_over_real_network(live_link_check_agent):
    """A real HTTP(S) connection attempt to a domain reserved for documentation/
    testing use (RFC 2606 .invalid TLD — guaranteed to never resolve) must fail
    DNS resolution, and _paper_link_is_valid must handle that real exception
    gracefully and exclude the paper rather than raise."""
    paper = {
        "title": "This Paper Does Not Exist",
        "link": "https://this-domain-absolutely-does-not-exist-12345.invalid/paper",
    }

    result = live_link_check_agent._paper_link_is_valid(paper)

    assert result is False


def test_filter_dead_links_excludes_over_real_network(live_link_check_agent):
    """Same as above but through the batch entry point used by _process_project,
    confirming the ThreadPoolExecutor wiring also works over a real (failing)
    connection attempt, not just the single-paper helper."""
    papers = [
        {"title": "This Paper Does Not Exist", "link": "https://this-domain-absolutely-does-not-exist-12345.invalid/paper"},
    ]

    result = live_link_check_agent._filter_dead_links("LiveTestProject", papers)

    assert result == []
