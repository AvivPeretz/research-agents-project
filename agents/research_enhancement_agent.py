from agents.base_agent import BaseAgent

class ResearchEnhancementAgent(BaseAgent):
    """
    Agent responsible for periodic research enhancement.
    Simulates external article review, extracts action items, and tracks innovation.
    """
    
    def __init__(self, overleaf_projects: list):
        super().__init__(agent_name="ResearchEnhancementAgent")
        self.overleaf_projects = overleaf_projects
        self.logger.info("ResearchEnhancementAgent initialized with %d projects.", len(self.overleaf_projects))

    def export_to_pdf(self, project: str) -> str:
        """
        Simulate saving the current state of the Overleaf project as a PDF file.
        """
        self.logger.info("Exporting project to PDF: %s", project)
        # Placeholder: In the future, connect to Overleaf API to download the actual PDF.
        return f"{project}_draft.pdf"

    def send_for_review(self, pdf_path: str) -> str:
        """
        Simulate sending the PDF to an external article review website and receiving a critique.
        """
        self.logger.info("Simulating external review for %s...", pdf_path)
        
        # Simulated review received from an external academic source
        dummy_review = (
            "The paper presents an interesting approach to Autonomous AI agents. "
            "However, the methodology section lacks detail on the specific LLM parameters used. "
            "Furthermore, the literature review misses several key papers from 2025 regarding "
            "multi-agent reinforcement learning. The results are promising, but statistical "
            "significance tests (e.g., p-values) are missing. Overall innovation is moderate, "
            "building mostly on existing RAG (Retrieval-Augmented Generation) architectures."
        )
        return dummy_review

    def extract_tasks(self, review_text: str) -> str:
        """
        Use the LLM to extract actionable tasks from the received article review.
        """
        self.logger.info("Extracting tasks from the review data...")
        
        prompt = f"""
        You are a rigorous academic project manager. Read the following external peer-review of our paper:
        ---
        {review_text}
        ---
        Extract a clear, actionable To-Do list (bullet points) for the research team so they can address the reviewer's concerns.
        """
        
        response = self.ask_llm(prompt)
        print(f"\n📋 Actionable Tasks Extracted:\n{response}\n")
        return response

    def compare_innovation(self, review_text: str) -> str:
        """
        Use the LLM to analyze the innovation level based on the review and suggest improvements.
        """
        self.logger.info("Analyzing innovation metrics...")
        
        prompt = f"""
        Based on the following paper review, provide a brief summary of the paper's current innovation level 
        and provide ONE concrete, ambitious suggestion on how to push the boundaries of this research 
        to make it highly groundbreaking.
        ---
        {review_text}
        ---
        """
        
        response = self.ask_llm(prompt)
        print(f"\n🚀 Innovation Analysis & Strategy:\n{response}\n")
        return response

    def run(self):
        """
        Main execution flow for research enhancement.
        """
        self.logger.info("Starting the research enhancement cycle.")
        
        for project in self.overleaf_projects:
            print(f"\n{'='*40}")
            print(f"🔬 Enhancing Project: {project}")
            print(f"{'='*40}")
            
            # 1. Export and Review
            pdf_path = self.export_to_pdf(project)
            review_text = self.send_for_review(pdf_path)
            
            # 2. Process the review with LLM
            self.extract_tasks(review_text)
            self.compare_innovation(review_text)
            
        self.logger.info("Research enhancement cycle completed.")