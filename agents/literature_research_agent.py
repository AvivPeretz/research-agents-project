import os
import json
import time
from datetime import datetime
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
    def __init__(self, active_projects: list, notifier: NotificationAgent, db=None):
        super().__init__(agent_name="LiteratureResearchAgent")
        self.projects = active_projects
        self.library = LibraryManager()
        self.notifier = notifier

        # Initialize the dedicated fetcher service
        self.fetcher = LiteratureFetcher()

        self.db = db
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
        papers_json = json.dumps(scholar_data, indent=2)

        prompt = f"""You are an academic research assistant. Analyze the following list of \
research papers and extract structured metadata for each one.

For each paper, you are given:
- title, abstract, year, citation count, venue, url
- openalex: a dict with topics, keywords, is_oa, oa_url (may be empty)

Extract the following fields for EACH paper. Read the column definitions carefully:

COLUMN DEFINITIONS:
- "paper name": The full title of the paper. Copy it exactly.
- "cited": The number of times this paper has been cited (integer as string).
- "source": The URL where the paper can be accessed.
- "year published": The year the paper was published (4-digit string).
- "types of available data": What type of dataset does the paper use or \
describe? Look for mentions of dataset names, data types (e.g., network \
traffic, sensor readings, images, signals). Use OpenAlex topics/keywords \
to help identify. If not mentioned, use "N/A".
- "number of samples": How many data samples/instances are in the dataset? \
Look for phrases like "X samples", "dataset of X", "X instances". \
If not stated, use "N/A".
- "number of features": What features or attributes does the data have? \
In communication/networking papers, look for feature types like SNR, RSSI, \
packet size, frequency bands. List them briefly or count them. \
If not stated, use "N/A".
- "number of classes": How many classes or categories are in the dataset? \
For classification papers, look for "X-class", "X categories", \
"binary classification". If not stated, use "N/A".
- "location": Where was the data collected? Lab name, university, city, \
or type of environment (e.g., "indoor lab", "outdoor testbed", \
"simulation"). If not stated, use "N/A".
- "for how long": Over what duration was the data collected? \
Look for "X days", "X weeks", "X hours". If not stated, use "N/A".
- "is there privacy issues?": Does the paper mention privacy concerns about \
its data? Look for mentions of anonymization, GDPR, personal data, \
user consent. Answer "Yes", "No", or "N/A".
- "is it reproducible?": Is there a link to code, GitHub repository, or \
dataset that allows reproducing results? Check oa_url from OpenAlex too. \
If is_oa is True, mention it. Answer "Yes (link: URL)", "No", or "N/A".
- "data representation": How is the raw data represented as input features? \
Look for how the paper converts raw signals/data into model inputs: \
e.g., "spectrogram image", "feature vector", "graph", "time series array", \
"frequency-domain matrix", "raw bytes". Use OpenAlex topics/keywords to help. \
If not stated, use "N/A".
- "how complicated is it?": Leave this field COMPLETELY EMPTY — do not write \
anything, not even N/A.
- "can i control the application collected?": Leave this field COMPLETELY \
EMPTY — do not write anything, not even N/A.

IMPORTANT RULES:
- Base your answers primarily on the abstract text provided.
- Use OpenAlex topics and keywords as supplementary hints, not as the \
primary source.
- If a field truly cannot be determined from the available text, use "N/A".
- Do NOT hallucinate values — only extract what is explicitly stated or \
strongly implied in the text.
- Return ONLY a valid JSON object. No markdown, no explanation.

Return format:
{{
  "summary": "A 2-3 sentence overview of the literature landscape for this project based on all papers combined.",
  "papers": [
    {{
      "paper name": "...",
      "cited": "...",
      "source": "...",
      "year published": "...",
      "types of available data": "...",
      "number of samples": "...",
      "number of features": "...",
      "number of classes": "...",
      "location": "...",
      "for how long": "...",
      "is there privacy issues?": "...",
      "is it reproducible?": "...",
      "data representation": "...",
      "how complicated is it?": "",
      "can i control the application collected?": ""
    }}
  ]
}}

Papers to analyze:
{papers_json}
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
            if self.db:
                self.db.log_agent_run(
                    agent_name=self.agent_name,
                    project_name=project,
                    status="STARTED",
                    started_at=datetime.now().isoformat()
                )
            print(f"\n{'='*40}\n🔬 Literature Search & Data Extraction for: {project}\n{'='*40}")

            text = self._read_project_text(project)
            keywords = self.extract_keywords_from_text(project, text) if text else project
            
            # Generate 2 additional keyword angles from the project name and text
            domain_keywords = project  # project name as domain query
            method_keywords = keywords + " methodology" if keywords else project + " method"

            # Run search for each query angle and merge results
            all_papers = []
            seen_titles = set()

            for i, query in enumerate([keywords, domain_keywords, method_keywords]):
                if not query or not query.strip():
                    continue
                if i > 0:
                    time.sleep(1.5)  # 1 req/sec limit; internal rate limiter handles per-call timing
                results = self.fetcher.search(query)
                for paper in results:
                    title = paper.get("title", "").lower().strip()
                    if title and title not in seen_titles:
                        seen_titles.add(title)
                        all_papers.append(paper)

            # Cap at 12 unique papers total
            all_papers = all_papers[:12]

            # SerpAPI + scholarly fallback chain — only triggered when Semantic Scholar is empty
            if not all_papers:
                self.logger.warning(
                    "Semantic Scholar returned no results for project '%s'. "
                    "Trying SerpAPI fallback...", project
                )
                all_papers = self.fetcher.fetch_from_serpapi(keywords)

                if not all_papers:
                    self.logger.warning(
                        "SerpAPI returned no results for project '%s'. "
                        "Trying scholarly as last resort...", project
                    )
                    all_papers = self.fetcher.fetch_from_scholarly(keywords)

                if not all_papers:
                    self.logger.error(
                        "All search sources failed for project '%s'. "
                        "No literature update will be sent.", project
                    )
                    if self.db:
                        self.db.log_agent_run(
                            agent_name=self.agent_name,
                            project_name=project,
                            status="FAILURE",
                            finished_at=datetime.now().isoformat()
                        )
                    continue

            # Unified cap after fallback chain
            all_papers = all_papers[:12]

            # Enrich with OpenAlex metadata before LLM processing
            # (papers already enriched by fetch_from_openalex are skipped)
            all_papers = self.fetcher.enrich_with_openalex(all_papers)

            if not all_papers:
                self.logger.warning("No data fetched for %s. Skipping LLM processing.", project)
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=project,
                        status="SUCCESS",
                        finished_at=datetime.now().isoformat()
                    )
                continue

            research_data = self.process_results_with_llm(project, keywords, all_papers)
            
            if not research_data.get("papers"):
                self.logger.warning("No parsed papers available for %s. Saving summary only.", project)
                
            links_section = "\n\n### 🔗 Direct Links to Found Papers:\n"
            for item in all_papers:
                links_section += f"* [{item['title']}]({item.get('link', item.get('url', ''))})\n"

            summary_text = f"# Literature Review for: {project}\n\n**Keywords Used:** {keywords}\n\n{research_data.get('summary', 'No summary available.')}{links_section}"

            self.library.save_literature_summary(project, summary_text)
            self.logger.info("Saved literature markdown summary for project: %s", project)

            papers_list = research_data.get("papers", [])
            for paper in papers_list:
                self.library.append_to_project_literature_table(project, paper)

                # Using .get() gracefully defaults if the key somehow wasn't mapped
                paper_title = paper.get("paper name", "Unknown")
                self.logger.info("Appended paper '%s' to rolling CSV table.", paper_title)

            safe_name = project.replace(" ", "_")
            csv_file_path = os.path.join(Config.LIBRARY_DIR, "comparison_tables", safe_name, f"{safe_name}_rolling_table.csv")

            if not research_data.get("papers"):
                self.logger.info(
                    "No papers found for project '%s'. Skipping literature email.", project
                )
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=project,
                        status="SKIPPED",
                        finished_at=datetime.now().isoformat()
                    )
                continue

            self.logger.info("Sending literature update email for %s...", project)
            if not os.path.exists(csv_file_path):
                self.logger.warning(
                    "Rolling CSV not found at expected path: %s — "
                    "email will be sent without attachment.", csv_file_path
                )

            self.notifier.send_literature_update(
                project_name=project,
                md_content=summary_text,
                csv_path=csv_file_path if os.path.exists(csv_file_path) else None
            )
            if self.db:
                self.db.log_agent_run(
                    agent_name=self.agent_name,
                    project_name=project,
                    status="SUCCESS",
                    finished_at=datetime.now().isoformat()
                )

        self.logger.info("Literature research cycle completed successfully.")