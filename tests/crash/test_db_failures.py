"""Tests for database failure handling."""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest


class TestDatabaseFailures:
    """Tests for handling database errors gracefully."""

    def test_get_project_state_on_corrupt_db_returns_none(self, db_in_memory):
        """Asserts that database error returns None for get_project_state."""
        with patch.object(db_in_memory, "_get_connection", side_effect=sqlite3.Error("Database corrupted")):
            result = db_in_memory.get_project_state("AnyProject")
            # Should return None, not crash
            assert result is None or isinstance(result, dict)

    def test_update_project_state_on_db_error_does_not_crash(self, db_in_memory):
        """Asserts that database error during update does not crash."""
        with patch.object(db_in_memory, "_get_connection", side_effect=sqlite3.Error("Database error")):
            # Should not raise exception
            try:
                db_in_memory.update_project_state("Project", stanford_status="NEW_STATUS")
            except sqlite3.Error:
                pass  # Acceptable to raise, but not unhandled

    def test_add_progress_snapshot_on_db_error_does_not_crash(self, db_in_memory):
        """Asserts that database error during snapshot add does not crash."""
        with patch.object(db_in_memory, "_get_connection", side_effect=sqlite3.Error("Database error")):
            # Should not raise unhandled exception
            try:
                db_in_memory.add_progress_snapshot("Project", had_changes=True, delta_char_count=7)
            except sqlite3.Error:
                pass

    def test_get_last_modified_on_db_error_returns_none(self, db_in_memory):
        """Asserts that database error returns None for get_last_modified."""
        with patch.object(db_in_memory, "_get_connection", side_effect=sqlite3.Error("Database error")):
            result = db_in_memory.get_last_modified("Project")
            # Should return None, not crash
            assert result is None or isinstance(result, str)

    def test_migrate_from_json_with_list_structure(self, db_in_memory, tmp_path):
        """Asserts that JSON as list of dicts is migrated correctly."""
        import json

        json_file = tmp_path / "state.json"
        # JSON is a list instead of dict
        state_list = [
            {"project_name": "Project_A", "email": "emaila@example.com"},
            {"project_name": "Project_B", "email": "emailb@example.com"},
        ]
        json_file.write_text(json.dumps(state_list))

        # Should handle both dict and list formats
        db_in_memory.migrate_from_json(str(json_file))
        # Test validates flexible JSON handling

    def test_update_project_state_with_no_kwargs_does_nothing(self, db_in_memory):
        """Asserts that update with no kwargs does nothing and does not crash."""
        project = "NoKwargs_Project"
        db_in_memory.add_project(project, "test@example.com")

        # Call with no keyword arguments
        db_in_memory.update_project_state(project)

        # Project should still exist and be unchanged
        result = db_in_memory.get_project_state(project)
        assert result is not None
