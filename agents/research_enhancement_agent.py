from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager

class ResearchEnhancementAgent(BaseAgent):
    """
    Agent responsible for periodic research enhancement.
    Simulates external article review, extracts action items, and tracks innovation.
    """
    
    def __init__(self, overleaf_projects: list):
        super().__init__(agent_name="ResearchEnhancementAgent")
        self.overleaf_projects = overleaf_projects
        self.library = LibraryManager()  # Instantiate the library manager
        self.logger.info("ResearchEnhancementAgent initialized with %d projects.", len(self.overleaf_projects))

    def export_to_pdf(self, project: str) -> str:
        self.logger.info("Exporting project to PDF: %s", project)
        return f"{project}_draft.pdf"

    def send_for_review(self, pdf_path: str) -> str:
        self.logger.info("Simulating external review for %s...", pdf_path)
        dummy_review = (
            "The paper presents an interesting approach to Autonomous AI agents. "
            "However, the methodology section lacks detail on the specific LLM parameters used. "
            "Furthermore, the literature review misses several key papers from 2025 regarding "
            "multi-agent reinforcement learning. The results are promising, but statistical "
            "significance tests are missing. Overall innovation is moderate."
        )
        return dummy_review

    def extract_tasks(self, review_text: str) -> str:
        self.logger.info("Extracting tasks from the review data...")
        prompt = f"""
        You are a rigorous academic project manager. Read the following external peer-review:
        ---\n{review_text}\n---
        Extract a clear, actionable To-Do list (bullet points) for the research team.
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
        """Main execution flow for research enhancement."""
        self.logger.info("Starting the research enhancement cycle.")
        for project in self.overleaf_projects:
            print(f"\n{'='*40}\n🔬 Enhancing Project: {project}\n{'='*40}")
            pdf_path = self.export_to_pdf(project)
            review_text = self.send_for_review(pdf_path)
            
            tasks = self.extract_tasks(review_text)
            innovation = self.compare_innovation(review_text)
            
            # Save the action items and innovation analysis to a file
            self.library.save_enhancement_tasks(project, tasks, innovation)
            self.logger.info("Saved enhancement plan for %s.", project)
            
        self.logger.info("Research enhancement cycle completed.")