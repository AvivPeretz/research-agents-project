import os
import difflib
from datetime import datetime

# Import the centralized configuration
from config import Config
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from utils.overleaf_connector import OverleafConnector
from agents.notification_agent import NotificationAgent

class ProgressTrackingAgent(BaseAgent):
    """
    Agent responsible for tracking progress in Overleaf projects.
    It utilizes a 'Delta Memory' system managed via SQLite to only analyze 
    newly added or modified text, saving LLM tokens.
    """
    
    def __init__(self, overleaf_projects: list, notifier: NotificationAgent, db=None):
        super().__init__(agent_name="ProgressTrackingAgent")
        self.overleaf_projects = overleaf_projects
        self.library = LibraryManager()
        self.connector = OverleafConnector()
        self.notifier = notifier 
        
        # Dependency Injection for Database
        self.db = db 
        
        self.logger.info("ProgressTrackingAgent initialized with %d projects.", len(self.overleaf_projects))

    def _get_last_seen_text(self, project: str) -> str:
        """Retrieves the text from the previous run directly from SQLite."""
        if not self.db:
            self.logger.warning("No DB connection. Cannot retrieve last seen text for %s.", project)
            return ""
            
        try:
            state = self.db.get_project_state(project)
            if state and state.get('last_seen_text'):
                return state['last_seen_text']
        except Exception as e:
            self.logger.error("Failed to fetch last seen text from DB for %s: %s", project, str(e))
            
        return ""

    def _save_current_text(self, project: str, text: str):
        """Saves the current text to the SQLite database for future comparisons."""
        if not self.db:
            self.logger.warning("No DB connection. Cannot save current text for %s.", project)
            return
            
        try:
            self.db.update_project_state(project, last_seen_text=text)
        except Exception as e:
            self.logger.error("Failed to save current text state to DB for %s: %s", project, str(e))

    def _extract_delta(self, old_text: str, new_text: str) -> str:
        """
        Compares old and new text and extracts ONLY the newly added or modified lines.
        """
        if not old_text.strip() and new_text.strip():
            return new_text
            
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        
        diff = difflib.ndiff(old_lines, new_lines)
        
        added_lines = [line[2:] for line in diff if line.startswith('+ ')]
        
        delta_text = "\n".join([line for line in added_lines if line.strip()])
        return delta_text

    def check_text_changes(self, project: str) -> dict:
        """
        Reads the actual text, compares it to DB memory, and extracts the Delta.
        """
        self.logger.info("Reading text from local Drop Folder for project: %s", project)
        
        project_path = os.path.join(self.connector.base_storage_path, project)
        current_text = self.connector.read_and_clean_tex_file(project_path, "main.tex")
        
        if not current_text:
            self.logger.warning("No text found for %s.", project)
            return {"has_changes": False, "delta_text": ""}
            
        old_text = self._get_last_seen_text(project)
        
        if not old_text:
            self.logger.info("First time processing %s. The entire text is considered 'new'.", project)
            delta_text = current_text
        else:
            self.logger.info("Comparing current text to DB memory to extract Delta...")
            delta_text = self._extract_delta(old_text, current_text)
            
        self._save_current_text(project, current_text)
            
        if not delta_text.strip():
            self.logger.info("Text was modified, but no meaningful new additions were found.")
            return {"has_changes": False, "delta_text": ""}
            
        return {"has_changes": True, "delta_text": delta_text}

    def provide_feedback(self, delta_text: str) -> str:
        self.logger.info("Analyzing Delta text to provide focused feedback...")
        prompt = f"""
        You are an expert academic reviewer. Review the following NEW ADDITIONS or MODIFICATIONS to a research paper:
        ---\n{delta_text}\n---
        Provide a brief, constructive critique focusing ONLY on these new changes regarding their academic tone, clarity, and depth. 
        Do not rewrite the text, just evaluate its current state.
        """
        try:
            response = self.ask_llm(prompt)
            print(f"\n📝 Focused Feedback on new changes:\n{response}\n")
            return response
        except RuntimeError as e:
            self.logger.error("LLM failed to generate feedback: %s", str(e))
            return "⚠️ *System Note: The AI assistant was unable to generate feedback at this time due to a temporary connection issue.*"

    def suggest_improvements(self, delta_text: str) -> str:
        self.logger.info("Generating targeted writing suggestions for the Delta...")
        prompt = f"""
        You are an expert academic editor. Review the following NEW ADDITIONS or MODIFICATIONS to a research paper:
        ---\n{delta_text}\n---
        Suggest improvements to elevate the academic phrasing and flow of these specific new sections. 
        Explain *what* should be changed and *why*, but do not rewrite the paragraph.
        Provide 2-3 bullet points of concrete suggestions.
        """
        try:
            response = self.ask_llm(prompt)
            print(f"\n💡 Targeted Suggestions on new changes:\n{response}\n")
            return response
        except RuntimeError as e:
            self.logger.error("LLM failed to generate suggestions: %s", str(e))
            return "⚠️ *System Note: The AI assistant was unable to generate suggestions at this time due to a temporary connection issue.*"

    def run(self):
        self.logger.info("Starting the progress tracking cycle.")
        for project in self.overleaf_projects:
            if self.db:
                self.db.log_agent_run(
                    agent_name=self.agent_name,
                    project_name=project,
                    status="STARTED",
                    started_at=datetime.now().isoformat()
                )
            print(f"\n{'-'*40}\n📂 Evaluating Project Updates: {project}\n{'-'*40}")

            old_text_before_run = self._get_last_seen_text(project)
            is_first_run = not old_text_before_run or old_text_before_run.strip() == ""

            changes = self.check_text_changes(project)
            has_changes = changes.get("has_changes", False)
            delta_text = changes.get("delta_text", "")

            # Record snapshot regardless of changes
            if self.db:
                delta_char_count = len(delta_text) if has_changes else 0
                self.db.add_progress_snapshot(
                    project_name=project,
                    had_changes=has_changes,
                    delta_char_count=delta_char_count
                )

            if has_changes:
                old_text = self._get_last_seen_text(project)
                is_first_run = not old_text or old_text.strip() == ""

                if is_first_run:
                    self.logger.info(
                        "First run for '%s' — baseline established. Skipping feedback email.", project
                    )
                    if self.db:
                        self.db.log_agent_run(
                            agent_name=self.agent_name,
                            project_name=project,
                            status="SUCCESS",
                            finished_at=datetime.now().isoformat()
                        )
                    continue

                if len(delta_text.strip()) < 50:
                    self.logger.info("Delta too small to process (<50 chars). Skipping feedback.")
                    if self.db:
                        self.db.log_agent_run(
                            agent_name=self.agent_name,
                            project_name=project,
                            status="SKIPPED",
                            finished_at=datetime.now().isoformat()
                        )
                    continue

                feedback = self.provide_feedback(delta_text)
                suggestions = self.suggest_improvements(delta_text)

                self.library.save_tracking_feedback(project, feedback, suggestions)
                self.logger.info("Saved focused feedback and suggestions for %s.", project)

                combined_md = f"### 📝 Progress Feedback\n{feedback}\n\n### 💡 Targeted Suggestions\n{suggestions}"
                self.logger.info("Sending progress feedback email for %s...", project)
                self.notifier.send_progress_feedback(
                    project_name=project,
                    md_content=combined_md
                )
            else:
                self.logger.info("No actionable new text found for %s. Skipping LLM analysis.", project)

            if self.db:
                self.db.log_agent_run(
                    agent_name=self.agent_name,
                    project_name=project,
                    status="SUCCESS",
                    finished_at=datetime.now().isoformat()
                )

        self.logger.info("Progress tracking cycle completed.")