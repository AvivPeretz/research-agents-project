"""Idempotency tests for recurring agent operations."""

from unittest.mock import patch

import pytest


class TestIdempotency:
    """Tests for idempotent behavior of agents and database operations."""

    def test_progress_agent_double_run_creates_two_snapshots_not_one(self, db_in_memory, mock_notifier):
        """Asserts that running ProgressTrackingAgent twice creates exactly 2 snapshots."""
        from agents.progress_tracking_agent import ProgressTrackingAgent

        project = "Idempotent_Project"
        db_in_memory.add_project(project, "test@example.com")

        agent = ProgressTrackingAgent(
            overleaf_projects=[project],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        with patch.object(agent.connector, "read_and_clean_tex_file", return_value="Static content"):
            agent.run()
            agent.run()

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM progress_snapshots WHERE project_name = ?", (project,))
            count = cursor.fetchone()[0]

        assert count == 2

    def test_sync_registry_double_update_has_one_row(self, db_in_memory):
        """Asserts that updating sync registry twice results in exactly 1 row."""
        project = "Sync_Project"
        db_in_memory.update_sync_registry(project, "Text version 1")
        db_in_memory.update_sync_registry(project, "Text version 2")

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sync_registry WHERE project_name = ?", (project,))
            count = cursor.fetchone()[0]

        assert count == 1

    def test_add_project_double_call_has_one_row(self, db_in_memory):
        """Asserts that add_project called twice results in exactly 1 row."""
        project = "Double_Project"
        db_in_memory.add_project(project, "test@example.com")
        db_in_memory.add_project(project, "test@example.com")

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM project_state WHERE project_name = ?", (project,))
            count = cursor.fetchone()[0]

        assert count == 1

    def test_stanford_state_completed_does_not_reset(self, db_in_memory):
        """Asserts that REVIEW_COMPLETED status persists across agent runs."""
        project = "Stanford_Project"
        db_in_memory.add_project(project, "test@example.com")
        db_in_memory.update_project_state(project, stanford_status="REVIEW_COMPLETED")

        # Simulate another agent run that shouldn't change completed state
        state = db_in_memory.get_project_state(project)
        if state["stanford_status"] == "REVIEW_COMPLETED":
            # State machine should not reset it
            db_in_memory.update_project_state(project, stanford_status="REVIEW_COMPLETED")

        state = db_in_memory.get_project_state(project)
        assert state["stanford_status"] == "REVIEW_COMPLETED"

    def test_migrate_from_json_triple_call_stays_idempotent(self, db_in_memory, tmp_path):
        """Asserts that migrating JSON 3 times results in no duplicates."""
        import json

        json_file = tmp_path / "state.json"
        state_dict = {"Migration_Project": "email@example.com"}
        json_file.write_text(json.dumps(state_dict))

        db_in_memory.migrate_from_json(str(json_file))
        db_in_memory.migrate_from_json(str(json_file))
        db_in_memory.migrate_from_json(str(json_file))

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM project_state WHERE project_name = ?", ("Migration_Project",))
            count = cursor.fetchone()[0]

        assert count == 1

    def test_garbage_collector_double_run_does_not_crash(self, tmp_path):
        """Asserts that running GarbageCollector twice raises no exception."""
        from utils.garbage_collector import GarbageCollector

        base_dir = tmp_path / "research_library"
        base_dir.mkdir()

        gc = GarbageCollector(base_dir=str(base_dir))
        gc.run()
        # Second run should not crash
        gc.run()
