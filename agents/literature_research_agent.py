import os
import json
from pydantic import ValidationError
from domain.schemas import LiteratureReport
import re 
# Import the centralized configuration
from config import Config
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from agents.notification_agent import NotificationAgent

# NEW: Import our dedicated fetcher
from utils.literature_fetcher import LiteratureFetcher 

class LiteratureResearchAgent(BaseAgent):
    """
    Agent responsible for analyzing the current research text, generating keywords,
    fetching literature via LiteratureFetcher, and extracting data using Pydantic contracts.
    """
    def __init__(self, active_projects: list, notifier: NotificationAgent):
        super().__init__(agent_name="LiteratureResearchAgent")
        self.projects = active_projects
        self.library = LibraryManager()
        self.notifier = notifier 
        
        # Initialize the dedicated fetcher service
        self.fetcher = LiteratureFetcher()
        
        self.logger.info("LiteratureResearchAgent initialized with %d projects.", len(self.projects))

    def _read_project_text(self, project_name: str) -> str:
        """Reads the local extracted text of the project to generate context-aware keywords."""
        project_dir = os.path.join(Config.OVERLEAF_DIR, project_name)
        text_content = ""
        if os.path.exists(project_dir):
            for root, _, files in os.walk(project_dir):
                if files:
                    for file in files:
                        if file.endswith('.tex'):
                            try:
                                with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                    text_content += f.read() + "\n"
                            except Exception as e:
                                self.logger.warning("Failed to read .tex file: %s", str(e))
                            
        # Defensive check
        if not text_content or text_content.strip() == "":
            self.logger.warning("No valid LaTeX text extracted for project: %s", project_name)
            return ""
            
        return text_content[:4000]

    def extract_keywords_from_text(self, project_name: str, text: str) -> str:
        """Uses the actual manuscript text to generate highly targeted search keywords."""
        if not text or text.strip() == "":
            self.logger.warning("Empty text provided for keyword extraction. Using project name as fallback.")
            return project_name
            
        self.logger.info("Extracting keywords based on manuscript text for: %s", project_name)
        
        prompt = f"""
        Analyze the following excerpt from an academic manuscript titled '{project_name}':
        
        {text}
        
        Generate a highly specific search query (3-5 keywords) that can be used in academic databases 
        to find the most recent and relevant literature for this specific research.
        Return ONLY the keywords separated by spaces (e.g., "machine learning automated testing framework").
        """
        try:
            keywords = self.ask_llm(prompt).strip()
            return keywords.replace('"', '').replace("'", "")
        except RuntimeError as e:
            self.logger.error("LLM failed to generate keywords: %s", str(e))
            return project_name

    def process_results_with_llm(self, project: str, keywords: str, scholar_data: list) -> dict:
        """Feeds the scraped data to the LLM and validates the output strictly against Pydantic schemas."""
        fallback_data = {
            "summary": "The LLM was unable to generate a valid summary for these papers due to a formatting error.",
            "papers": []
        }
        
        if not scholar_data:
            self.logger.warning("No scholar data provided to LLM processing.")
            return fallback_data
            
        self.logger.info("Processing fetched literature data via LLM with Pydantic validation...")
        data_str = json.dumps(scholar_data, indent=2)
        
        prompt = f"""
        Act as an expert academic research assistant. 
        I have fetched recent papers related to the project: '{project}'.
        Keywords used: {keywords}
        
        Here are the enriched results (containing full abstracts, exact citation counts, and venues):
        {data_str}

        You MUST return your response as a valid JSON object with EXACTLY the following structure:
        {{
            "summary": "A 1-2 paragraph engaging summary describing how these specific papers relate to the project. Embed the Markdown links to the papers in the text (e.g., [Paper Title](url)).",
            "papers": [
                {{
                    "paper_name": "Title of the paper",
                    "cited": "Use the 'citationCount' from the data (e.g., '15' or 'N/A')",
                    "source": "Use the 'venue' from the data (e.g., 'IEEE Transactions...' or 'N/A')",
                    "year_published": "Use the 'year' from the data",
                    "types of available data": "Describe data type based on abstract. IF this is a Theoretical/Mathematical paper, explicitly write 'Theoretical Paper - No Dataset'",
                    "number of samples": "e.g., 20000. IF theoretical, write 'N/A (Theoretical)'",
                    "number of features": "e.g., 14. IF theoretical, write 'N/A (Theoretical)'",
                    "number of classes": "e.g., 5. IF theoretical, write 'N/A (Theoretical)'",
                    "location": "e.g., In-lab testbed. IF theoretical, write 'N/A (Theoretical)'",
                    "for how long": "e.g., 3 weeks (or N/A)",
                    "reproducible": "Must be EXACTLY 'Yes', 'No', or 'N/A'",
                    "complexity": "Must be EXACTLY 'High', 'Moderate', 'Low', or 'N/A'",
                    "is there privacy issues?": "Must be EXACTLY 'Yes', 'No', 'Minimal', 'High', or 'N/A'",
                    "can i control the application collected": "Must be EXACTLY 'Yes', 'No', or 'N/A'"
                }}
            ]
        }}
        Important constraints:
        1. Keep the EXACT JSON keys as defined above.
        2. Obey the strict EXACTLY string match rules for dropdown fields (like reproducible, complexity).
        3. ESCAPE ALL STRINGS properly. Do NOT use markdown blocks like ```json.
        """
        
        try:
            response = self.ask_llm(prompt)
            start = response.find('{')
            end = response.rfind('}') + 1
            
            if start == -1 or end == 0:
                 raise ValueError("LLM response did not contain JSON brackets.")
                 
            raw_json = response[start:end]
            raw_json = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw_json)
            validated_report = LiteratureReport.model_validate_json(raw_json)
            return validated_report.model_dump(by_alias=True)
            
        except ValidationError as e:
            self.logger.error("Pydantic Schema Validation Failed! LLM hallucinated bad structure: %s", str(e))
            return fallback_data
        except (RuntimeError, ValueError, json.JSONDecodeError) as e:
            self.logger.error("Failed to parse or extract JSON from LLM: %s", str(e))
            return fallback_data

    def run(self):
        self.logger.info("Starting the literature research cycle.")
        for project in self.projects:
            print(f"\n{'='*40}\n🔬 Literature Search & Data Extraction for: {project}\n{'='*40}")
            
            text = self._read_project_text(project)
            keywords = self.extract_keywords_from_text(project, text) if text else project
            
            # --- NEW: Clean, single call to the Fetcher ---
            enriched_data = self.fetcher.search(keywords)
            
            if not enriched_data:
                self.logger.warning("No data fetched for %s. Skipping LLM processing.", project)
                continue
                
            research_data = self.process_results_with_llm(project, keywords, enriched_data)
            
            if not research_data.get("papers"):
                self.logger.warning("No parsed papers available for %s. Saving summary only.", project)
                
            links_section = "\n\n### 🔗 Direct Links to Found Papers:\n"
            for item in enriched_data:
                links_section += f"* [{item['title']}]({item['link']})\n"

            summary_text = f"# Literature Review for: {project}\n\n**Keywords Used:** {keywords}\n\n{research_data.get('summary', 'No summary available.')}{links_section}"
            
            self.library.save_literature_summary(project, summary_text)
            self.logger.info("Saved literature markdown summary for project: %s", project)
            
            papers_list = research_data.get("papers", [])
            for paper in papers_list:
                self.library.append_to_project_literature_table(project, paper)
                
                # Using .get() gracefully defaults if the key somehow wasn't mapped
                paper_title = paper.get("paper_name", "Unknown") or paper.get("paper name", "Unknown")
                self.logger.info("Appended paper '%s' to rolling CSV table.", paper_title)
                
            safe_name = project.replace(" ", "_")
            csv_file_path = os.path.join(Config.LIBRARY_DIR, "comparison_tables", safe_name, f"{safe_name}_rolling_table.csv")
            
            self.logger.info("Sending literature update email for %s...", project)
            self.notifier.send_literature_update(
                project_name=project,
                md_content=summary_text,
                csv_path=csv_file_path if os.path.exists(csv_file_path) else None
            )
                
        self.logger.info("Literature research cycle completed successfully.")