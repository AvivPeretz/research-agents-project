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

    def check_text_changes(self, project: str) -> dict:
        """
        Check for new text changes in the given Overleaf project.
        Returns a dictionary with status and dummy text for testing.
        """
        self.logger.info("Checking for text changes in project: %s", project)
        
        # Simulated rough draft text that a researcher might write
        dummy_new_text = (
            "The results of the AI model were very good. We saw that it works fast. "
            "It is better than the old model because it uses less memory and gives right answers."
        )
        return {"has_changes": True, "new_text": dummy_new_text}

    def provide_feedback(self, text: str) -> str:
        """
        Ask the LLM to provide feedback on the newly added text.
        """
        self.logger.info("Analyzing changes to provide feedback...")
        
        # Prompt engineering: Assigning the persona of an academic reviewer
        prompt = f"""
        You are an expert academic reviewer. Review the following draft text added to a research paper:
        ---
        {text}
        ---
        Provide a brief, constructive critique (1 short paragraph) focusing on the academic tone, clarity, and depth. 
        Do not rewrite the text, just evaluate its current state.
        """
        
        response = self.ask_llm(prompt)
        print(f"\n📝 Feedback for new text:\n{response}\n")
        return response

    def suggest_improvements(self, text: str) -> str:
        """
        Ask the LLM to suggest writing improvements without changing the core meaning.
        """
        self.logger.info("Generating writing suggestions...")
        
        # Prompt engineering: Assigning the persona of an academic editor
        prompt = f"""
        You are an expert academic editor. Review the following draft text:
        ---
        {text}
        ---
        Suggest improvements to elevate the academic phrasing, vocabulary, and flow. 
        Explain *what* should be changed and *why*, but do not rewrite the paragraph for them.
        Provide 2-3 bullet points of concrete suggestions.
        """
        
        response = self.ask_llm(prompt)
        print(f"\n💡 Writing Suggestions:\n{response}\n")
        return response

    def run(self):
        """
        Main execution flow for tracking project progress.
        """
        self.logger.info("Starting the progress tracking cycle.")
        
        for project in self.overleaf_projects:
            print(f"\n{'-'*40}")
            print(f"📂 Evaluating Project: {project}")
            print(f"{'-'*40}")
            
            changes = self.check_text_changes(project)
            
            if changes.get("has_changes"):
                new_text = changes.get("new_text")
                self.provide_feedback(new_text)
                self.suggest_improvements(new_text)
                
        self.logger.info("Progress tracking cycle completed.")