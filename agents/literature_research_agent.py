import os
import json
import time
import urllib.parse
from playwright.sync_api import sync_playwright
import requests

# Import the centralized configuration
from config import Config
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from agents.notification_agent import NotificationAgent
from pydantic import ValidationError
from domain.schemas import LiteratureReport

class LiteratureResearchAgent(BaseAgent):
    """
    Agent responsible for analyzing the current research text, generating keywords,
    scraping Google Scholar via Playwright, and extracting data into a 14-column structure.
    """
    def __init__(self, active_projects: list, notifier: NotificationAgent):
        super().__init__(agent_name="LiteratureResearchAgent")
        self.projects = active_projects
        self.library = LibraryManager()
        self.notifier = notifier # <--- DEPENDENCY INJECTION
        
        # Use Config for credentials and state paths
        self.dummy_email = Config.NOTIFICATION_SENDER_EMAIL
        self.dummy_password = Config.NOTIFICATION_SENDER_PASSWORD
        self.state_file = Config.SCHOLAR_STATE_PATH
        
        self.logger.info("LiteratureResearchAgent initialized with %d projects.", len(self.projects))

    def _read_project_text(self, project_name: str) -> str:
        """Reads the local extracted text of the project to generate context-aware keywords."""
        project_dir = os.path.join(Config.OVERLEAF_DIR, project_name)
        text_content = ""
        if os.path.exists(project_dir):
            for root, _, files in os.walk(project_dir):
                for file in files:
                    if file.endswith('.tex'):
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                text_content += f.read() + "\n"
                        except Exception as e:
                            self.logger.warning("Failed to read .tex file: %s", str(e))
                            
        # Defensive check: ensure we don't return None or empty string if reading failed
        if not text_content or text_content.strip() == "":
            self.logger.warning("No valid LaTeX text extracted for project: %s", project_name)
            return ""
            
        return text_content[:4000]

    def extract_keywords_from_text(self, project_name: str, text: str) -> str:
        """Uses the actual manuscript text to generate highly targeted search keywords."""
        # Defensive programming: Check for empty input
        if not text or text.strip() == "":
            self.logger.warning("Empty text provided for keyword extraction. Using project name as fallback.")
            return project_name
            
        self.logger.info("Extracting keywords based on manuscript text for: %s", project_name)
        
        prompt = f"""
        Analyze the following excerpt from an academic manuscript titled '{project_name}':
        
        {text}
        
        Generate a highly specific search query (3-5 keywords) that can be used in Google Scholar 
        to find the most recent and relevant literature for this specific research.
        Return ONLY the keywords separated by spaces (e.g., "machine learning automated testing framework").
        """
        try:
            keywords = self.ask_llm(prompt).strip()
            return keywords.replace('"', '').replace("'", "")
        except RuntimeError as e:
            # Catch the new Exception from BaseAgent and provide a fallback
            self.logger.error("LLM failed to generate keywords: %s", str(e))
            return project_name

    def _perform_manual_login(self):
        """Fallback method: Opens browser for user to log into Google Scholar."""
        print("\n🛑 No Google Scholar session found! Initiating manual login...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            
            login_url = "https://accounts.google.com/ServiceLogin?continue=https://scholar.google.com/"
            page.goto(login_url)
            
            print(f"🔑 Please log in using the dummy account: {self.dummy_email}")
            print("\n🚨 ACTION REQUIRED: Complete the login and solve any CAPTCHAs.")
            print("⏳ Waiting up to 90 seconds for you to reach the Google Scholar homepage...")
            
            try:
                page.wait_for_url("https://scholar.google.com/**", timeout=90000)
                print("✅ Reached Google Scholar! Saving session securely...")
                context.storage_state(path=self.state_file)
                time.sleep(2)
            except Exception as e:
                print(f"❌ Login failed or timed out: {e}")
            finally:
                context.close()
                browser.close()

    def scrape_google_scholar(self, keywords: str) -> list:
        """Scrapes Google Scholar for the given keywords and extracts Titles, Links, and Snippets."""
        # Defensive programming: Check for empty keywords
        if not keywords or keywords.strip() == "":
            self.logger.error("Empty keywords provided to Google Scholar scraper.")
            return []
            
        self.logger.info("Scraping Google Scholar for keywords: '%s'", keywords)
        
        if not os.path.exists(self.state_file):
            self._perform_manual_login()
            
        if not os.path.exists(self.state_file):
            self.logger.error("No session file for Scholar. Cannot proceed with scraping.")
            return []

        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(storage_state=self.state_file)
            page = context.new_page()
            
            try:
                query = urllib.parse.quote_plus(keywords)
                page.goto(f"https://scholar.google.com/scholar?q={query}&as_ylo=2023")
                
                if page.locator('form[id="captcha-form"]').count() > 0:
                    print("⚠️ Scholar CAPTCHA detected! Please solve it in the browser window.")
                    page.wait_for_selector('div.gs_ri', timeout=60000)
                
                # Use Playwright timeout from Config
                page.wait_for_selector('div.gs_ri', timeout=Config.PLAYWRIGHT_TIMEOUT_MS)
                
                article_elements = page.locator('div.gs_ri').all()[:3]
                
                for el in article_elements:
                    title_el = el.locator('h3.gs_rt a')
                    if title_el.count() > 0:
                        title = title_el.inner_text()
                        link = title_el.get_attribute('href')
                        snippet = el.locator('div.gs_rs').inner_text() if el.locator('div.gs_rs').count() > 0 else "No snippet available."
                        
                        results.append({
                            "title": title,
                            "link": link,
                            "snippet": snippet
                        })
                        
            except Exception as e:
                self.logger.error("Error scraping Google Scholar: %s", str(e))
            finally:
                context.close()
                browser.close()
                
        return results

    def enrich_with_semantic_scholar(self, scholar_data: list) -> list:
        """Takes Google Scholar results and fetches full abstracts & metadata from Semantic Scholar API."""
        if not scholar_data:
            return []
            
        # Defensive check: ZeroDivisionError prevention for future stat calculations
        total_papers = len(scholar_data)
        if total_papers == 0:
            return []
            
        self.logger.info("Enriching %d Google Scholar results with Semantic Scholar API...", total_papers)
        enriched_data = []
        
        for item in scholar_data:
            title = item.get('title', '')
            if not title:
                self._apply_fallback_data(item)
                enriched_data.append(item)
                continue
                
            query = urllib.parse.quote_plus(title)
            url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit=1&fields=title,abstract,year,citationCount,venue"
            
            try:
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data') and len(data['data']) > 0:
                        paper_info = data['data'][0]
                        item['abstract'] = paper_info.get('abstract') or item['snippet']
                        item['year'] = paper_info.get('year') or "N/A"
                        item['citationCount'] = paper_info.get('citationCount') or "N/A"
                        item['venue'] = paper_info.get('venue') or "N/A"
                        self.logger.info(f"Successfully enriched: {title[:30]}...")
                    else:
                        self._apply_fallback_data(item)
                else:
                    self._apply_fallback_data(item)
            except Exception as e:
                self.logger.warning(f"Semantic Scholar API failed for '{title}': {e}")
                self._apply_fallback_data(item)
                
            # Respect Rate Limit from Config
            time.sleep(300 / Config.SEMANTIC_SCHOLAR_RATE_LIMIT) 
                
            enriched_data.append(item)
            
        return enriched_data

    def _apply_fallback_data(self, item: dict):
        """Helper to safely fallback to Google Scholar data if Semantic Scholar fails."""
        item['abstract'] = item.get('snippet', 'No abstract available.')
        item['year'] = "N/A"
        item['citationCount'] = "N/A"
        item['venue'] = "N/A"    

    def process_results_with_llm(self, project: str, keywords: str, scholar_data: list) -> dict:
        """Feeds the scraped data to the LLM and validates the output strictly against Pydantic schemas."""
        # Defensive programming: Fallback structure
        fallback_data = {
            "summary": "The LLM was unable to generate a valid summary for these papers due to a formatting error.",
            "papers": []
        }
        
        if not scholar_data:
            self.logger.warning("No scholar data provided to LLM processing.")
            return fallback_data
            
        self.logger.info("Processing scraped Scholar data via LLM with Pydantic validation...")
        data_str = json.dumps(scholar_data, indent=2)
        
        # Notice how the JSON keys now perfectly match our Pydantic variables and aliases
        prompt = f"""
        Act as an expert academic research assistant. 
        I have scraped recent papers related to the project: '{project}'.
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
            
            # --- NEW: Pydantic Strict Validation ---
            validated_report = LiteratureReport.model_validate_json(raw_json)
            
            # Convert back to dict using aliases so LibraryManager CSV creation works seamlessly
            return validated_report.model_dump(by_alias=True)
            
        except ValidationError as e:
            # This catches hallucinations: missing keys, wrong types, or invalid Enums
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
            
            scholar_data = self.scrape_google_scholar(keywords)
            
            if not scholar_data:
                self.logger.warning("No data scraped for %s. Skipping.", project)
                continue
                
            enriched_data = self.enrich_with_semantic_scholar(scholar_data)
                
            research_data = self.process_results_with_llm(project, keywords, enriched_data)
            
            # Now we know research_data will always be a valid dict, even if empty/fallback
            if not research_data.get("papers"):
                self.logger.warning("No parsed papers available for %s. Saving summary only.", project)
                
            links_section = "\n\n### 🔗 Direct Links to Found Papers:\n"
            for item in scholar_data:
                links_section += f"* [{item['title']}]({item['link']})\n"

            summary_text = f"# Literature Review for: {project}\n\n**Keywords Used:** {keywords}\n\n{research_data.get('summary', 'No summary available.')}{links_section}"
            
            self.library.save_literature_summary(project, summary_text)
            self.logger.info("Saved literature markdown summary for project: %s", project)
            
            papers_list = research_data.get("papers", [])
            for paper in papers_list:
                self.library.append_to_project_literature_table(project, paper)
                self.logger.info("Appended paper '%s' to rolling CSV table.", paper.get("paper name", "Unknown"))
                
            safe_name = project.replace(" ", "_")
            csv_file_path = os.path.join(Config.LIBRARY_DIR, "comparison_tables", safe_name, f"{safe_name}_rolling_table.csv")
            
            self.logger.info("Sending literature update email for %s...", project)
            self.notifier.send_literature_update(
                project_name=project,
                md_content=summary_text,
                csv_path=csv_file_path if os.path.exists(csv_file_path) else None
            )
                
        self.logger.info("Literature research cycle completed successfully.")