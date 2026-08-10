import os
import re
import difflib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

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

    @staticmethod
    def _normalize_lines(text: str) -> list:
        """Collapse internal whitespace per line so LaTeX reflow doesn't trigger false deltas."""
        return [re.sub(r'\s+', ' ', l).strip() for l in text.splitlines() if l.strip()]

    def _extract_delta(self, old_text: str, new_text: str) -> str:
        """Compares old and new text and extracts ONLY the newly added or modified lines."""
        if not old_text.strip() and new_text.strip():
            return new_text

        old_lines = self._normalize_lines(old_text)
        new_lines = self._normalize_lines(new_text)

        diff = difflib.ndiff(old_lines, new_lines)
        added_lines = [line[2:] for line in diff if line.startswith('+ ')]
        return "\n".join(added_lines)

    def check_text_changes(self, project: str) -> dict:
        """
        Reads the actual text, compares it to DB memory, and extracts the Delta.
        """
        self.logger.info("Reading text from local Drop Folder for project: %s", project)
        
        project_path = os.path.join(self.connector.base_storage_path, project)
        current_text = self.connector.read_all_tex_files(project_path)
        
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

    def analyze_delta(self, delta_text: str) -> tuple:
        """Single LLM call returning (feedback, suggestions) — halves token usage vs two separate calls."""
        self.logger.info("Analyzing Delta (single LLM call for feedback + suggestions)...")
        prompt = f"""
        You are an expert academic reviewer and editor. Review the following NEW ADDITIONS or MODIFICATIONS to a research paper:
        ---\n{delta_text}\n---

        Provide your response in EXACTLY this format (keep the headers):

        ### FEEDBACK
        A brief, constructive critique of these new changes regarding academic tone, clarity, and depth. Do not rewrite the text.

        ### SUGGESTIONS
        2-3 bullet points suggesting concrete improvements to phrasing and flow. Explain what should be changed and why, but do not rewrite.
        """
        _error = "⚠️ *System Note: The AI assistant was unable to generate feedback at this time due to a temporary connection issue.*"
        try:
            response = self.ask_llm(prompt)
            parts = response.split("### SUGGESTIONS", 1)
            if len(parts) == 2:
                feedback = parts[0].replace("### FEEDBACK", "").strip()
                suggestions = parts[1].strip()
            else:
                feedback = response.strip()
                suggestions = ""
            print(f"\n📝 Focused Feedback on new changes:\n{feedback}\n")
            print(f"\n💡 Targeted Suggestions on new changes:\n{suggestions}\n")
            return feedback, suggestions
        except RuntimeError as e:
            self.logger.error("LLM failed to generate analysis: %s", str(e))
            return _error, _error

    def _get_writing_velocity(self, project: str, days: int = 7) -> str:
        """
        Returns a human-readable velocity string: chars/day over the last N days.
        Returns 'N/A' if insufficient data or no DB.
        """
        if not self.db:
            return "N/A"
        try:
            snapshots = self.db.get_project_snapshots(project, days=days)
            active = [s for s in snapshots if s.get('had_changes')]
            if not active:
                return "0 chars/day"
            total_chars = sum(s.get('delta_char_count', 0) for s in active)
            velocity = total_chars / days
            return f"{velocity:.0f} chars/day"
        except Exception as e:
            self.logger.warning("Could not compute velocity for %s: %s", project, str(e))
            return "N/A"

    def _process_project(self, project: str):
        """Per-project logic extracted so ThreadPoolExecutor can run projects in parallel."""
        if self.db:
            self.db.log_agent_run(
                agent_name=self.agent_name,
                project_name=project,
                status="STARTED",
                started_at=datetime.now().isoformat()
            )
        print(f"\n{'-'*40}\n📂 Evaluating Project Updates: {project}\n{'-'*40}")
        velocity = self._get_writing_velocity(project)
        self.logger.info("Writing velocity for '%s' (last 7 days): %s", project, velocity)

        old_text_before_run = self._get_last_seen_text(project)

        changes = self.check_text_changes(project)
        has_changes = changes.get("has_changes", False)
        delta_text = changes.get("delta_text", "")

        if self.db:
            delta_char_count = len(delta_text) if has_changes else 0
            self.db.add_progress_snapshot(
                project_name=project,
                had_changes=has_changes,
                delta_char_count=delta_char_count
            )

        if has_changes:
            is_first_run = not old_text_before_run or old_text_before_run.strip() == ""

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
                return

            min_delta = getattr(Config, 'MIN_DELTA_CHARS', 50)
            if len(delta_text.strip()) < min_delta:
                self.logger.info("Delta too small to process (<%d chars). Skipping feedback.", min_delta)
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=project,
                        status="SUCCESS",
                        finished_at=datetime.now().isoformat()
                    )
                return

            # Groq on_demand tier: 12k TPM. Single merged call uses ~4k tokens max.
            MAX_DELTA_CHARS = getattr(Config, 'MAX_DELTA_CHARS', 8000)
            if len(delta_text) > MAX_DELTA_CHARS:
                self.logger.info(
                    "Delta too large (%d chars). Truncating to %d chars for LLM.",
                    len(delta_text), MAX_DELTA_CHARS
                )
                delta_text = delta_text[:MAX_DELTA_CHARS] + "\n\n[... truncated ...]"

            feedback, suggestions = self.analyze_delta(delta_text)

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

    def run(self):
        self.logger.info("Starting the progress tracking cycle.")
        max_workers = getattr(Config, 'PROGRESS_MAX_WORKERS', 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._process_project, p): p for p in self.overleaf_projects}
            for future in as_completed(futures):
                project = futures[future]
                try:
                    future.result()
                except Exception as e:
                    self.logger.error("Unhandled error processing project '%s': %s", project, str(e))
                    if self.db:
                        self.db.log_agent_run(
                            agent_name=self.agent_name,
                            project_name=project,
                            status="FAILURE",
                            error_message=str(e),
                            finished_at=datetime.now().isoformat()
                        )
                    if self.notifier:
                        try:
                            self.notifier.send_admin_alert(
                                subject=f"ProgressTrackingAgent — Project Failed: {project}",
                                message=(
                                    f"Unhandled error while processing '{project}':\n\n{e}\n\n"
                                    f"See ProgressTrackingAgent.log for the full traceback."
                                )
                            )
                        except Exception:
                            pass  # do not let alert failure mask the original error
        self.logger.info("Progress tracking cycle completed.")