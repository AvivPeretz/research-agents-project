import os
import json
import time
import imaplib
import email
import re
from datetime import datetime
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager

load_dotenv()

class ResearchEnhancementAgent(BaseAgent):
    """
    Agent responsible for uploading manuscripts to paperreview.ai, 
    reading the feedback via IMAP safely, and generating actionable tasks.
    """
    def __init__(self, overleaf_projects: list):
        super().__init__(agent_name="ResearchEnhancementAgent")
        self.projects = overleaf_projects
        self.library = LibraryManager()
        
        # Emails
        self.uni_email = os.getenv("OVERLEAF_EMAIL") 
        self.dummy_email = os.getenv("NOTIFICATION_SENDER_EMAIL")
        self.dummy_password = os.getenv("NOTIFICATION_SENDER_PASSWORD")
        
        self.logger.info("ResearchEnhancementAgent initialized for %d projects.", len(self.projects))

    def _get_project_pdf_path(self, project_name: str) -> str:
        project_dir = os.path.join(os.path.abspath("overleaf_projects"), project_name)
        if os.path.exists(project_dir):
            for root, _, files in os.walk(project_dir):
                for file in files:
                    if file.endswith('.pdf'):
                        return os.path.join(root, file)
        return None

    def _get_state_file(self, project_name: str) -> str:
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
            browser = p.chromium.launch(headless=False) 
            context = browser.new_context()
            page = context.new_page()
            try:
                print("🌐 Navigating to Stanford PaperReview...")
                page.goto("https://paperreview.ai/")
                page.wait_for_load_state("networkidle")
                
                print("📂 Uploading the PDF manuscript...")
                file_input = page.locator('input[type="file"]')
                if file_input.count() > 0:
                    file_input.set_input_files(pdf_path)
                else:
                    time.sleep(15)
                
                time.sleep(2)
                
                print("📧 Entering the University email address...")
                email_input = page.locator('input[type="email"]')
                if email_input.count() > 0:
                    email_input.fill(self.uni_email)
                
                print("🚀 Submitting the paper for review...")
                submit_button = page.locator('button:has-text("Submit"), button:has-text("Review"), button[type="submit"]')
                if submit_button.count() > 0:
                    submit_button.first.click()
                else:
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

    def _get_stanford_token_from_email(self, project_name: str) -> str:
        """Phase 2a: Connects to Gmail and extracts the token specifically for this project."""
        print(f"   🔍 Checking Gmail for Stanford review token for '{project_name}'...")
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
            mail.login(self.dummy_email, self.dummy_password)
            mail.select("INBOX")
            
            status, messages = mail.search(None, 'ALL')
            if status == "OK":
                email_ids = messages[0].split()
                for e_id in reversed(email_ids[-30:]):
                    res, msg_data = mail.fetch(e_id, '(RFC822)')
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            
                            # 1. Match the Project Name in the Subject!
                            subject = str(msg.get("Subject", "")).replace('\r', '').replace('\n', '')
                            clean_proj_name = project_name.strip().lower()
                            
                            if clean_proj_name not in subject.lower():
                                continue
                                
                            print(f"   📧 Found matching email subject: {subject}")
                            
                            # 2. Extract the body (handle both plain text and HTML)
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    content_type = part.get_content_type()
                                    if content_type in ["text/plain", "text/html"]:
                                        try:
                                            body += part.get_payload(decode=True).decode(errors='ignore') + " "
                                        except:
                                            pass
                            else:
                                body = msg.get_payload(decode=True).decode(errors='ignore')
                            
                            # 3. Clean HTML tags and normalize whitespace to ensure Regex works
                            clean_body = re.sub(r'<[^>]+>', ' ', body)  # Remove HTML tags
                            clean_body = re.sub(r'\s+', ' ', clean_body) # Remove extra spaces/newlines
                            
                            # 4. Exact Regex Match (Catch ANY character until the next space)
                            match = re.search(r'Your Access Token:\s*([^\s]{20,})', clean_body, re.IGNORECASE)
                            if match:
                                token = match.group(1).strip()
                                # Print the FULL token and its length so we can debug it
                                print(f"   ✅ Found Correct Stanford Token: {token} (Length: {len(token)})")
                                mail.logout()
                                return token
                            else:
                                print("   ⚠️ Email matched subject, but token not found in body! (Check email formatting)")
            mail.logout()
            print(f"   ⏳ No token found yet for '{project_name}'.")
            return None
        except Exception as e:
            self.logger.error("IMAP Error: %s", str(e))
            return None

    def _fetch_review_from_stanford(self, token: str) -> str:
        """Phase 2b: Uses Playwright to submit the token and scrape the review."""
        print("   🌐 Navigating to paperreview.ai/review to fetch feedback...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            try:
                page.goto("https://paperreview.ai/review")
                page.wait_for_load_state("networkidle")
                
                print("   🔑 Entering the access token...")
                token_input = page.locator('input:visible').first
                token_input.click()
                token_input.fill(token)
                page.wait_for_timeout(1000)
                
                print("   🚀 Submitting token to view review...")
                token_input.press("Enter")
                page.wait_for_timeout(1000)
                
                buttons = page.locator('button:visible')
                if buttons.count() > 0:
                    buttons.first.click()
                
                print("   ⏳ Waiting for the 'Summary' or 'Strengths' sections to load...")
                # Smart wait: Wait explicitly for the review content to appear (max 15 seconds)
                try:
                    page.locator('text="Summary"').first.wait_for(state="visible", timeout=15000)
                except:
                    print("   ⚠️ Timed out waiting for 'Summary'. Checking for 'Invalid Token' error...")
                    if page.locator('text="Invalid Token"').count() > 0 or page.locator('text="Error"').count() > 0:
                        print("   ❌ Error: The website rejected the token.")
                        return None
                
                # Scrape all text. Inner text naturally keeps the structure of the headers.
                review_text = page.locator('body').inner_text()
                
                if len(review_text) < 400:
                    print("   ⚠️ Warning: The scraped text is very short. It might still be on the login screen.")
                    return None
                else:
                    print(f"   ✅ Review scraped successfully! (Length: {len(review_text)} characters)")
                    return review_text
            except Exception as e:
                self.logger.error("Failed to scrape review: %s", str(e))
                return None
            finally:
                context.close()
                browser.close()

    def _generate_actionable_tasks(self, project_name: str, review_text: str) -> str:
        """Phase 2c: Uses Groq to parse the review and generate tasks with deadlines."""
        print("   🧠 Sending Stanford review to LLM for task generation & novelty check...")
        prompt = f"""
        You are a rigorous Academic Research Manager. You have received an external peer-review from Stanford for the project '{project_name}'.
        
        Here is the raw review text:
        ---
        {review_text}
        ---
        
        Please read the entire review carefully and provide a structured Markdown response containing:
        1. **Novelty & Innovation Check**: Summarize what the reviewer thought about the paper's innovation.
        2. **Actionable Task List**: Extract specific critiques and turn them into a practical, numbered To-Do list. 
           For EACH task, you MUST provide:
           - **Task Description**: What specifically needs to be fixed or added. Avoid heavy LaTeX blocks if standard text/Unicode can explain it cleanly.
           - **Estimated Effort**: Analyze the complexity of the task (e.g., "Requires writing a new mathematical proof", "Simple typo correction", "Running new simulations"). Estimate the actual working time required (e.g., "~15 hours of focused work", "~2 hours").
           - **Recommended Deadline**: Suggest a specific deadline within the next 30-day sprint based on the effort required.
        """
        
        try:
            tasks = self.ask_llm(prompt)
            
            safe_name = project_name.replace(" ", "_")
            save_path = os.path.join(self.library.base_dir, "project_enhancement", safe_name, "stanford_tasks.md")
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(tasks)
                
            print(f"   ✅ Tasks generated and saved to {save_path}")
            return tasks
        except Exception as e:
            self.logger.error("LLM Generation failed: %s", str(e))
            return None

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
                print("⏳ Project is waiting for review. Initiating Phase 2 (IMAP Check)...")
                # We now pass the specific project name to the email fetcher!
                token = self._get_stanford_token_from_email(project)
                
                if token:
                    review_text = self._fetch_review_from_stanford(token)
                    if review_text:
                        tasks = self._generate_actionable_tasks(project, review_text)
                        if tasks:
                            state["status"] = "REVIEW_COMPLETED" 
                            self._save_state(project, state)
                            print("✅ Phase 2 complete. Tasks generated and state updated to REVIEW_COMPLETED.")
                else:
                    print("   ⏭️ Review email not yet received or token not found. Will try again next run.")
                    
        self.logger.info("Research Enhancement cycle completed.")