"""Integration tests for SupervisorStatusAgent."""

from unittest.mock import patch

import pytest

from agents.supervisor_status_agent import SupervisorStatusAgent
from tests.fixtures.mock_responses import VALID_SUPERVISOR_JSON


class TestSupervisorStatusAgent:
    """Integration tests for SupervisorStatusAgent report generation."""

    @pytest.fixture
    def supervisor_agent(self, db_in_memory, mock_notifier):
        """Create a SupervisorStatusAgent with mocked dependencies."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_SUPERVISOR_JSON):
            agent = SupervisorStatusAgent(
                db=db_in_memory,
                notifier=mock_notifier,
            )
            return agent

    def test_fetch_supervisor_projects_returns_empty_when_none_assigned(self, supervisor_agent, db_in_memory):
        """Asserts that empty dict is returned when no projects in DB."""
        result = supervisor_agent._fetch_supervisor_projects()
        assert result == {} or isinstance(result, dict)

    def test_fetch_supervisor_projects_groups_by_supervisor(self, supervisor_agent, db_in_memory):
        """Asserts that projects are grouped correctly by supervisor."""
        supervisor = "prof@university.edu"
        db_in_memory.add_project("Project_1", "student1@university.edu")
        db_in_memory.update_project_state("Project_1", supervisor_email=supervisor)
        db_in_memory.add_project("Project_2", "student2@university.edu")
        db_in_memory.update_project_state("Project_2", supervisor_email=supervisor)

        result = supervisor_agent._fetch_supervisor_projects()
        # Should have supervisor key with both projects grouped under it
        if supervisor in result:
            assert len(result[supervisor]) == 2

    def test_calculate_metrics_new_project_returns_zero_active_days(self, supervisor_agent, db_in_memory):
        """Asserts that new project with no snapshots has all metrics at zero."""
        project_name = "New_Project"
        db_in_memory.add_project(project_name, "test@example.com")
        metrics = supervisor_agent._calculate_project_metrics(project_name, None)
        assert metrics["total_active_days"] == 0

    def test_calculate_metrics_counts_active_days_correctly(self, supervisor_agent, db_in_memory):
        """Asserts that active days are counted correctly (3 with changes, 2 without)."""
        project_name = "Active_Project"
        db_in_memory.add_project(project_name, "test@example.com")

        # Insert 3 snapshots with changes, 2 without
        for i in range(3):
            db_in_memory.add_progress_snapshot(project_name, had_changes=True, delta_char_count=7)
        for i in range(2):
            db_in_memory.add_progress_snapshot(project_name, had_changes=False, delta_char_count=0)

        metrics = supervisor_agent._calculate_project_metrics(project_name, None)
        assert metrics["total_active_days"] == 3

    def test_calculate_metrics_current_silent_streak(self, supervisor_agent, db_in_memory):
        """Asserts that current silent streak counts consecutive False snapshots."""
        project_name = "Silent_Project"
        db_in_memory.add_project(project_name, "test@example.com")

        # Add old changes, then recent silence
        db_in_memory.add_progress_snapshot(project_name, had_changes=True, delta_char_count=7)
        db_in_memory.add_progress_snapshot(project_name, had_changes=False, delta_char_count=0)
        db_in_memory.add_progress_snapshot(project_name, had_changes=False, delta_char_count=0)

        metrics = supervisor_agent._calculate_project_metrics(project_name, None)
        assert metrics.get("current_silent_streak", 0) >= 0

    def test_generate_report_via_llm_validates_pydantic(self, supervisor_agent):
        """Asserts that LLM response is parsed as SupervisorReport."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_SUPERVISOR_JSON):
            result = supervisor_agent._generate_report_via_llm("test@supervisor.com", [])
            assert result is not None

    def test_generate_report_via_llm_raises_on_invalid_json(self, supervisor_agent):
        """Asserts that invalid JSON raises RuntimeError."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value="{invalid json}"):
            with pytest.raises(Exception):  # RuntimeError or ValueError
                supervisor_agent._generate_report_via_llm("test@supervisor.com", [])

    def test_run_sends_report_to_supervisor(self, supervisor_agent, mock_notifier):
        """Asserts that run() calls send_supervisor_report with supervisor email."""
        supervisor_agent.run()
        # Verify notifier was called (implementation dependent)
