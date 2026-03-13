import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager

load_dotenv()

class ResearchEnhancementAgent(BaseAgent):
    """
    Agent responsible for uploading manuscripts to paperreview.ai, 
    reading the feedback via IMAP, and generating actionable tasks with deadlines.
    """
    def __init__(self, overleaf_projects: list):
        super().__init__(agent_name="ResearchEnhancementAgent")
        self.projects = overleaf_projects
        self.library = LibraryManager()
        
        # We use the University email for the Stanford form (academic credibility)
        self.uni_email = os.getenv("OVERLEAF_EMAIL") 
        
        self.logger.info("ResearchEnhancementAgent initialized for %d projects.", len(self.projects))

    def _get_project_pdf_path(self, project_name: str) -> str:
        """Finds the downloaded PDF for the project."""
        project_dir = os.path.join(os.path.abspath("overleaf_projects"), project_name)
        if os.path.exists(project_dir):
            for root, _, files in os.walk(project_dir):
                for file in files: # הוספנו את הלולאה שרצה על רשימת הקבצים
                    if file.endswith('.pdf'):
                        return os.path.join(root, file)
        return None

    def _get_state_file(self, project_name: str) -> str:
        """Gets the path for the state tracking file."""
        safe_name = project_name.replace(" ", "_")
        project_dir = os.path.join(self.library.base_dir, "project_enhancement", safe_name)
        if not os.path.exists(project_dir):
            os.makedirs(project_dir)
        return os.path.join(project_dir, "stanford_state.json")

    def _get_state(self, project_name: str) -> dict:
        state_file = self._get_state_file(project_name)
        if os.path.exists(state_file):
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {"status": "READY_FOR_UPLOAD", "last_upload_time": None}

    def _save_state(self, project_name: str, state: dict):
        with open(self._get_state_file(project_name), 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=4)

    def upload_to_stanford(self, project_name: str, pdf_path: str) -> bool:
        """Phase 1: Uploads the PDF to paperreview.ai using Playwright."""
        self.logger.info("Initiating Phase 1: Uploading '%s' to paperreview.ai...", project_name)
        
        with sync_playwright() as p:
            # Headless=False so you can watch the magic happen!
            browser = p.chromium.launch(headless=False) 
            context = browser.new_context()
            page = context.new_page()
            
            try:
                print("🌐 Navigating to Stanford PaperReview...")
                page.goto("https://paperreview.ai/")
                page.wait_for_load_state("networkidle")
                
                print("📂 Uploading the PDF manuscript...")
                # Try to find the file input automatically
                file_input = page.locator('input[type="file"]')
                if file_input.count() > 0:
                    file_input.set_input_files(pdf_path)
                else:
                    print("⚠️ Could not find the upload button. Please click it and upload manually within the next 15 seconds!")
                    time.sleep(15) # Fallback if DOM changes
                
                time.sleep(2) # Give it a moment to process the file
                
                print("📧 Entering the University email address...")
                email_input = page.locator('input[type="email"]')
                if email_input.count() > 0:
                    email_input.fill(self.uni_email)
                
                print("🚀 Submitting the paper for review...")
                # Try common submit button texts
                submit_button = page.locator('button:has-text("Submit"), button:has-text("Review"), button[type="submit"]')
                if submit_button.count() > 0:
                    submit_button.first.click()
                else:
                    print("⚠️ Please click the submit button manually!")
                    time.sleep(10)
                
                print("✅ Upload process finished! Waiting 5 seconds to ensure the server received it...")
                time.sleep(5)
                return True
                
            except Exception as e:
                self.logger.error("Failed to upload to Stanford: %s", str(e))
                return False
            finally:
                context.close()
                browser.close()

    def run(self):
        self.logger.info("Starting Research Enhancement cycle.")
        for project in self.projects:
            print(f"\n{'-'*40}\n🧠 Stanford Peer-Review Engine: {project}\n{'-'*40}")
            
            state = self._get_state(project)
            
            if state["status"] == "READY_FOR_UPLOAD":
                pdf_path = self._get_project_pdf_path(project)
                if not pdf_path:
                    self.logger.warning("No PDF found for %s. Cannot upload.", project)
                    continue
                    
                success = self.upload_to_stanford(project, pdf_path)
                if success:
                    state["status"] = "WAITING_FOR_REVIEW"
                    state["last_upload_time"] = datetime.now().isoformat()
                    self._save_state(project, state)
                    self.logger.info("✅ Project state changed to WAITING_FOR_REVIEW.")
                    
            elif state["status"] == "WAITING_FOR_REVIEW":
                self.logger.info("⏳ Project is waiting for review. Phase 2 (IMAP Check) will run here...")
                
        self.logger.info("Research Enhancement cycle completed.")