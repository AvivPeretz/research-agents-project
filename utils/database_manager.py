import os
import json
import sqlite3
import logging

# Import the centralized configuration
from config import Config

class DatabaseManager:
    """
    Manages the SQLite database for the Academic Research Multi-Agent System.
    Handles project configurations, student statuses, and notification frequencies.
    """
    def __init__(self):
        self.logger = logging.getLogger("DatabaseManager")
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
            self.logger.setLevel(logging.INFO)
            
        self.db_path = os.path.join(Config.LIBRARY_DIR, "research_agents.db")
        os.makedirs(Config.LIBRARY_DIR, exist_ok=True)
        self._create_tables()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row 
        return conn

    def _create_tables(self):
        """
        Creates the schema. Note: StudentStatus and UpdateFrequency have NO defaults, 
        meaning they will inherently be NULL until updated via the future dashboard.
        """
        query = """
        CREATE TABLE IF NOT EXISTS Projects (
            ProjectName TEXT PRIMARY KEY,
            ResearcherEmail TEXT NOT NULL,
            StudentStatus TEXT,
            UpdateFrequency TEXT
        );
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                conn.commit()
            self.logger.info("Database tables verified/created successfully.")
        except sqlite3.Error as e:
            self.logger.error("Failed to create tables: %s", str(e))

    def add_project(self, project_name: str, email: str, status: str = None, frequency: str = None):
        """
        Inserts a new project. Uses INSERT OR IGNORE to prevent crashes on duplicates.
        """
        query = """
        INSERT OR IGNORE INTO Projects (ProjectName, ResearcherEmail, StudentStatus, UpdateFrequency)
        VALUES (?, ?, ?, ?)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (project_name, email, status, frequency))
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Failed to add project %s: %s", project_name, str(e))

    def get_project_config(self, project_name: str) -> dict:
        query = "SELECT * FROM Projects WHERE ProjectName = ?"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, (project_name,))
                row = cursor.fetchone()
                if row:
                    return dict(row)
                return None
        except sqlite3.Error as e:
            self.logger.error("Failed to fetch project %s: %s", project_name, str(e))
            return None

    def migrate_from_json(self, json_path: str):
        """
        One-time operation: Reads the old researchers_map.json and populates the DB.
        Only inserts ProjectName and ResearcherEmail. Status and Frequency remain NULL.
        """
        if not os.path.exists(json_path):
            return

        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                old_data = json.load(f)

            self.logger.info("Starting migration from JSON to SQLite...")
            for project_name, email in old_data.items():
                self.add_project(project_name, email)
                
            self.logger.info("Migration completed. The SQL Database is now populated.")
        except Exception as e:
            self.logger.error("Migration failed: %s", str(e))