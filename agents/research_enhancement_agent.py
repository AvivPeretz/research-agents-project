import os
import time
import imaplib
import email
import re
from datetime import datetime
from playwright.sync_api import sync_playwright

# Import the centralized configuration
from config import Config
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from agents.notification_agent import NotificationAgent

class ResearchEnhancementAgent(BaseAgent):
    """
    Agent responsible for uploading manuscripts to paperreview.ai, 
    reading the feedback via IMAP safely, and generating actionable tasks.
    State is now fully managed via the central SQLite database.
    """
    def __init__(self, overleaf_projects: list, notifier: NotificationAgent, db=None):
        super().__init__(agent_name="ResearchEnhancementAgent")
        self.projects = overleaf_projects
        self.library = LibraryManager()
        self.notifier = notifier 
        
        # --- NEW: Dependency Injection for Database ---
        self.db = db 
        
        # Use Config for credentials
        self.uni_email = Config.OVERLEAF_EMAIL 
        self.dummy_email = Config.NOTIFICATION_SENDER_EMAIL
        self.dummy_password = Config.NOTIFICATION_SENDER_PASSWORD
        
        self.logger.info("ResearchEnhancementAgent initialized for %d projects.", len(self.projects))

    def _get_project_pdf_path(self, project_name: str) -> str:
        project_dir = os.path.join(Config.OVERLEAF_DIR, project_name)
        if os.path.exists(project_dir):
            for root, _, files in os.walk(project_dir):
                for file in files:
                    if file.endswith('.pdf'):
                        return os.path.join(root, file)
        return None

    def _get_stanford_state(self, project_name: str) -> dict:
        """Retrieves Stanford status from SQLite database."""
        if not self.db:
            return {"status": "READY_FOR_UPLOAD", "last_upload_time": None}
            
        try:
            state = self.db.get_project_state(project_name)
            if state:
                return {
                    "status": state.get('stanford_status') or "READY_FOR_UPLOAD",
                    "last_upload_time": state.get('last_upload_time')
                }
        except Exception as e:
            self.logger.warning("Could not read DB state for %s: %s", project_name, str(e))
            
        return {"status": "READY_FOR_UPLOAD", "last_upload_time": None}

    def _update_stanford_state(self, project_name: str, status: str, upload_time: str = None):
        """Updates Stanford status in SQLite database."""
        if not self.db:
            return
            
        try:
            if upload_time:
                self.db.update_project_state(project_name, stanford_status=status, last_upload_time=upload_time)
            else:
                self.db.update_project_state(project_name, stanford_status=status)
        except Exception as e:
            self.logger.error("Failed to update DB state for %s: %s", project_name, str(e))

    def upload_to_stanford(self, project_name: str, pdf_path: str) -> bool:
        """Phase 1: Uploads the PDF to paperreview.ai using Playwright."""
        if not pdf_path or not os.path.exists(pdf_path):
            self.logger.error("Invalid PDF path provided for upload: %s", pdf_path)
            return False
            
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
            mail = imaplib.IMAP4_SSL(Config.IMAP_SERVER, Config.IMAP_PORT)
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
                            
                            subject = str(msg.get("Subject", "")).replace('\r', '').replace('\n', '')
                            clean_proj_name = project_name.strip().lower()
                            
                            if clean_proj_name not in subject.lower():
                                continue
                                
                            print(f"   📧 Found matching email subject: {subject}")
                            
                            body = ""
                            if msg.is_multipart():
                                for part in msg.walk():
                                    content_type = part.get_content_type()
                                    if content_type in ["text/plain", "text/html"]:
                                        try:
                                            body += part.get_payload(decode=True).decode(errors='ignore') + " "
                                        except Exception:
                                            pass
                            else:
                                body = msg.get_payload(decode=True).decode(errors='ignore')
                            
                            clean_body = re.sub(r'<[^>]+>', ' ', body)
                            clean_body = re.sub(r'\s+', ' ', clean_body)
                            
                            match = re.search(r'Your Access Token:\s*([^\s]{20,})', clean_body, re.IGNORECASE)
                            if match:
                                token = match.group(1).strip()
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
        if not token or str(token).strip() == "":
            self.logger.error("Empty token provided to Stanford scraper.")
            return None
            
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
                try:
                    page.locator('text="Summary"').first.wait_for(state="visible", timeout=15000)
                except:
                    print("   ⚠️ Timed out waiting for 'Summary'. Checking for 'Invalid Token' error...")
                    if page.locator('text="Invalid Token"').count() > 0 or page.locator('text="Error"').count() > 0:
                        print("   ❌ Error: The website rejected the token.")
                        return None
                
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
        if not review_text or str(review_text).strip() == "":
            self.logger.error("Empty review text provided for task generation.")
            return None
            
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
            save_dir = os.path.join(Config.LIBRARY_DIR, "project_enhancement", safe_name)
            os.makedirs(save_dir, exist_ok=True)
            save_path = os.path.join(save_dir, "stanford_tasks.md")
            
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(tasks)
                
            print(f"   ✅ Tasks generated and saved to {save_path}")
            return tasks
        except RuntimeError as e:
            self.logger.error("LLM Generation failed: %s", str(e))
            return "⚠️ *System Note: The AI assistant was unable to generate actionable tasks at this time due to a temporary connection issue. Please review the raw feedback manually.*"

    def run(self):
        self.logger.info("Starting Research Enhancement cycle.")
        for project in self.projects:
            print(f"\n{'-'*40}\n🧠 Stanford Peer-Review Engine: {project}\n{'-'*40}")
            
            state = self._get_stanford_state(project)
            
            if state["status"] == "READY_FOR_UPLOAD":
                pdf_path = self._get_project_pdf_path(project)
                if not pdf_path:
                    self.logger.warning("No PDF found for %s. Cannot upload.", project)
                    continue
                    
                success = self.upload_to_stanford(project, pdf_path)
                if success:
                    self._update_stanford_state(project, "WAITING_FOR_REVIEW", datetime.now().isoformat())
                    self.logger.info("✅ Project state changed to WAITING_FOR_REVIEW in DB.")
                    
            elif state["status"] == "WAITING_FOR_REVIEW":
                if state.get("last_upload_time"):
                    try:
                        upload_dt = datetime.fromisoformat(state["last_upload_time"])
                        hours_waiting = (datetime.now() - upload_dt).total_seconds() / 3600
                        if hours_waiting > 48:
                            self.logger.warning(
                                "Project '%s' has been WAITING_FOR_REVIEW for %.1f hours. Sending alert.",
                                project, hours_waiting
                            )
                            self.notifier.send_admin_alert(
                                subject=f"Stanford Review Stuck: {project}",
                                message=f"Project '{project}' has been waiting for a Stanford review token for {hours_waiting:.0f} hours. Manual intervention may be required."
                            )
                            self._update_stanford_state(project, "READY_FOR_UPLOAD")
                            continue
                    except Exception as e:
                        self.logger.warning("Could not parse upload time for %s: %s", project, str(e))
                print("⏳ Project is waiting for review. Initiating Phase 2 (IMAP Check)...")
                token = self._get_stanford_token_from_email(project)
                
                if token:
                    review_text = self._fetch_review_from_stanford(token)
                    if review_text:
                        tasks = self._generate_actionable_tasks(project, review_text)
                        if tasks is not None and tasks.strip():
                            self._update_stanford_state(project, "REVIEW_COMPLETED")
                            print("✅ Phase 2 complete. Tasks generated and DB state updated to REVIEW_COMPLETED.")
                            
                            self.logger.info("Sending Stanford task list email for %s...", project)
                            self.notifier.send_stanford_tasks(
                                project_name=project,
                                md_content=tasks
                            )
                else:
                    print("   ⏭️ Review email not yet received or token not found. Will try again next run.")
                    
        self.logger.info("Research Enhancement cycle completed.")