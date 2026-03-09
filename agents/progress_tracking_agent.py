import os
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from utils.overleaf_connector import OverleafConnector

class ProgressTrackingAgent(BaseAgent):
    """
    Agent responsible for tracking progress in Overleaf projects,
    analyzing text changes, and suggesting writing improvements.
    """
    
    def __init__(self, overleaf_projects: list):
        super().__init__(agent_name="ProgressTrackingAgent")
        self.overleaf_projects = overleaf_projects
        self.library = LibraryManager()
        self.connector = OverleafConnector()
        self.logger.info("ProgressTrackingAgent initialized with %d projects.", len(self.overleaf_projects))

    def check_text_changes(self, project: str) -> dict:
        """
        Reads the actual text from the local Drop Folder.
        """
        self.logger.info("Reading text from local Drop Folder for project: %s", project)
        project_path = os.path.join(self.connector.base_storage_path, project)
        
        # Read the real text using our connector
        real_text = self.connector.read_and_clean_tex_file(project_path, "main.tex")
        
        if not real_text:
            self.logger.warning("No text found for %s. Make sure 'main.tex' is in the folder.", project)
            return {"has_changes": False, "new_text": ""}
            
        return {"has_changes": True, "new_text": real_text}

    def provide_feedback(self, text: str) -> str:
        self.logger.info("Analyzing changes to provide feedback...")
        prompt = f"""
        You are an expert academic reviewer. Review the following draft text added to a research paper:
        ---\n{text}\n---
        Provide a brief, constructive critique focusing on the academic tone, clarity, and depth. 
        Do not rewrite the text, just evaluate its current state.
        """
        response = self.ask_llm(prompt)
        print(f"\n📝 Feedback:\n{response}\n")
        return response

    def suggest_improvements(self, text: str) -> str:
        self.logger.info("Generating writing suggestions...")
        prompt = f"""
        You are an expert academic editor. Review the following draft text:
        ---\n{text}\n---
        Suggest improvements to elevate the academic phrasing and flow. 
        Explain *what* should be changed and *why*, but do not rewrite the paragraph.
        Provide 2-3 bullet points of concrete suggestions.
        """
        response = self.ask_llm(prompt)
        print(f"\n💡 Suggestions:\n{response}\n")
        return response

    def run(self):
        self.logger.info("Starting the progress tracking cycle.")
        for project in self.overleaf_projects:
            print(f"\n{'-'*40}\n📂 Evaluating Project: {project}\n{'-'*40}")
            changes = self.check_text_changes(project)
            
            if changes.get("has_changes"):
                new_text = changes.get("new_text")
                feedback = self.provide_feedback(new_text)
                suggestions = self.suggest_improvements(new_text)
                
                self.library.save_feedback(project, feedback, suggestions)
                self.logger.info("Saved feedback and suggestions for %s.", project)
                
        self.logger.info("Progress tracking cycle completed.")