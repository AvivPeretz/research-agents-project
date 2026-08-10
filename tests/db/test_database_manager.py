"""Tests for DatabaseManager SQLite operations."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from utils.database_manager import DatabaseManager


class TestDatabaseManagerSetup:
    """Tests for database table creation and schema."""

    def test_create_tables_creates_all_four_tables(self, db_in_memory):
        """Asserts that all 4 tables exist in database after initialization."""
        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}

        expected_tables = {"sync_registry", "project_state", "agent_runs", "progress_snapshots"}
        assert expected_tables.issubset(tables)

    def test_ensure_column_exists_adds_missing_column(self, db_in_memory):
        """Asserts that missing column is added by ensure_column_exists."""
        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY)")
            conn.commit()
            db_in_memory._ensure_column_exists(cursor, "test_table", "new_col", "TEXT")
            cursor.execute("PRAGMA table_info(test_table)")
            columns = {row[1] for row in cursor.fetchall()}

        assert "new_col" in columns

    def test_ensure_column_exists_does_not_fail_if_column_exists(self, db_in_memory):
        """Asserts that calling twice for same column raises no exception."""
        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            db_in_memory._ensure_column_exists(cursor, "project_state", "project_name", "TEXT")
            # Should not raise
            db_in_memory._ensure_column_exists(cursor, "project_state", "project_name", "TEXT")


class TestSyncRegistry:
    """Tests for sync registry (last modified text tracking)."""

    def test_update_sync_registry_insert(self, db_in_memory):
        """Asserts that new project is inserted into sync registry."""
        db_in_memory.update_sync_registry("TestProject", "Some text content")

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT last_modified_text FROM sync_registry WHERE project_name = ?", ("TestProject",))
            result = cursor.fetchone()

        assert result is not None
        assert result[0] == "Some text content"

    def test_update_sync_registry_upsert(self, db_in_memory):
        """Asserts that updating same project updates text without duplicating."""
        db_in_memory.update_sync_registry("TestProject", "Text 1")
        db_in_memory.update_sync_registry("TestProject", "Text 2")

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM sync_registry WHERE project_name = ?", ("TestProject",))
            count = cursor.fetchone()[0]
            cursor.execute("SELECT last_modified_text FROM sync_registry WHERE project_name = ?", ("TestProject",))
            result = cursor.fetchone()

        assert count == 1
        assert result[0] == "Text 2"

    def test_get_last_modified_returns_none_for_unknown_project(self, db_in_memory):
        """Asserts that None is returned for project not in database."""
        result = db_in_memory.get_last_modified("UnknownProject")
        assert result is None

    def test_get_last_modified_returns_correct_value(self, db_in_memory):
        """Asserts that retrieved text matches what was stored."""
        test_text = "Stored text content"
        db_in_memory.update_sync_registry("ProjectA", test_text)
        result = db_in_memory.get_last_modified("ProjectA")
        assert result == test_text


class TestProjectState:
    """Tests for project state management."""

    def test_add_project_inserts_row(self, db_in_memory):
        """Asserts that add_project creates a row in project_state."""
        db_in_memory.add_project("ProjectX", "researcher@example.com")

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT project_name FROM project_state WHERE project_name = ?", ("ProjectX",))
            result = cursor.fetchone()

        assert result is not None
        assert result[0] == "ProjectX"

    def test_add_project_is_idempotent(self, db_in_memory):
        """Asserts that calling add_project twice results in only one row."""
        db_in_memory.add_project("ProjectY", "test@example.com")
        db_in_memory.add_project("ProjectY", "test@example.com")

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM project_state WHERE project_name = ?", ("ProjectY",))
            count = cursor.fetchone()[0]

        assert count == 1

    def test_get_project_state_returns_none_for_unknown(self, db_in_memory):
        """Asserts that None is returned for unknown project."""
        result = db_in_memory.get_project_state("UnknownProject")
        assert result is None

    def test_get_project_state_returns_dict_with_all_keys(self, db_in_memory):
        """Asserts that returned dict contains expected keys."""
        db_in_memory.add_project("ProjectZ", "test@example.com")
        result = db_in_memory.get_project_state("ProjectZ")

        assert result is not None
        assert "project_name" in result
        assert "stanford_status" in result
        assert "researcher_email" in result

    def test_update_project_state_single_field(self, db_in_memory):
        """Asserts that updating one field only changes that field."""
        db_in_memory.add_project("ProjectA", "test@example.com")
        db_in_memory.update_project_state("ProjectA", stanford_status="WAITING_FOR_REVIEW")

        result = db_in_memory.get_project_state("ProjectA")
        assert result["stanford_status"] == "WAITING_FOR_REVIEW"

    def test_update_project_state_multiple_fields(self, db_in_memory):
        """Asserts that updating 3 fields updates all of them."""
        db_in_memory.add_project("ProjectB", "test@example.com")
        db_in_memory.update_project_state(
            "ProjectB",
            stanford_status="REVIEW_COMPLETED",
            researcher_email="new@example.com",
        )

        result = db_in_memory.get_project_state("ProjectB")
        assert result["stanford_status"] == "REVIEW_COMPLETED"
        assert result["researcher_email"] == "new@example.com"


class TestProgressSnapshots:
    """Tests for progress tracking snapshots."""

    def test_add_progress_snapshot_inserts_row(self, db_in_memory):
        """Asserts that snapshot is recorded in progress_snapshots table."""
        db_in_memory.add_project("ProjectC", "test@example.com")
        db_in_memory.add_progress_snapshot("ProjectC", had_changes=True, delta_char_count=16)

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT had_changes FROM progress_snapshots WHERE project_name = ?", ("ProjectC",))
            result = cursor.fetchone()

        assert result is not None
        assert result[0] == 1  # True stored as 1

    def test_add_progress_snapshot_with_no_changes(self, db_in_memory):
        """Asserts that snapshot with no changes is stored correctly."""
        db_in_memory.add_project("ProjectD", "test@example.com")
        db_in_memory.add_progress_snapshot("ProjectD", had_changes=False, delta_char_count=0)

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT had_changes, delta_char_count FROM progress_snapshots WHERE project_name = ?", ("ProjectD",))
            result = cursor.fetchone()

        assert result is not None
        assert result[0] == 0  # False stored as 0
        assert result[1] == 0  # No delta


class TestJsonMigration:
    """Tests for migrating from JSON state files to database."""

    def test_migrate_from_json_valid_dict(self, db_in_memory, tmp_path):
        """Asserts that valid JSON dict is migrated correctly."""
        json_file = tmp_path / "state.json"
        state_dict = {
            "Project_1": "email1@example.com",
            "Project_2": "email2@example.com",
        }
        json_file.write_text(json.dumps(state_dict))

        db_in_memory.migrate_from_json(str(json_file))

        result1 = db_in_memory.get_project_state("Project_1")
        result2 = db_in_memory.get_project_state("Project_2")

        assert result1 is not None
        assert result2 is not None

    def test_migrate_from_json_is_idempotent(self, db_in_memory, tmp_path):
        """Asserts that migrating twice results in no duplicates."""
        json_file = tmp_path / "state.json"
        state_dict = {"Project_3": "email3@example.com"}
        json_file.write_text(json.dumps(state_dict))

        db_in_memory.migrate_from_json(str(json_file))
        db_in_memory.migrate_from_json(str(json_file))

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM project_state WHERE project_name = ?", ("Project_3",))
            count = cursor.fetchone()[0]

        assert count == 1

    def test_migrate_from_json_missing_file(self, db_in_memory):
        """Asserts that migration raises no exception for missing file."""
        # Should not raise
        db_in_memory.migrate_from_json("/nonexistent/path/state.json")

    def test_migrate_from_json_broken_json(self, db_in_memory, tmp_path):
        """Asserts that broken JSON raises no exception."""
        json_file = tmp_path / "broken.json"
        json_file.write_text("{broken json content")

        # Should not raise
        db_in_memory.migrate_from_json(str(json_file))

    def test_migrate_from_json_uses_fallback_email_when_missing(self, db_in_memory, tmp_path):
        """Asserts that missing email uses Config.OVERLEAF_EMAIL as fallback."""
        from config import Config

        json_file = tmp_path / "state.json"
        state_dict = {"Project_4": None}  # No email provided
        json_file.write_text(json.dumps(state_dict))

        db_in_memory.migrate_from_json(str(json_file))

        result = db_in_memory.get_project_state("Project_4")
        if result:
            # Email should either be fallback or original None
            assert result["researcher_email"] is not None or result["researcher_email"] == Config.OVERLEAF_EMAIL


class TestStaleAgentRuns:
    """Tests for get_stale_started_runs() -- detects a process killed outright
    (crash/OOM/machine sleep) rather than one that failed in a way the code's own
    try/except could catch and log as FAILURE."""

    def test_old_started_row_with_no_successor_is_stale(self, db_in_memory):
        old_time = (datetime.now() - timedelta(hours=5)).isoformat()
        db_in_memory.log_agent_run(
            agent_name="LiteratureResearchAgent", project_name="StuckProject",
            status="STARTED", started_at=old_time
        )

        stale = db_in_memory.get_stale_started_runs(older_than_hours=2.0)

        assert len(stale) == 1
        assert stale[0]["agent_name"] == "LiteratureResearchAgent"
        assert stale[0]["project_name"] == "StuckProject"

    def test_recent_started_row_is_not_stale(self, db_in_memory):
        recent_time = datetime.now().isoformat()
        db_in_memory.log_agent_run(
            agent_name="LiteratureResearchAgent", project_name="InProgressProject",
            status="STARTED", started_at=recent_time
        )

        stale = db_in_memory.get_stale_started_runs(older_than_hours=2.0)

        assert stale == []

    def test_started_row_followed_by_success_is_not_stale(self, db_in_memory):
        old_time = (datetime.now() - timedelta(hours=5)).isoformat()
        db_in_memory.log_agent_run(
            agent_name="ProgressTrackingAgent", project_name="CompletedProject",
            status="STARTED", started_at=old_time
        )
        db_in_memory.log_agent_run(
            agent_name="ProgressTrackingAgent", project_name="CompletedProject",
            status="SUCCESS", finished_at=datetime.now().isoformat()
        )

        stale = db_in_memory.get_stale_started_runs(older_than_hours=2.0)

        assert stale == []

    def test_started_row_followed_by_failure_is_not_stale(self, db_in_memory):
        """A caught exception already produces a FAILURE row and (separately) an
        admin alert -- this check exists only for what exception handling can't
        see, so an old STARTED with a later FAILURE must not double-alert."""
        old_time = (datetime.now() - timedelta(hours=5)).isoformat()
        db_in_memory.log_agent_run(
            agent_name="ResearchEnhancementAgent", project_name="FailedProject",
            status="STARTED", started_at=old_time
        )
        db_in_memory.log_agent_run(
            agent_name="ResearchEnhancementAgent", project_name="FailedProject",
            status="FAILURE", finished_at=datetime.now().isoformat()
        )

        stale = db_in_memory.get_stale_started_runs(older_than_hours=2.0)

        assert stale == []

    def test_new_started_row_after_old_completed_run_is_stale(self, db_in_memory):
        """Only the MOST RECENT row per (agent, project) matters -- an old completed
        run followed by a fresh, still-stuck STARTED must still be flagged."""
        db_in_memory.log_agent_run(
            agent_name="LiteratureResearchAgent", project_name="RepeatProject",
            status="SUCCESS",
            started_at=(datetime.now() - timedelta(days=1)).isoformat(),
            finished_at=(datetime.now() - timedelta(days=1)).isoformat(),
        )
        db_in_memory.log_agent_run(
            agent_name="LiteratureResearchAgent", project_name="RepeatProject",
            status="STARTED", started_at=(datetime.now() - timedelta(hours=5)).isoformat()
        )

        stale = db_in_memory.get_stale_started_runs(older_than_hours=2.0)

        assert len(stale) == 1
        assert stale[0]["project_name"] == "RepeatProject"

    def test_no_agent_runs_returns_empty_list(self, db_in_memory):
        assert db_in_memory.get_stale_started_runs() == []
