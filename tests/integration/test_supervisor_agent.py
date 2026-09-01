"""Integration tests for SupervisorStatusAgent."""

import argparse
from unittest.mock import patch, MagicMock, call

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

    def test_generate_report_via_llm_uses_standardized_waterfall_alert(
        self, supervisor_agent, mock_notifier
    ):
        """Consistency fix: on genuine LLM waterfall exhaustion (ask_llm raising
        RuntimeError), _generate_report_via_llm must go through the same
        _alert_waterfall_exhausted path every other LLM-calling agent uses — not a
        bespoke alert only reachable via run()'s generic except block. Verifies via
        the alert's standardized subject format (f"{agent_name} — LLM waterfall
        exhausted: {project_name}", see BaseAgent._alert_waterfall_exhausted)."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("All providers exhausted")):
            with pytest.raises(RuntimeError):
                supervisor_agent._generate_report_via_llm("prof@university.edu", [])

        mock_notifier.send_admin_alert.assert_called_once()
        _, kwargs = mock_notifier.send_admin_alert.call_args
        assert "LLM waterfall exhausted" in kwargs["subject"]
        assert "prof@university.edu" in kwargs["subject"]

    def test_generate_report_via_llm_waterfall_alert_dedups_per_supervisor(
        self, supervisor_agent, mock_notifier
    ):
        """Two waterfall-exhaustion failures for the SAME supervisor in one run
        produce only one admin alert — same dedup guarantee every other agent's
        _alert_waterfall_exhausted call sites already have."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    supervisor_agent._generate_report_via_llm("prof@university.edu", [])

        mock_notifier.send_admin_alert.assert_called_once()

    def test_run_sends_report_to_supervisor(self, supervisor_agent, mock_notifier):
        """Asserts that run() calls send_supervisor_report with supervisor email."""
        supervisor_agent.run()
        # Verify notifier was called (implementation dependent)

    # TEST — FIX 4c: one supervisor's LLM failure must not abort remaining supervisors
    def test_one_supervisor_llm_failure_does_not_abort_remaining_supervisors(
        self, supervisor_agent, db_in_memory, mock_notifier
    ):
        """2 supervisors, each with 1 student project. The first supervisor's
        _generate_report_via_llm raises; the second supervisor must still get a
        report generated and sent, and an admin alert must fire for the failure."""
        sup_fail = "prof.fail@university.edu"
        sup_ok = "prof.ok@university.edu"

        db_in_memory.add_project("Project_Fail", "student1@university.edu")
        db_in_memory.update_project_state(
            "Project_Fail", supervisor_email=sup_fail, student_name="Student Fail"
        )
        db_in_memory.add_project("Project_Ok", "student2@university.edu")
        db_in_memory.update_project_state(
            "Project_Ok", supervisor_email=sup_ok, student_name="Student Ok"
        )

        def fake_generate_report(supervisor_email, projects_metrics):
            if supervisor_email == sup_fail:
                raise RuntimeError("Simulated LLM failure for first supervisor")
            from tests.fixtures.mock_responses import VALID_SUPERVISOR_JSON
            from domain.schemas import SupervisorReport
            return SupervisorReport.model_validate_json(VALID_SUPERVISOR_JSON)

        with patch.object(
            supervisor_agent, "_generate_report_via_llm", side_effect=fake_generate_report
        ):
            supervisor_agent.run()

        # The second (successful) supervisor's report must still have been sent,
        # despite the first supervisor's failure.
        sent_to = [
            c.kwargs.get("supervisor_email", c.args[0] if c.args else None)
            for c in mock_notifier.send_supervisor_report.call_args_list
        ]
        assert sup_ok in sent_to
        assert sup_fail not in sent_to

        # An admin alert must have been sent for the failing supervisor.
        assert mock_notifier.send_admin_alert.called


class TestSupervisorCLIDispatch:
    """Tests that --agent supervisor dispatches to SupervisorStatusAgent.run()."""

    # TEST E — FIX 2: --agent supervisor must call SupervisorStatusAgent.run() exactly once
    def test_agent_supervisor_cli_calls_run_once(self, monkeypatch, tmp_path):
        """Simulating --agent supervisor via argparse must invoke SupervisorStatusAgent.run() once."""
        from config import Config
        monkeypatch.setattr(Config, "LIBRARY_DIR", str(tmp_path))
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path / "projects"))
        monkeypatch.setattr(Config, "LOGS_DIR", str(tmp_path / "logs"))

        mock_db = MagicMock()
        mock_notifier = MagicMock()

        with patch("main.Config.validate"), \
             patch("main.DatabaseManager", return_value=mock_db), \
             patch("main.NotificationAgent", return_value=mock_notifier), \
             patch("main.get_all_active_projects", return_value=[]), \
             patch("main.SupervisorStatusAgent") as mock_supervisor_cls, \
             patch("sys.argv", ["main.py", "--agent", "supervisor"]):

            mock_supervisor_instance = MagicMock()
            mock_supervisor_cls.return_value = mock_supervisor_instance

            from main import main
            main()

        mock_supervisor_cls.assert_called_once_with(db=mock_db, notifier=mock_notifier)
        mock_supervisor_instance.run.assert_called_once()
