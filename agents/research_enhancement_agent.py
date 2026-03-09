import os
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from utils.overleaf_connector import OverleafConnector

class ResearchEnhancementAgent(BaseAgent):
    """
    Agent responsible for periodic research enhancement.
    Generates a simulated peer-review based on REAL text, extracts action items, and tracks innovation.
    """
    
    def __init__(self, overleaf_projects: list):
        super().__init__(agent_name="ResearchEnhancementAgent")
        self.overleaf_projects = overleaf_projects
        self.library = LibraryManager()
        self.connector = OverleafConnector()
        self.logger.info("ResearchEnhancementAgent initialized with %d projects.", len(self.overleaf_projects))

    def get_project_text(self, project: str) -> str:
        """Reads the full cleaned text from the project folder."""
        self.logger.info("Reading project text for peer review: %s", project)
        project_path = os.path.join(self.connector.base_storage_path, project)
        return self.connector.read_and_clean_tex_file(project_path, "main.tex")

    def send_for_review(self, text: str) -> str:
        """Uses the LLM to generate a harsh peer-review based on the actual manuscript text."""
        self.logger.info("Simulating external peer review via LLM...")
        prompt = f"""
        You are a strict and rigorous academic peer reviewer for a top-tier journal. 
        Read the following manuscript draft and provide a harsh but constructive peer review (1-2 paragraphs).
        Highlight missing elements, methodological flaws, lack of citations, or lack of depth.
        ---\n{text}\n---
        """
        response = self.ask_llm(prompt)
        print(f"\n📨 Simulated Peer Review:\n{response}\n")
        return response

    def extract_tasks(self, review_text: str) -> str:
        self.logger.info("Extracting tasks from the review data...")
        prompt = f"""
        You are a rigorous academic project manager. Read the following peer-review:
        ---\n{review_text}\n---
        Extract a clear, actionable To-Do list (bullet points) for the research team to fix the paper.
        """
        response = self.ask_llm(prompt)
        print(f"\n📋 Actionable Tasks:\n{response}\n")
        return response

    def compare_innovation(self, review_text: str) -> str:
        self.logger.info("Analyzing innovation metrics...")
        prompt = f"""
        Based on the following paper review, provide a brief summary of the paper's current innovation level 
        and provide ONE concrete, ambitious suggestion on how to push the boundaries of this research.
        ---\n{review_text}\n---
        """
        response = self.ask_llm(prompt)
        print(f"\n🚀 Innovation Analysis:\n{response}\n")
        return response

    def run(self):
        self.logger.info("Starting the research enhancement cycle.")
        for project in self.overleaf_projects:
            print(f"\n{'='*40}\n🔬 Enhancing Project: {project}\n{'='*40}")
            
            text = self.get_project_text(project)
            if not text:
                continue
                
            review_text = self.send_for_review(text)
            tasks = self.extract_tasks(review_text)
            innovation = self.compare_innovation(review_text)
            
            self.library.save_enhancement_tasks(project, tasks, innovation)
            self.logger.info("Saved enhancement plan for %s.", project)
            
        self.logger.info("Research enhancement cycle completed.")