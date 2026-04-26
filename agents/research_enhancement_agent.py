import os
import time
import imaplib
import email
import re
from datetime import datetime
from playwright.sync_api import sync_playwright
import pandas as pd

# Import the centralized configuration
from config import Config
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from utils.overleaf_connector import OverleafConnector
from agents.notification_agent import NotificationAgent

MINIMUM_REVIEW_LENGTH = 3000  # characters — papers shorter than this are skipped

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
            browser = p.chromium.launch(
                headless=Config.PLAYWRIGHT_HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
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
            browser = p.chromium.launch(
                headless=Config.PLAYWRIGHT_HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )
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

    def _load_related_papers_from_csv(self, project_name: str) -> str:
        """
        Reads the rolling literature CSV produced by LiteratureResearchAgent
        and formats the top rows as a readable string for the review prompt.
        Returns an empty string if the CSV does not exist or is unreadable.
        """
        try:
            safe_name = project_name.replace(" ", "_")
            csv_path = os.path.join(
                Config.LIBRARY_DIR, "comparison_tables", safe_name, f"{safe_name}_rolling_table.csv"
            )
            if not os.path.exists(csv_path):
                self.logger.warning(
                    "Rolling literature CSV not found for project '%s': %s", project_name, csv_path
                )
                return ""

            df = pd.read_csv(csv_path)
            df = df.head(8)

            lines = []
            for i, (_, row) in enumerate(df.iterrows(), start=1):
                title = row.get("paper name", "N/A")
                year = row.get("year published", "N/A")
                cited = row.get("cited", "N/A")
                source = row.get("source", "N/A")
                data_types = row.get("types of available data", "N/A")
                lines.append(
                    f"[{i}] Title: {title}\n"
                    f"    Year: {year} | Citations: {cited} | Venue: {source}\n"
                    f"    Data: {data_types}"
                )

            return "\n\n".join(lines)
        except Exception as e:
            self.logger.warning("Failed to load related papers CSV for '%s': %s", project_name, str(e))
            return ""

    def _truncate_paper_text(self, paper_text: str, max_chars: int = 8000) -> str:
        """
        Intelligently truncates long paper text to fit within LLM context.
        Preserves beginning (introduction/abstract), middle (methodology/results),
        and end (conclusion/discussion) of the paper.
        """
        if len(paper_text) <= max_chars:
            return paper_text

        # Allocation: 40% intro, 35% body, 25% conclusion
        intro_end = int(max_chars * 0.40)
        body_size = int(max_chars * 0.35)
        conclusion_start = int(max_chars * 0.25)

        intro = paper_text[:intro_end]
        mid_start = len(paper_text) // 2 - body_size // 2
        body = paper_text[mid_start:mid_start + body_size]
        conclusion = paper_text[-conclusion_start:]

        return (
            intro +
            "\n\n[... middle section truncated for brevity ...]\n\n" +
            body +
            "\n\n[... section truncated ...]\n\n" +
            conclusion
        )

    def _build_internal_review_prompt(self, project_name: str, paper_text: str, related_papers_str: str) -> str:
        """
        Builds the comprehensive single-call prompt that produces both
        a structured academic review AND an actionable task list.
        Designed to replicate the output quality of Stanford's paperreview.ai.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        return f"""You are an expert academic peer reviewer with deep knowledge across \
multiple scientific domains. Your role is to evaluate the research manuscript \
titled '{project_name}' with the same rigor and standards applied at top-tier \
academic conferences such as NeurIPS, ICLR, ICML, and leading scientific journals.

You must provide a structured, objective, and constructive review — not a summary.
Your goal is to help the authors improve their work, identify genuine weaknesses, \
and assess the paper's contribution to the scientific community.

=== RELATED WORK CONTEXT ===
The following recent papers were retrieved from academic databases and are relevant \
to this manuscript's topic. Use them to assess whether the authors have properly \
contextualized their work and whether their claims of novelty are justified:

{related_papers_str if related_papers_str else "No related papers available for this review."}

=== MANUSCRIPT TEXT ===
{paper_text}

=== EVALUATION DIMENSIONS ===
Evaluate the manuscript across exactly 7 dimensions, each scored 1-5:

1. Originality (1-5)
   1=Incremental/nearly identical to prior work, 3=Meaningful extension, 5=Genuinely novel

2. Importance of Research Question (1-5)
   1=Narrow/limited impact, 3=Relevant to field, 5=Fundamental/broad implications

3. Support of Claims (1-5)
   1=Major claims unsubstantiated, 3=Most claims supported with minor gaps, 5=All claims rigorously supported

4. Soundness of Experiments/Methodology (1-5)
   1=Flawed design/missing baselines, 3=Adequate with limitations, 5=Rigorous/reproducible

5. Clarity of Writing (1-5)
   1=Difficult to follow, 3=Acceptable with confusing sections, 5=Exceptionally clear

6. Value to Research Community (1-5)
   1=Unlikely to influence future research, 3=Useful contribution, 5=Likely reference paper

7. Contextualization Relative to Prior Work (1-5)
   1=Ignores/misrepresents related work, 3=Covers main refs, 5=Comprehensive and fair

SCORING RULES:
- Never give all 5s. A perfect paper does not exist.
- Never give all 1s unless the paper is fundamentally broken.
- Each score must be explicitly justified with a specific reference to the manuscript.
- Do not summarize — evaluate.

=== REQUIRED OUTPUT FORMAT ===
Produce your response in EXACTLY this structure:

## Summary
(3-5 sentences: what the paper does, its main contribution, core methodology — \
written as a reviewer, NOT copied from the abstract)

## Strengths
(3-5 bullet points, each referencing a specific section, result, or claim)

## Weaknesses
(4-7 bullet points. Each must state: what the issue is, where it appears, \
why it matters for the paper's claims)

## Detailed Comments

### On Originality and Novelty
(Compare contributions against the related work provided above. \
Are novelty claims justified? Is there prior work missed or misrepresented?)

### On Methodology and Experiments
(Evaluate experimental design. Are baselines appropriate? Is evaluation fair? \
Are results reproducible? List missing ablations.)

### On Clarity and Presentation
(Identify specific sections, figures, or equations that are unclear. \
Be specific — never write "the paper is well-written" without evidence.)

### On Related Work
(List important missing papers from bibliography. Reference the related work \
context provided above explicitly.)

## Scores

| Dimension | Score (1-5) | Justification |
|---|---|---|
| Originality | X/5 | one sentence |
| Importance of Research Question | X/5 | one sentence |
| Support of Claims | X/5 | one sentence |
| Soundness of Experiments | X/5 | one sentence |
| Clarity of Writing | X/5 | one sentence |
| Value to Research Community | X/5 | one sentence |
| Contextualization of Prior Work | X/5 | one sentence |
| **Overall Score** | **X/5** | weighted average |

## Recommendation
(Choose exactly one and justify in 2-3 sentences referencing critical issues/strengths)
[ ] Accept as-is
[ ] Minor Revision
[ ] Major Revision
[ ] Reject and Resubmit
[ ] Reject

## Questions for the Authors
(3-5 specific, genuine questions for rebuttal/revision)

---

## Action Plan for Research Team

Based on the review above, generate a prioritized, actionable task list \
for the research team. For EACH task provide:
- **Task**: What specifically needs to be fixed or added
- **Effort Estimate**: Actual working time (e.g., "~3 hours", "~1 day")
- **Deadline**: Specific date within the next 30 days from today ({today_str})
- **Priority**: High / Medium / Low

Format as a numbered markdown list. Be concrete and surgical — \
avoid vague tasks like "improve the writing."
"""

    def _run_internal_review(self, project_name: str) -> bool:
        """
        Fallback peer review pipeline. Activates when Stanford pipeline fails.
        Produces a structured review using the internal LLM and existing literature data.
        Returns True on success, False on failure.
        """
        self.logger.info("Activating internal review fallback for project: %s", project_name)

        # Step 1: Get paper text via OverleafConnector
        connector = OverleafConnector()
        project_path = os.path.join(Config.OVERLEAF_DIR, project_name)
        paper_text = connector.read_and_clean_tex_file(project_path, "main.tex")

        # Step 2: Minimum text check
        if not paper_text or len(paper_text.strip()) < MINIMUM_REVIEW_LENGTH:
            self.logger.warning(
                "Project '%s' has insufficient text (%d chars < %d minimum). "
                "Skipping internal review.", project_name, len(paper_text or ""), MINIMUM_REVIEW_LENGTH
            )
            self._update_stanford_state(project_name, "SKIPPED_INSUFFICIENT_TEXT")
            return False

        # Step 3: Truncate paper text
        truncated_text = self._truncate_paper_text(paper_text)

        # Step 4: Load related papers from CSV
        related_papers_str = self._load_related_papers_from_csv(project_name)

        # Step 5: Build prompt and call LLM (single call)
        prompt = self._build_internal_review_prompt(project_name, truncated_text, related_papers_str)
        try:
            review_content = self.ask_llm(prompt)
        except RuntimeError as e:
            self.logger.error("Internal review LLM call failed for %s: %s", project_name, str(e))
            return False

        # Step 6: Save to same path Stanford would use
        safe_name = project_name.replace(" ", "_")
        save_dir = os.path.join(Config.LIBRARY_DIR, "project_enhancement", safe_name)
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, "stanford_tasks.md")
        header = f"# Internal Peer Review — {project_name}\n\n"
        header += "_Note: This review was generated by the internal fallback pipeline "
        header += "(Stanford pipeline was unavailable)._\n\n---\n\n"
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(header + review_content)

        # Step 7: Update DB status
        self._update_stanford_state(project_name, "INTERNAL_REVIEW_COMPLETED")

        # Step 8: Send email via existing notifier
        self.notifier.send_stanford_tasks(
            project_name=project_name,
            md_content=header + review_content
        )

        # Step 9: Return True
        self.logger.info("Internal review completed successfully for %s.", project_name)
        return True

    def run(self):
        self.logger.info("Starting Research Enhancement cycle.")
        for project in self.projects:
            if self.db:
                self.db.log_agent_run(
                    agent_name=self.agent_name,
                    project_name=project,
                    status="STARTED",
                    started_at=datetime.now().isoformat()
                )
            print(f"\n{'-'*40}\n🧠 Stanford Peer-Review Engine: {project}\n{'-'*40}")

            state = self._get_stanford_state(project)

            if state["status"] == "SKIPPED_INSUFFICIENT_TEXT":
                self.logger.info("Project '%s' was previously skipped (insufficient text). Skipping.", project)
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=project,
                        status="SUCCESS",
                        finished_at=datetime.now().isoformat()
                    )
                continue

            if state["status"] == "READY_FOR_UPLOAD":
                pdf_path = self._get_project_pdf_path(project)
                if not pdf_path:
                    self.logger.warning("No PDF found for %s. Cannot upload.", project)
                    if self.db:
                        self.db.log_agent_run(
                            agent_name=self.agent_name,
                            project_name=project,
                            status="SUCCESS",
                            finished_at=datetime.now().isoformat()
                        )
                    continue
                    
                success = self.upload_to_stanford(project, pdf_path)
                if success:
                    self._update_stanford_state(project, "WAITING_FOR_REVIEW", datetime.now().isoformat())
                    self.logger.info("✅ Project state changed to WAITING_FOR_REVIEW in DB.")
                else:
                    self.logger.warning("Stanford upload failed for '%s'. Activating internal fallback.", project)
                    self._run_internal_review(project)
                    
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
                            if self.db:
                                self.db.log_agent_run(
                                    agent_name=self.agent_name,
                                    project_name=project,
                                    status="SUCCESS",
                                    finished_at=datetime.now().isoformat()
                                )
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
                        self.logger.warning(
                            "Stanford review fetch returned empty for '%s'. Activating internal fallback.", project
                        )
                        self._run_internal_review(project)
                else:
                    print("   ⏭️ Review email not yet received or token not found. Will try again next run.")

            elif state["status"] in ("REVIEW_COMPLETED", "INTERNAL_REVIEW_COMPLETED"):
                self.logger.info(
                    "Project '%s' review is already complete (status: %s). Skipping.",
                    project, state["status"]
                )

            if self.db:
                self.db.log_agent_run(
                    agent_name=self.agent_name,
                    project_name=project,
                    status="SUCCESS",
                    finished_at=datetime.now().isoformat()
                )

        self.logger.info("Research Enhancement cycle completed.")