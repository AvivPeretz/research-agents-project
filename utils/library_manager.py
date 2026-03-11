import os
import json
from datetime import datetime
import pandas as pd

class LibraryManager:
    """
    A utility class responsible for saving the outputs of the agents 
    into strictly organized files and folders based on the Project Name.
    """
    def __init__(self, base_path: str = "research_library"):
        self.base_path = base_path
        self._create_directory(self.base_path)

    def _create_directory(self, path: str):
        """Helper method to create directories if they don't exist."""
        if not os.path.exists(path):
            os.makedirs(path)

    def save_summary(self, project_name: str, content: str):
        """
        Saves the literature review summary as a Markdown file inside the project's folder.
        """
        safe_project_name = project_name.replace(" ", "_")
        project_dir = os.path.join(self.base_path, "literature_reviews", safe_project_name)
        self._create_directory(project_dir)
        
        # Using a clear timestamp and naming convention
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}_literature_summary.md"
        filepath = os.path.join(project_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# Literature Review for: {project_name}\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n\n")
            f.write(content)
            
        return filepath

    def update_comparison_table(self, project_name: str, new_data: dict):
        """
        Updates a rolling CSV comparison table for a specific project.
        """
        safe_project_name = project_name.replace(" ", "_")
        project_dir = os.path.join(self.base_path, "comparison_tables", safe_project_name)
        self._create_directory(project_dir)
        
        # The filename is static so it appends to the same rolling table over time
        filepath = os.path.join(project_dir, "rolling_comparison.csv")
        
        df_new = pd.DataFrame([new_data])
        
        if os.path.exists(filepath):
            # Append to existing rolling table
            df_existing = pd.read_csv(filepath)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(filepath, index=False)
        else:
            # Create a new rolling table
            df_new.to_csv(filepath, index=False)

    def save_feedback(self, project_name: str, feedback: str, suggestions: str):
         """
         Saves the feedback and suggestions for an Overleaf project.
         """
         safe_project_name = project_name.replace(" ", "_")
         project_dir = os.path.join(self.base_path, "project_tracking", safe_project_name)
         self._create_directory(project_dir)
         
         timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
         filepath = os.path.join(project_dir, f"{timestamp}_feedback.md")
         
         with open(filepath, "w", encoding="utf-8") as f:
             f.write(f"# Progress Tracking: {project_name}\n\n")
             f.write(f"## Feedback\n{feedback}\n\n")
             f.write(f"## Suggestions\n{suggestions}\n")

    def save_enhancement_tasks(self, project_name: str, tasks: str, innovation: str):
         """
         Saves the actionable tasks and innovation analysis for a project.
         """
         safe_project_name = project_name.replace(" ", "_")
         project_dir = os.path.join(self.base_path, "project_enhancement", safe_project_name)
         self._create_directory(project_dir)
         
         timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
         filepath = os.path.join(project_dir, f"{timestamp}_enhancement_plan.md")
         
         with open(filepath, "w", encoding="utf-8") as f:
             f.write(f"# Enhancement Plan: {project_name}\n\n")
             f.write(f"## Actionable Tasks\n{tasks}\n\n")
             f.write(f"## Innovation Strategy\n{innovation}\n")