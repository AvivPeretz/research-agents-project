import os
import json
import sqlite3
import logging
from datetime import datetime

# Import the centralized configuration
from config import Config

class DatabaseManager:
    """
    Single SQLite database for ALL system state.
    Replaces: sync_registry.json, researchers_map.json, and scattered text state files.
    """
    def __init__(self):
        self.logger = logging.getLogger("DatabaseManager")
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
            self.logger.setLevel(logging.INFO)
            
        self.db_path = os.path.join(Config.LIBRARY_DIR, "system.db") 
        os.makedirs(Config.LIBRARY_DIR, exist_ok=True)
        self._create_tables()

    def _get_connection(self):
        # Add a short timeout to avoid indefinite locks
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row 
        return conn

    def _ensure_column_exists(self, cursor, table_name: str, column_name: str, column_type: str):
        """
        Safely adds a column to an existing table if it doesn't already exist.
        Prevents crashes during database schema upgrades.
        """
        cursor.execute(f"PRAGMA table_info({table_name})")
        # Extract column names from the PRAGMA result
        columns = [row['name'] for row in cursor.fetchall()]
        if column_name not in columns:
            self.logger.info("Upgrading Database Schema: Adding column '%s' to '%s'.", column_name, table_name)
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def _create_tables(self):
        queries = [
            """
            CREATE TABLE IF NOT EXISTS sync_registry (
                project_name TEXT PRIMARY KEY,
                last_modified_text TEXT,
                last_synced TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS project_state (
                project_name TEXT PRIMARY KEY,
                stanford_status TEXT DEFAULT 'READY_FOR_UPLOAD',
                last_upload_time TEXT,
                last_seen_text TEXT,
                researcher_email TEXT,
                student_status TEXT,
                update_frequency TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                project_name TEXT NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                started_at TEXT,
                finished_at TEXT
            );
            """,
            """
            CREATE TABLE IF NOT EXISTS progress_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT,
                snapshot_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                had_changes BOOLEAN,
                delta_char_count INTEGER,
                FOREIGN KEY(project_name) REFERENCES project_state(project_name)
            );
            """
        ]
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for q in queries:
                    cursor.execute(q)
                
                # --- Dynamic Schema Upgrades (Safe Migrations) ---
                # Adding new columns for Supervisor Status tracking
                self._ensure_column_exists(cursor, "project_state", "supervisor_email", "TEXT")
                self._ensure_column_exists(cursor, "project_state", "student_name", "TEXT")
                self._ensure_column_exists(cursor, "project_state", "created_at", "TEXT")
                self._ensure_column_exists(cursor, "project_state", "stanford_token", "TEXT")
                
                conn.commit()
            self.logger.info("Database tables verified/created successfully.")
        except sqlite3.Error as e:
            self.logger.error("Failed to create/upgrade tables: %s", str(e))

    # ==========================================
    # SYNC REGISTRY METHODS (For DataIngestion)
    # ==========================================
    def get_last_modified(self, project_name: str):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT last_modified_text FROM sync_registry WHERE project_name = ?", (project_name,))
                row = cursor.fetchone()
                return row['last_modified_text'] if row else None
        except sqlite3.Error as e:
            self.logger.error("Failed to get last modified: %s", str(e))
            return None

    def get_project_count(self) -> int:
        """Returns the number of rows in project_state. Used to skip JSON migration after first run."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM project_state")
                return cursor.fetchone()[0]
        except sqlite3.Error as e:
            self.logger.error("Failed to count projects: %s", str(e))
            return 0

    def get_all_last_modified(self) -> dict:
        """Returns {project_name: last_modified_text} for all tracked projects in one query."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT project_name, last_modified_text FROM sync_registry")
                return {row['project_name']: row['last_modified_text'] for row in cursor.fetchall()}
        except sqlite3.Error as e:
            self.logger.error("Failed to get all last modified: %s", str(e))
            return {}

    def update_sync_registry(self, project_name: str, last_modified_text: str):
        # Using SQLite 'UPSERT' (ON CONFLICT) to insert or update seamlessly
        query = """
        INSERT INTO sync_registry (project_name, last_modified_text, last_synced)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(project_name) DO UPDATE SET 
        last_modified_text = excluded.last_modified_text,
        last_synced = CURRENT_TIMESTAMP
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (project_name, last_modified_text))
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Failed to update sync registry: %s", str(e))

    # ==========================================
    # PROGRESS SNAPSHOTS METHODS (For Supervisor Agent)
    # ==========================================
    def add_progress_snapshot(self, project_name: str, had_changes: bool, delta_char_count: int = 0):
        """
        Records a daily/run snapshot of project activity.
        Called by ProgressTrackingAgent even when had_changes is False to track silent days.
        """
        query = """
        INSERT INTO progress_snapshots (project_name, had_changes, delta_char_count)
        VALUES (?, ?, ?)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (project_name, had_changes, delta_char_count))
                conn.commit()
            self.logger.info("Recorded progress snapshot for [%s]: changes=%s, chars=%d", 
                             project_name, had_changes, delta_char_count)
        except sqlite3.Error as e:
            self.logger.error("Failed to add progress snapshot for %s: %s", project_name, str(e))

    # ==========================================
    # PROJECT STATE METHODS (For Core Agents)
    # ==========================================
    def add_project(self, project_name: str, email: str):
        query = """
        INSERT OR IGNORE INTO project_state (project_name, researcher_email)
        VALUES (?, ?)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (project_name, email))
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Failed to add project %s: %s", project_name, str(e))

    def get_project_state(self, project_name: str) -> dict:
        query = "SELECT * FROM project_state WHERE project_name = ?"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (project_name,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            self.logger.error("Failed to fetch project state %s: %s", project_name, str(e))
            return None

    def get_project_state_slim(self, project_name: str) -> dict:
        """Returns project state without last_seen_text — use when the blob is not needed."""
        query = """SELECT project_name, stanford_status, last_upload_time, stanford_token, researcher_email,
                   student_status, update_frequency, supervisor_email, student_name, created_at
                   FROM project_state WHERE project_name = ?"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (project_name,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            self.logger.error("Failed to fetch slim project state %s: %s", project_name, str(e))
            return None

    def update_project_state(self, project_name: str, **kwargs):
        if not kwargs:
            return

        # Whitelist of valid column names to prevent SQL injection
        VALID_COLUMNS = {
            "stanford_status", "last_upload_time", "last_seen_text", "stanford_token",
            "researcher_email", "student_status", "update_frequency",
            "supervisor_email", "student_name", "created_at"
        }

        invalid_keys = set(kwargs.keys()) - VALID_COLUMNS
        if invalid_keys:
            self.logger.error(
                "Attempted to update invalid columns: %s. Aborting update.", invalid_keys
            )
            return

        columns = ", ".join([f"{k} = ?" for k in kwargs.keys()])
        values = list(kwargs.values())
        values.append(project_name)

        query = f"UPDATE project_state SET {columns} WHERE project_name = ?"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, values)
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Failed to update project state: %s", str(e))

    # ==========================================
    # AGENT RUNS METHODS (For Auditing & Dashboard)
    # ==========================================
    def log_agent_run(self, agent_name: str, project_name: str, status: str, error_message: str = "", started_at: str = "", finished_at: str = ""):
        query = """
        INSERT INTO agent_runs (agent_name, project_name, status, error_message, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (agent_name, project_name, status, error_message, started_at, finished_at))
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Failed to log agent run: %s", str(e))

    def get_projects_by_supervisor(self) -> list:
        """
        Returns all projects that have a supervisor assigned.
        Each item is a dict with keys: project_name, supervisor_email,
        student_name, created_at.
        Returns an empty list on error.
        """
        query = """
            SELECT project_name, supervisor_email, student_name, created_at
            FROM project_state
            WHERE supervisor_email IS NOT NULL AND supervisor_email != ''
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            self.logger.error("Failed to get projects by supervisor: %s", str(e))
            return []

    def get_project_snapshots(self, project_name: str, days: int = 28) -> list:
        """
        Returns progress snapshots for a project within the last N days.
        Each item is a dict with keys: snapshot_date, had_changes, delta_char_count.
        Returns an empty list on error.
        """
        query = """
            SELECT snapshot_date, had_changes, delta_char_count
            FROM progress_snapshots
            WHERE project_name = ? AND snapshot_date >= date('now', ?)
            ORDER BY snapshot_date DESC
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (project_name, f'-{days} days'))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            self.logger.error("Failed to get snapshots for %s: %s", project_name, str(e))
            return []

    def get_agent_run_stats(self) -> list:
        """
        Returns per-agent run statistics for dashboard analytics.
        Each row: {agent_name, total, successes, failures, avg_duration_s}
        avg_duration_s is the mean of (finished_at - started_at) in seconds
        for rows where both timestamps are present.
        """
        query = """
            SELECT
                agent_name,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS successes,
                SUM(CASE WHEN status IN ('FAILED', 'ERROR') THEN 1 ELSE 0 END) AS failures,
                AVG(
                    CASE
                        WHEN started_at IS NOT NULL AND started_at != ''
                             AND finished_at IS NOT NULL AND finished_at != ''
                        THEN (julianday(finished_at) - julianday(started_at)) * 86400
                        ELSE NULL
                    END
                ) AS avg_duration_s
            FROM agent_runs
            GROUP BY agent_name
            ORDER BY total DESC
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            self.logger.error("Failed to get agent run stats: %s", str(e))
            return []

    def get_all_project_snapshots(self, days: int = 28) -> list:
        """
        Returns progress snapshots for ALL projects within the last N days.
        Each row: {project_name, snapshot_date, had_changes, delta_char_count}
        Used by the Analytics dashboard page.
        """
        query = """
            SELECT project_name, snapshot_date, had_changes, delta_char_count
            FROM progress_snapshots
            WHERE snapshot_date >= date('now', ?)
            ORDER BY project_name, snapshot_date
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (f'-{days} days',))
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            self.logger.error("Failed to get all project snapshots: %s", str(e))
            return []

    def migrate_from_json(self, json_path: str):
        """
        Safely migrate projects from a legacy researchers_map.json file into the
        `project_state` table. This operation is idempotent.
        """
        if not json_path:
            self.logger.info("No json_path provided to migrate_from_json(). Skipping.")
            return

        if not os.path.exists(json_path):
            self.logger.info("Migration file not found at %s. Nothing to migrate.", json_path)
            return

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            self.logger.error("Failed to read migration JSON %s: %s", json_path, str(e))
            return

        def _process_entry(project_name, val):
            email = None
            if isinstance(val, str):
                email = val
            elif isinstance(val, dict):
                email = val.get('researcher_email') or val.get('ResearcherEmail') or val.get('email')
            elif isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        email = item
                        break
                    if isinstance(item, dict):
                        email = item.get('researcher_email') or item.get('email') or email
                        if email:
                            break

            if not email:
                email = Config.RESEARCHER_EMAIL

            try:
                self.add_project(project_name, email)
            except Exception as e:
                self.logger.error("Failed to add migrated project %s: %s", project_name, str(e))

        try:
            if isinstance(data, dict):
                for proj, val in data.items():
                    _process_entry(proj, val)
            elif isinstance(data, list):
                for record in data:
                    if isinstance(record, dict):
                        proj = record.get('project_name') or record.get('project') or record.get('name')
                        if proj:
                            _process_entry(proj, record)
            else:
                self.logger.info("Migration JSON has unsupported top-level structure. Skipping.")
        except Exception as e:
            self.logger.error("Unexpected error during migration: %s", str(e))