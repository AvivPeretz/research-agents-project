import os
import json
import time
import urllib.parse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from agents.notification_agent import NotificationAgent

load_dotenv()

class LiteratureResearchAgent(BaseAgent):
    """
    Agent responsible for analyzing the current research text, generating keywords,
    scraping Google Scholar via Playwright, and extracting data into a 14-column structure.
    """
    def __init__(self, active_projects: list):
        super().__init__(agent_name="LiteratureResearchAgent")
        self.projects = active_projects
        self.library = LibraryManager()
        self.notifier = NotificationAgent() # <--- NEW: Initialize the Notification Service
        
        # We will use the dummy Gmail account for Scholar authentication
        self.dummy_email = os.getenv("NOTIFICATION_SENDER_EMAIL")
        self.dummy_password = os.getenv("NOTIFICATION_SENDER_PASSWORD") # App password or normal password
        self.state_file = "scholar_state.json"
        
        self.logger.info("LiteratureResearchAgent initialized with %d projects.", len(self.projects))

    def _read_project_text(self, project_name: str) -> str:
        """Reads the local extracted text of the project to generate context-aware keywords."""
        project_dir = os.path.join(os.path.abspath("overleaf_projects"), project_name)
        text_content = ""
        if os.path.exists(project_dir):
            for root, _, files in os.walk(project_dir):
                for file in files:
                    if file.endswith('.tex'):
                        try:
                            with open(os.path.join(root, file), 'r', encoding='utf-8') as f:
                                text_content += f.read() + "\n"
                        except Exception:
                            pass
        return text_content[:4000] # Limit to first 4000 characters for token efficiency

    def extract_keywords_from_text(self, project_name: str, text: str) -> str:
        """Uses the actual manuscript text to generate highly targeted search keywords."""
        self.logger.info("Extracting keywords based on manuscript text for: %s", project_name)
        
        prompt = f"""
        Analyze the following excerpt from an academic manuscript titled '{project_name}':
        
        {text}
        
        Generate a highly specific search query (3-5 keywords) that can be used in Google Scholar 
        to find the most recent and relevant literature for this specific research.
        Return ONLY the keywords separated by spaces (e.g., "machine learning automated testing framework").
        """
        keywords = self.ask_llm(prompt).strip()
        # Clean up if LLM added quotes
        return keywords.replace('"', '').replace("'", "")

    def _perform_manual_login(self):
        """Fallback method: Opens browser for user to log into Google Scholar."""
        print("\n🛑 No Google Scholar session found! Initiating manual login...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            
            # FIXED URL: Clean Google login redirecting to Scholar
            login_url = "https://accounts.google.com/ServiceLogin?continue=https://scholar.google.com/"
            page.goto(login_url)
            
            print(f"🔑 Please log in using the dummy account: {self.dummy_email}")
            print("\n🚨 ACTION REQUIRED: Complete the login and solve any CAPTCHAs.")
            print("⏳ Waiting up to 90 seconds for you to reach the Google Scholar homepage...")
            
            try:
                # Wait until we land on the scholar page after login
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
        self.logger.info("Scraping Google Scholar for keywords: '%s'", keywords)
        
        if not os.path.exists(self.state_file):
            self._perform_manual_login()
            
        if not os.path.exists(self.state_file):
            self.logger.error("No session file for Scholar. Cannot proceed with scraping.")
            return []

        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False) # Headless=False helps avoid bot detection
            context = browser.new_context(storage_state=self.state_file)
            page = context.new_page()
            
            try:
                query = urllib.parse.quote_plus(keywords)
                # Search articles from the last year
                page.goto(f"https://scholar.google.com/scholar?q={query}&as_ylo=2023")
                
                # Check for CAPTCHA explicitly
                if page.locator('form[id="captcha-form"]').count() > 0:
                    print("⚠️ Scholar CAPTCHA detected! Please solve it in the browser window.")
                    page.wait_for_selector('div.gs_ri', timeout=60000)
                
                page.wait_for_selector('div.gs_ri', timeout=15000)
                
                # Extract top 3 results
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

    def process_results_with_llm(self, project: str, keywords: str, scholar_data: list) -> dict:
        """Feeds the scraped data to the LLM to generate the 14-column JSON and a summary."""
        self.logger.info("Processing scraped Scholar data via LLM...")
        
        if not scholar_data:
            return None
            
        data_str = json.dumps(scholar_data, indent=2)
        
        prompt = f"""
        Act as an expert academic research assistant. 
        I have scraped Google Scholar for recent papers related to the project: '{project}'.
        Keywords used: {keywords}
        
        Here are the top results:
        {data_str}

        You MUST return your response as a valid JSON object with EXACTLY the following structure:
        {{
            "summary": "A 1-2 paragraph engaging summary describing how these specific papers relate to the project. Embed the Markdown links to the papers in the text (e.g., [Paper Title](url)).",
            "papers": [
                {{
                    "paper name": "Title of the paper",
                    "cited": "V (if highly cited based on your knowledge) or N/A",
                    "source": "Journal or Conference (guess based on your knowledge, or N/A)",
                    "year published": "YYYY",
                    "types of available data": "e.g., PCAP, Images (or N/A)",
                    "number of samples": "e.g., 20000 (or N/A)",
                    "number of features": "e.g., 14 (or N/A)",
                    "number of classes": "e.g., 5 (or N/A)",
                    "location": "e.g., In-lab testbed (or N/A)",
                    "for how long": "e.g., 3 weeks (or N/A)",
                    "is it reproducible?": "Yes/No (or N/A)",
                    "how complicated it is?": "e.g., Moderate (or N/A)",
                    "is there privacy issues?": "e.g., Minimal (or N/A)",
                    "can i control the application collected": "Yes/No (or N/A)"
                }}
            ]
        }}
        Important constraints:
        1. If a specific data point for the 14 columns is not explicitly in the snippet, use "N/A".
        2. ESCAPE ALL STRINGS. Do NOT use raw newlines (\\n) or unescaped quotes inside the JSON string values.
        3. Ensure the JSON is perfectly valid. Do not use markdown blocks like ```json.
        """
        
        response = self.ask_llm(prompt)
        
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            # strict=False allows Python to forgive minor control character errors from the LLM
            parsed_data = json.loads(response[start:end], strict=False)
            return parsed_data
        except Exception as e:
            self.logger.error("Failed to parse the structured JSON from LLM: %s", str(e))
            return None

    def run(self):
        self.logger.info("Starting the literature research cycle.")
        for project in self.projects:
            print(f"\n{'='*40}\n🔬 Literature Search & Data Extraction for: {project}\n{'='*40}")
            
            # 1. Read actual text & extract keywords
            text = self._read_project_text(project)
            keywords = self.extract_keywords_from_text(project, text) if text else project
            
            # 2. Scrape Scholar
            scholar_data = self.scrape_google_scholar(keywords)
            
            if not scholar_data:
                self.logger.warning("No data scraped for %s. Skipping.", project)
                continue
                
            # 3. Process into 14 columns & Summary
            research_data = self.process_results_with_llm(project, keywords, scholar_data)
            
            if not research_data:
                continue
                
            # 4. Save the textual summary for the email notification
            links_section = "\n\n### 🔗 Direct Links to Found Papers:\n"
            for item in scholar_data:
                links_section += f"* [{item['title']}]({item['link']})\n"

            summary_text = f"# Literature Review for: {project}\n\n**Keywords Used:** {keywords}\n\n{research_data.get('summary', 'No summary available.')}{links_section}"
            
            self.library.save_literature_summary(project, summary_text)
            self.logger.info("Saved literature markdown summary for project: %s", project)
            
            # 5. Append each found paper to the 14-column CSV table
            papers_list = research_data.get("papers", [])
            for paper in papers_list:
                self.library.append_to_project_literature_table(project, paper)
                self.logger.info("Appended paper '%s' to rolling CSV table.", paper.get("paper name", "Unknown"))
                
            # --- 6. Trigger the Notification Agent ---
            # Construct the exact path based on your LibraryManager structure
            safe_name = project.replace(" ", "_")
            csv_file_path = os.path.join("research_library", "comparison_tables", safe_name, f"{safe_name}_rolling_table.csv")
            
            self.logger.info("Sending literature update email for %s...", project)
            self.notifier.send_literature_update(
                project_name=project,
                md_content=summary_text,
                csv_path=csv_file_path if os.path.exists(csv_file_path) else None
            )
                
        self.logger.info("Literature research cycle completed successfully.")