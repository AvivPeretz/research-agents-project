from agents.base_agent import BaseAgent

class ProgressTrackingAgent(BaseAgent):
    """
    Agent responsible for tracking progress in Overleaf projects,
    analyzing text changes, and suggesting writing improvements.
    """
    
    def __init__(self, overleaf_projects: list):
        super().__init__(agent_name="ProgressTrackingAgent")
        self.overleaf_projects = overleaf_projects
        self.logger.info("ProgressTrackingAgent initialized with %d projects.", len(self.overleaf_projects))

    def check_text_changes(self, project: str):
        """
        Check for new text changes in the given Overleaf project.
        """
        self.logger.info("Checking for text changes in project: %s", project)
        # TODO: Implement Overleaf integration to fetch text diffs
        return {"has_changes": True, "diff": "dummy diff data"} 

    def provide_feedback(self, text_diff: str):
        """
        Provide feedback or an opinion on the new text changes.
        """
        self.logger.info("Analyzing changes to provide feedback...")
        # TODO: Send diff to LLM and generate feedback

    def suggest_improvements(self, project: str):
        """
        Review the entire text and suggest writing improvements 
        without changing the original content.
        """
        self.logger.info("Generating writing suggestions for project: %s", project)
        # TODO: Send full text to LLM for writing suggestions

    def run(self):
        """
        Main execution flow for tracking project progress.
        """
        self.logger.info("Starting the progress tracking cycle.")
        
        for project in self.overleaf_projects:
            changes = self.check_text_changes(project)
            
            # If there are changes, provide feedback
            if changes.get("has_changes"):
                self.provide_feedback(changes.get("diff"))
                
            # Suggest general writing improvements
            self.suggest_improvements(project)
                
        self.logger.info("Progress tracking cycle completed.")