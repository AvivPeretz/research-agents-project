import os
import difflib
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from utils.overleaf_connector import OverleafConnector

class ProgressTrackingAgent(BaseAgent):
    """
    Agent responsible for tracking progress in Overleaf projects.
    It utilizes a 'Delta Memory' system to only analyze newly added or modified text,
    saving LLM tokens and providing highly focused feedback.
    """
    
    def __init__(self, overleaf_projects: list):
        super().__init__(agent_name="ProgressTrackingAgent")
        self.overleaf_projects = overleaf_projects
        self.library = LibraryManager()
        self.connector = OverleafConnector()
        self.logger.info("ProgressTrackingAgent initialized with %d projects.", len(self.overleaf_projects))

    def _get_state_file_path(self, project: str) -> str:
        """Returns the path to the hidden state file that remembers the last seen text."""
        safe_project = project.replace(" ", "_")
        project_dir = os.path.join(self.library.base_path, "project_tracking", safe_project)
        self.library._create_directory(project_dir)
        return os.path.join(project_dir, ".last_seen_text.txt")

    def _get_last_seen_text(self, project: str) -> str:
        """Retrieves the text from the previous run."""
        state_file = self._get_state_file_path(project)
        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def _save_current_text(self, project: str, text: str):
        """Saves the current text to memory for future comparisons."""
        state_file = self._get_state_file_path(project)
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(text)

    def _extract_delta(self, old_text: str, new_text: str) -> str:
        """
        Compares old and new text and extracts ONLY the newly added or modified lines.
        """
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        
        # Calculate the differences
        diff = difflib.ndiff(old_lines, new_lines)
        
        # Keep only the lines that were added (start with '+ ')
        added_lines = [line[2:] for line in diff if line.startswith('+ ')]
        
        # Clean empty lines
        delta_text = "\n".join([line for line in added_lines if line.strip()])
        return delta_text

    def check_text_changes(self, project: str) -> dict:
        """
        Reads the actual text, compares it to memory, and extracts the Delta.
        """
        self.logger.info("Reading text from local Drop Folder for project: %s", project)
        project_path = os.path.join(self.connector.base_storage_path, project)
        
        # 1. Read the real, current text
        current_text = self.connector.read_and_clean_tex_file(project_path, "main.tex")
        
        if not current_text:
            self.logger.warning("No text found for %s.", project)
            return {"has_changes": False, "delta_text": ""}
            
        # 2. Get the old text from memory
        old_text = self._get_last_seen_text(project)
        
        # 3. Extract the Delta (what's new?)
        if not old_text:
            self.logger.info("First time processing %s. The entire text is considered 'new'.", project)
            delta_text = current_text
        else:
            self.logger.info("Comparing current text to memory to extract Delta...")
            delta_text = self._extract_delta(old_text, current_text)
            
        # 4. Save the current text to memory for next time!
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
        response = self.ask_llm(prompt)
        print(f"\n📝 Focused Feedback on new changes:\n{response}\n")
        return response

    def suggest_improvements(self, delta_text: str) -> str:
        self.logger.info("Generating targeted writing suggestions for the Delta...")
        prompt = f"""
        You are an expert academic editor. Review the following NEW ADDITIONS or MODIFICATIONS to a research paper:
        ---\n{delta_text}\n---
        Suggest improvements to elevate the academic phrasing and flow of these specific new sections. 
        Explain *what* should be changed and *why*, but do not rewrite the paragraph.
        Provide 2-3 bullet points of concrete suggestions.
        """
        response = self.ask_llm(prompt)
        print(f"\n💡 Targeted Suggestions on new changes:\n{response}\n")
        return response

    def run(self):
        self.logger.info("Starting the progress tracking cycle.")
        for project in self.overleaf_projects:
            print(f"\n{'-'*40}\n📂 Evaluating Project Updates: {project}\n{'-'*40}")
            changes = self.check_text_changes(project)
            
            if changes.get("has_changes"):
                delta_text = changes.get("delta_text")
                feedback = self.provide_feedback(delta_text)
                suggestions = self.suggest_improvements(delta_text)
                
                self.library.save_feedback(project, feedback, suggestions)
                self.logger.info("Saved focused feedback and suggestions for %s.", project)
            else:
                self.logger.info("No actionable new text found for %s. Skipping LLM analysis.", project)
                
        self.logger.info("Progress tracking cycle completed.")