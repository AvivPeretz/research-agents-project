import os
import logging
import pandas as pd
from datetime import datetime

class LibraryManager:
    """
    Manages the local file system (I/O) for the multi-agent system.
    Creates structured directories and saves structured CSVs per project.
    """
    def __init__(self, base_dir="research_library"):
        self.base_dir = os.path.abspath(base_dir)
        self.logger = logging.getLogger("LibraryManager")
        self._ensure_base_dirs()

    def _ensure_base_dirs(self):
        """Creates the main library directories if they do not exist."""
        # Added 'comparison_tables' to the architecture!
        folders = ["literature_reviews", "project_tracking", "project_enhancement", "comparison_tables"]
        for folder in folders:
            path = os.path.join(self.base_dir, folder)
            if not os.path.exists(path):
                os.makedirs(path)

    def _get_project_dir(self, category: str, project_name: str) -> str:
        """Returns and ensures a project-specific subfolder exists."""
        safe_name = project_name.replace(" ", "_")
        path = os.path.join(self.base_dir, category, safe_name)
        if not os.path.exists(path):
            os.makedirs(path)
        return path

    def _save_markdown(self, category: str, project_name: str, filename_prefix: str, content: str):
        """Helper to save markdown files with a timestamp."""
        project_dir = self._get_project_dir(category, project_name)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}_{filename_prefix}.md"
        filepath = os.path.join(project_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return filepath

    def save_literature_summary(self, project_name: str, summary: str):
        return self._save_markdown("literature_reviews", project_name, "literature_summary", summary)

    def save_tracking_feedback(self, project_name: str, feedback: str, suggestions: str):
        content = f"# Feedback\n{feedback}\n\n# Suggestions\n{suggestions}"
        return self._save_markdown("project_tracking", project_name, "tracking_feedback", content)

    def save_enhancement_tasks(self, project_name: str, tasks: str, innovation: str):
        content = f"# Actionable Tasks\n{tasks}\n\n# Innovation Analysis\n{innovation}"
        return self._save_markdown("project_enhancement", project_name, "enhancement_plan", content)

    def append_to_project_literature_table(self, project_name: str, paper_data: dict):
        """
        Appends a new paper's details to the project-specific rolling CSV table.
        Saves it inside the dedicated 'comparison_tables' folder.
        """
        # Changed the target folder from 'literature_reviews' to 'comparison_tables'
        project_dir = self._get_project_dir("comparison_tables", project_name)
        safe_name = project_name.replace(" ", "_")
        csv_path = os.path.join(project_dir, f"{safe_name}_rolling_table.csv")

        columns = [
            "paper name", "cited", "source", "year published", 
            "types of available data", "number of samples", "number of features", 
            "number of classes", "location", "for how long", 
            "is it reproducible?", "how complicated it is?", 
            "is there privacy issues?", "can i control the application collected"
        ]

        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
        else:
            df = pd.DataFrame(columns=columns)

        new_row = {col: paper_data.get(col, "N/A") for col in columns}

        if not df.empty and "paper name" in df.columns:
            new_paper_name = new_row.get("paper name", "N/A")
            if any(df["paper name"].fillna("").str.lower() == new_paper_name.lower()):
                self.logger.info("Paper '%s' already exists in the table. Skipping.", new_paper_name)
                return

        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        df.to_csv(csv_path, index=False)