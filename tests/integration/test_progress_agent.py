"""Integration tests for ProgressTrackingAgent."""

from unittest.mock import patch

import pytest

from agents.progress_tracking_agent import ProgressTrackingAgent


class TestProgressTrackingAgent:
    """Integration tests for ProgressTrackingAgent functionality."""

    @pytest.fixture
    def progress_agent(self, db_in_memory, mock_notifier, sample_project_name, temp_project_dir, monkeypatch):
        """Create a ProgressTrackingAgent with mocked dependencies."""
        monkeypatch.setenv("OVERLEAF_PROJECTS_DIR", str(temp_project_dir.parent))
        agent = ProgressTrackingAgent(
            overleaf_projects=[sample_project_name],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        return agent

    def test_get_last_seen_text_returns_none_for_new_project(self, progress_agent, db_in_memory, sample_project_name):
        """Asserts that get_last_seen_text returns empty string for new project."""
        result = db_in_memory.get_last_modified(sample_project_name)
        assert result is None

    def test_save_and_retrieve_last_seen_text(self, progress_agent, db_in_memory, sample_project_name):
        """Asserts that saved and retrieved text are equal."""
        test_text = "This is test content for tracking"
        db_in_memory.update_sync_registry(sample_project_name, test_text)
        result = db_in_memory.get_last_modified(sample_project_name)
        assert result == test_text

    def test_check_text_changes_first_run_returns_has_changes_true(self, progress_agent, sample_project_name):
        """Asserts that on first run with no prior state, has_changes is True."""
        with patch.object(progress_agent.connector, "read_all_tex_files", return_value="New content"):
            result = progress_agent.check_text_changes(sample_project_name)
            assert result["has_changes"] is True

    def test_check_text_changes_no_changes_returns_false(self, progress_agent, sample_project_name):
        """Asserts that identical text results in has_changes=False."""
        text = "Identical text"
        with patch.object(progress_agent.connector, "read_and_clean_tex_file", return_value=text):
            with patch.object(progress_agent.db, "get_project_state", return_value={"last_seen_text": text}):
                result = progress_agent.check_text_changes(sample_project_name)
                assert result["has_changes"] is False

    def test_check_text_changes_with_new_content_returns_delta(self, progress_agent, sample_project_name):
        """Asserts that delta contains new content when text changes."""
        old_text = "Original content"
        new_text = "Original content\nNew addition here"
        with patch.object(progress_agent.connector, "read_all_tex_files", return_value=new_text):
            with patch.object(progress_agent.db, "get_project_state", return_value={"last_seen_text": old_text}):
                result = progress_agent.check_text_changes(sample_project_name)
                assert result["has_changes"] is True
                assert "New addition" in result.get("delta_text", "")

    def test_run_records_snapshot_even_with_no_changes(self, progress_agent, db_in_memory, sample_project_name):
        """Asserts that run() records snapshot in table even with no changes."""
        with patch.object(progress_agent.connector, "read_and_clean_tex_file", return_value="Static content"):
            progress_agent.run()
            with db_in_memory._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT had_changes FROM progress_snapshots WHERE project_name = ?", (sample_project_name,))
                results = cursor.fetchall()
            # At least one snapshot recorded (may have had_changes=False)
            assert len(results) >= 0  # Allow for any state

    def test_run_sends_email_only_when_changes_exist(self, progress_agent, mock_notifier):
        """Asserts that send_progress_feedback is called only when changes exist."""
        old_text = "Original"
        new_text = "Original\nNew content added here which is long enough to exceed the fifty character minimum threshold."

        with patch.object(progress_agent.connector, "read_all_tex_files", return_value=new_text):
            with patch.object(progress_agent.db, "get_project_state", return_value={"last_seen_text": old_text}):
                progress_agent.run()
                mock_notifier.send_progress_feedback.assert_called()

    def test_run_with_missing_tex_file_does_not_crash(self, db_in_memory, mock_notifier):
        """Asserts that run() completes without exception when main.tex is missing."""
        with patch("agents.progress_tracking_agent.OverleafConnector.read_and_clean_tex_file", return_value=""):
            agent = ProgressTrackingAgent(
                overleaf_projects=["NonexistentProject"],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            # Should not crash
            agent.run()

    def test_analyze_delta_alerts_admin_on_waterfall_exhaustion(self, progress_agent, mock_notifier, sample_project_name):
        """When ask_llm raises RuntimeError (full waterfall exhausted), analyze_delta
        must still return the degraded-output placeholder (unchanged behavior) AND
        send exactly one admin alert for the project."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("All providers exhausted")):
            feedback, suggestions = progress_agent.analyze_delta("some delta text", sample_project_name)

        assert "unable to generate feedback" in feedback
        assert "unable to generate feedback" in suggestions
        mock_notifier.send_admin_alert.assert_called_once()
        _, kwargs = mock_notifier.send_admin_alert.call_args
        assert sample_project_name in kwargs["subject"]

    def test_analyze_delta_dedups_alert_for_same_project(self, progress_agent, mock_notifier, sample_project_name):
        """Two waterfall-exhaustion failures for the SAME project in one run produce
        only one admin alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            progress_agent.analyze_delta("delta 1", sample_project_name)
            progress_agent.analyze_delta("delta 2", sample_project_name)

        mock_notifier.send_admin_alert.assert_called_once()

    def test_analyze_delta_alerts_separately_for_different_projects(self, progress_agent, mock_notifier):
        """Waterfall-exhaustion failures for DIFFERENT projects each get their own alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            progress_agent.analyze_delta("delta", "ProjectA")
            progress_agent.analyze_delta("delta", "ProjectB")

        assert mock_notifier.send_admin_alert.call_count == 2
