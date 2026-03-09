import os
import json
from datetime import datetime
import pandas as pd # Make sure to install pandas

class LibraryManager:
    """
    A utility class responsible for saving the outputs of the agents 
    into organized files and folders.
    """
    def __init__(self, base_path: str = "research_library"):
        self.base_path = base_path
        self._create_directory(self.base_path)

    def _create_directory(self, path: str):
        """Helper method to create directories if they don't exist."""
        if not os.path.exists(path):
            os.makedirs(path)

    def save_summary(self, topic: str, content: str):
        """
        Saves the literature review summary as a Markdown file.
        """
        topic_dir = os.path.join(self.base_path, "literature_reviews", topic.replace(" ", "_"))
        self._create_directory(topic_dir)
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}_summary.md"
        filepath = os.path.join(topic_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# Research Summary: {topic}\n\n")
            f.write(f"**Date:** {datetime.now().strftime('%Y-%m-%d')}\n\n")
            f.write(content)
            
        return filepath

    def update_comparison_table(self, topic: str, new_data: dict):
        """
        Updates a CSV file acting as a rolling comparison table for a topic.
        """
        topic_dir = os.path.join(self.base_path, "comparison_tables")
        self._create_directory(topic_dir)
        
        filepath = os.path.join(topic_dir, f"{topic.replace(' ', '_')}_comparison.csv")
        
        df_new = pd.DataFrame([new_data])
        
        if os.path.exists(filepath):
            # Append to existing
            df_existing = pd.read_csv(filepath)
            df_combined = pd.concat([df_existing, df_new], ignore_index=True)
            df_combined.to_csv(filepath, index=False)
        else:
            # Create new
            df_new.to_csv(filepath, index=False)

    def save_feedback(self, project: str, feedback: str, suggestions: str):
         """
         Saves the feedback and suggestions for an Overleaf project.
         """
         project_dir = os.path.join(self.base_path, "project_tracking", project)
         self._create_directory(project_dir)
         
         timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
         filepath = os.path.join(project_dir, f"{timestamp}_feedback.md")
         
         with open(filepath, "w", encoding="utf-8") as f:
             f.write(f"# Progress Tracking: {project}\n\n")
             f.write(f"## Feedback\n{feedback}\n\n")
             f.write(f"## Suggestions\n{suggestions}\n")

    def save_enhancement_tasks(self, project: str, tasks: str, innovation: str):
         """
         Saves the actionable tasks and innovation analysis for a project.
         """
         project_dir = os.path.join(self.base_path, "project_enhancement", project)
         self._create_directory(project_dir)
         
         timestamp = datetime.now().strftime("%Y-%m-%d")
         filepath = os.path.join(project_dir, f"{timestamp}_enhancement_plan.md")
         
         with open(filepath, "w", encoding="utf-8") as f:
             f.write(f"# Enhancement Plan: {project}\n\n")
             f.write(f"## Actionable Tasks\n{tasks}\n\n")
             f.write(f"## Innovation Strategy\n{innovation}\n")