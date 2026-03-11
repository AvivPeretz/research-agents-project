import datetime
import json
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager

class LiteratureResearchAgent(BaseAgent):
    """
    Agent responsible for conducting literature reviews based on downloaded projects.
    It infers keywords, simulates searching academic databases, and maintains a global rolling table.
    """
    
    def __init__(self, research_topics: list):
        # Note: 'research_topics' is actually the list of project names passed from main.py
        super().__init__(agent_name="LiteratureResearchAgent")
        self.projects = research_topics 
        self.library = LibraryManager()
        
        # --- INFRASTRUCTURE PREPARATION ---
        # Future URLs for scraping actual academic official papers
        self.academic_databases = [
            "https://arxiv.org/",
            "https://pubmed.ncbi.nlm.nih.gov/",
            "https://scholar.google.com/"
        ]
        
        self.logger.info("LiteratureResearchAgent initialized with %d projects.", len(self.projects))

    def extract_project_metadata(self, project_name: str) -> dict:
        """
        Uses the LLM to infer the exact research field and search keywords from the project name.
        """
        self.logger.info("Extracting research field and keywords for: %s", project_name)
        prompt = f"""
        Analyze the following academic project name: '{project_name}'.
        Provide a valid JSON response with two keys:
        1. "field": The broad academic research field.
        2. "keywords": A short list of 3-4 specific keywords for searching academic databases.
        
        Respond strictly with JSON. Example:
        {{"field": "Computer Science", "keywords": ["Machine Learning", "Neural Networks"]}}
        """
        response_text = self.ask_llm(prompt)
        
        # Clean the response in case the LLM wrapped it in markdown code blocks
        clean_json = response_text.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean_json)
        except Exception as e:
            self.logger.error(f"Failed to parse metadata JSON for {project_name}: {e}")
            return {"field": "General Research", "keywords": [project_name]}

    def search_new_articles(self, project_name: str, metadata: dict) -> str:
        """
        Simulates querying academic databases to find recent advancements based on keywords.
        """
        field = metadata.get("field", "Unknown Field")
        keywords = ", ".join(metadata.get("keywords", []))
        db_list = ", ".join(self.academic_databases)
        
        self.logger.info("Simulating search in (%s) using keywords: %s", db_list, keywords)
        
        prompt = f"""
        You are an expert academic research assistant. 
        Simulate a search across the following academic databases: {db_list}.
        The research project is titled '{project_name}' in the field of '{field}'.
        Keywords: {keywords}.
        
        Please provide a brief summary (2-3 paragraphs) of the most recent and significant 
        academic advancements or key papers published recently in this specific area.
        Focus strictly on academic innovation.
        """
        response_text = self.ask_llm(prompt)
        print(f"\n{'='*40}\n🔬 Literature Search Results for: {project_name}\n{'='*40}\n{response_text}\n")
        return response_text

    def generate_comparison_entry(self, project_name: str, metadata: dict, search_results: str) -> dict:
        """
        Generates the rich data row for the global rolling comparison table.
        """
        field = metadata.get("field", "Unknown Field")
        
        # Ask LLM to extract a single paragraph summarizing the "delta" / innovation
        prompt = f"""
        Based on the following recent literature search results for '{project_name}':
        {search_results}
        
        Write a single, concise paragraph summarizing what is the new trend or main 
        innovation discovered in this literature review compared to previous knowledge.
        """
        innovation_summary = self.ask_llm(prompt).strip()
        
        # Note: Researcher name is a placeholder until we extract actual author names from LaTeX
        researcher_name = "Dr. Aviv Peretz" 
        now = datetime.datetime.now()
        
        return {
            "Date & Time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "Project Name": project_name,
            "Researcher Name": researcher_name,
            "Research Field": field,
            "Recent Innovations & Changes": innovation_summary
        }

    def summarize_and_save(self, project_name: str, article_data: str):
        """
        Summarize the found article and save it to the specific project's folder.
        """
        self.logger.info("Saving literature summary for project: %s", project_name)
        self.library.save_summary(project_name, article_data)

    def run(self):
        """
        The main execution flow of the agent.
        """
        self.logger.info("Starting the literature research cycle.")
        
        for project in self.projects:
            # 1. Infer metadata (field & keywords)
            metadata = self.extract_project_metadata(project)
            
            # 2. Search for articles (with DB infrastructure)
            llm_result = self.search_new_articles(project, metadata)
            
            # 3. Save the full literature summary to the project's folder
            self.summarize_and_save(project, llm_result)
            
            # 4. Generate rich data and append to the GLOBAL comparison table
            table_entry = self.generate_comparison_entry(project, metadata, llm_result)
            
            self.logger.info("Updating the global rolling comparison table for %s...", project)
            self.library.update_comparison_table(table_entry)
            
        self.logger.info("Literature research cycle completed successfully.")