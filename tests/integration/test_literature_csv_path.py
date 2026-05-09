"""Integration tests verifying CSV-missing warning in LiteratureResearchAgent."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from agents.base_agent import BaseAgent
from agents.literature_research_agent import LiteratureResearchAgent
from tests.fixtures.mock_responses import VALID_LITERATURE_JSON


class TestCsvMissingLogsWarning:
    """Verifies that a warning is logged when the rolling CSV file is absent."""

    def test_csv_missing_logs_warning(self, mock_notifier, sample_project_name):
        """Warning is logged when the rolling CSV is not found before email send."""
        sample_paper = {
            "title": "Sample WSN Paper",
            "link": "https://example.com/sample",
            "snippet": "Abstract about wireless sensor networks.",
            "year": "2024",
            "citationCount": "5",
            "venue": "IEEE",
            "openalex": {},
        }

        with patch.object(BaseAgent, "ask_llm", return_value=VALID_LITERATURE_JSON):
            agent = LiteratureResearchAgent(
                active_projects=[sample_project_name],
                notifier=mock_notifier,
            )

        with patch.object(agent, "_read_project_text", return_value="sample research text"):
            with patch.object(agent.fetcher, "search", return_value=[sample_paper]):
                with patch.object(agent.fetcher, "fetch_from_openalex", return_value=[]):
                    with patch.object(
                        agent.fetcher, "enrich_with_openalex", return_value=[sample_paper]
                    ):
                        with patch.object(agent.library, "save_literature_summary"):
                            with patch.object(
                                agent.library, "append_to_project_literature_table"
                            ):
                                with patch.object(
                                    BaseAgent, "ask_llm", return_value=VALID_LITERATURE_JSON
                                ):
                                    with patch(
                                        "agents.literature_research_agent.os.path.exists",
                                        return_value=False,
                                    ):
                                        with patch.object(
                                            agent.logger, "warning"
                                        ) as mock_warn:
                                            agent.run()

        warning_templates = [call.args[0] for call in mock_warn.call_args_list]
        assert any("Rolling CSV" in t for t in warning_templates), (
            f"Expected 'Rolling CSV' warning not emitted. Warnings: {warning_templates}"
        )
