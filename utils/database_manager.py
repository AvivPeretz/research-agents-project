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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row 
        return conn

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
            """
        ]
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for q in queries:
                    cursor.execute(q)
                conn.commit()
            self.logger.info("Database tables verified/created successfully.")
        except sqlite3.Error as e:
            self.logger.error("Failed to create tables: %s", str(e))

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

    def update_project_state(self, project_name: str, **kwargs):
        """
        Dynamically updates any column in the project_state table.
        Example: db.update_project_state("Project A", last_seen_text="New text", stanford_status="REVIEWED")
        """
        if not kwargs:
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