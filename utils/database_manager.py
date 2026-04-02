import os
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
        # Set up a basic logger for the database operations
        self.logger = logging.getLogger("DatabaseManager")
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
            self.logger.setLevel(logging.INFO)
            
        # Define DB path using Config
        self.db_path = os.path.join(Config.LIBRARY_DIR, "research_agents.db")
        
        # Ensure the directory exists before trying to create the DB file
        os.makedirs(Config.LIBRARY_DIR, exist_ok=True)
        
        # Automatically initialize the database tables upon instantiation
        self._create_tables()

    def _get_connection(self):
        """
        Returns a database connection.
        Configures the row_factory to return rows as dictionaries for easier data access.
        """
        conn = sqlite3.connect(self.db_path)
        # This tells SQLite to let us access columns by name (e.g., row['ProjectName'])
        conn.row_factory = sqlite3.Row 
        return conn

    def _create_tables(self):
        """
        Executes the initial DDL (Data Definition Language) to create the schema.
        """
        query = """
        CREATE TABLE IF NOT EXISTS Projects (
            ProjectName TEXT PRIMARY KEY,
            ResearcherEmail TEXT NOT NULL,
            StudentStatus TEXT DEFAULT 'Full-Time',
            UpdateFrequency TEXT DEFAULT 'Daily'
        );
        """
        try:
            # Using 'with' ensures the connection closes automatically when done
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query)
                conn.commit()
            self.logger.info("Database tables verified/created successfully.")
        except sqlite3.Error as e:
            self.logger.error("Failed to create tables: %s", str(e))

    def add_project(self, project_name: str, email: str, status: str = 'Full-Time', frequency: str = 'Daily'):
        """
        Inserts a new project into the database.
        Uses INSERT OR IGNORE to prevent crashing if the Primary Key already exists.
        """
        query = """
        INSERT OR IGNORE INTO Projects (ProjectName, ResearcherEmail, StudentStatus, UpdateFrequency)
        VALUES (?, ?, ?, ?)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # We pass the variables as a tuple to prevent SQL Injection
                cursor.execute(query, (project_name, email, status, frequency))
                conn.commit()
        except sqlite3.Error as e:
            self.logger.error("Failed to add project %s: %s", project_name, str(e))

    def get_project_config(self, project_name: str) -> dict:
        """
        Retrieves the configuration for a specific project.
        Returns a dictionary mapping column names to values.
        """
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