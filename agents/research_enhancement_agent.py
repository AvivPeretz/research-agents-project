import os
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
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
        self.connector = OverleafConnector()

        # --- Dependency Injection for Database ---
        self.db = db

        # Use Config for credentials
        self.uni_email = Config.OVERLEAF_EMAIL

        # Cross-project circuit breaker: projects are processed concurrently via
        # ThreadPoolExecutor, and Stanford has no API to give us a clean rate-limit
        # signal from — only "did an upload succeed." If Stanford is failing
        # systemically this run, we'd otherwise launch a full Playwright browser
        # session (15-30s) per project only to watch it fail the same way each time.
        self._stanford_lock = threading.Lock()
        self._stanford_consecutive_failures = 0
        self._stanford_cooldown_until = 0.0

        self.logger.info("ResearchEnhancementAgent initialized for %d projects.", len(self.projects))

    def _stanford_cooldown_remaining(self) -> float:
        with self._stanford_lock:
            until = self._stanford_cooldown_until
        return max(0.0, until - time.time())

    def _record_stanford_outcome(self, success: bool):
        """Tracks consecutive Stanford upload failures across ALL projects in this
        run. After Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD in a row, Stanford
        is assumed to be down/unreachable for the rest of this run and further
        projects skip the browser attempt entirely."""
        with self._stanford_lock:
            if success:
                self._stanford_consecutive_failures = 0
                self._stanford_cooldown_until = 0.0
                return
            self._stanford_consecutive_failures += 1
            if self._stanford_consecutive_failures >= Config.STANFORD_CONSECUTIVE_FAILURE_THRESHOLD:
                self._stanford_cooldown_until = time.time() + Config.LLM_RATE_LIMIT_COOLDOWN_SECONDS
                self.logger.error(
                    "Stanford has failed %d times in a row this run — assuming it's "
                    "unreachable and skipping further upload attempts for %.0fs.",
                    self._stanford_consecutive_failures, Config.LLM_RATE_LIMIT_COOLDOWN_SECONDS
                )

    def _get_project_pdf_path(self, project_name: str) -> str:
        safe_name = project_name.replace(" ", "_")
        project_dir = os.path.join(Config.OVERLEAF_DIR, project_name)
        # Look for the canonical PDF saved by DataIngestionAgent first
        direct_path = os.path.join(project_dir, f"{safe_name}.pdf")
        if os.path.exists(direct_path):
            return direct_path
        # Fallback: walk for any .pdf (covers edge cases)
        if os.path.exists(project_dir):
            for root, _, files in os.walk(project_dir):
                for file in files:
                    if file.endswith('.pdf'):
                        return os.path.join(root, file)
        return None

    def _get_stanford_state(self, project_name: str) -> dict:
        """Retrieves Stanford status from SQLite database."""
        if not self.db:
            return {"status": "READY_FOR_UPLOAD", "last_upload_time": None, "token": None}

        try:
            state = self.db.get_project_state_slim(project_name)
            if state:
                return {
                    "status": state.get('stanford_status') or "READY_FOR_UPLOAD",
                    "last_upload_time": state.get('last_upload_time'),
                    "token": state.get('stanford_token'),
                    "upload_failures": state.get('stanford_upload_failures') or 0,
                }
        except Exception as e:
            self.logger.warning("Could not read DB state for %s: %s", project_name, str(e))

        return {"status": "READY_FOR_UPLOAD", "last_upload_time": None, "token": None, "upload_failures": 0}

    def _update_stanford_state(self, project_name: str, status: str, upload_time: str = None, token: str = None,
                                upload_failures: int = None) -> bool:
        """Updates Stanford status in SQLite database. Returns True on success, False
        on failure — callers persisting something irreplaceable (a review token) in
        the same breath as this call must check the return value rather than assume
        success just because nothing raised."""
        if not self.db:
            return True

        fields = {"stanford_status": status}
        if upload_time:
            fields["last_upload_time"] = upload_time
        if token:
            fields["stanford_token"] = token
        if upload_failures is not None:
            fields["stanford_upload_failures"] = upload_failures

        try:
            return bool(self.db.update_project_state(project_name, **fields))
        except Exception as e:
            self.logger.error("Failed to update DB state for %s: %s", project_name, str(e))
            return False

    def upload_to_stanford(self, project_name: str, pdf_path: str) -> str:
        """Phase 1: Uploads the PDF to paperreview.ai using Playwright.

        Returns the review access token scraped directly from the confirmation
        page's #tokenDisplay element, or None on failure. paperreview.ai warns
        that email delivery of this token is unreliable for some addresses, so
        the token is captured immediately rather than relying on an email later.
        """
        if not pdf_path or not os.path.exists(pdf_path):
            self.logger.error("Invalid PDF path provided for upload: %s", pdf_path)
            return None
            
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
                file_input.wait_for(state="attached", timeout=15000)
                file_input.set_input_files(pdf_path)

                print("📧 Entering the University email address...")
                email_input = page.locator('input[type="email"]')
                email_input.wait_for(state="visible", timeout=5000)
                email_input.fill(self.uni_email)

                print("🚀 Submitting the paper for review...")
                submit_button = page.locator('button:has-text("Submit"), button:has-text("Review"), button[type="submit"]').first
                submit_button.wait_for(state="visible", timeout=10000)
                submit_button.click()

                print("✅ Upload process finished! Waiting for server acknowledgement...")
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass  # networkidle timeout is non-fatal — page loaded but may still have background XHR

                # networkidle can fire before the client-side JS finishes rendering the success
                # panel (confirmed live: #tokenDisplay.count() is 0 immediately after networkidle
                # even on a successful submission) — wait for the element itself rather than
                # checking immediately.
                token_display = page.locator("#tokenDisplay")
                try:
                    token_display.wait_for(state="visible", timeout=10000)
                except Exception:
                    self.logger.error(
                        "Upload for '%s' did not show a confirmation token — submission likely failed.",
                        project_name
                    )
                    return None

                token = token_display.inner_text().strip()
                if not token:
                    self.logger.error("Token display was present but empty for '%s'.", project_name)
                    return None

                print(f"🔑 Captured review token from confirmation page (length: {len(token)})")
                return token

            except Exception as e:
                self.logger.error("Failed to upload to Stanford: %s", str(e))
                return None
            finally:
                context.close()
                browser.close()

    def _fetch_review_from_stanford(self, token: str) -> str:
        """Phase 2: Polls paperreview.ai's JSON API for the review content.

        Returns the review content string when ready (HTTP 200, success=true).
        Returns None when the review isn't ready yet, the token is invalid, or
        the request fails — all three are treated the same: try again next run.
        """
        if not token or str(token).strip() == "":
            self.logger.error("Empty token provided to Stanford review fetch.")
            return None

        print(f"   🌐 Checking paperreview.ai for review status (token length: {len(token)})...")
        try:
            response = requests.get(f"https://paperreview.ai/api/review/{token}", timeout=15)
        except requests.exceptions.RequestException as e:
            self.logger.error("Stanford review API request failed: %s", str(e))
            return None

        if response.status_code != 200:
            print(f"   ⏳ Review not ready yet (status {response.status_code}).")
            return None

        try:
            data = response.json()
        except ValueError as e:
            self.logger.error("Stanford review API returned invalid JSON: %s", str(e))
            return None

        if not data.get("success") or not data.get("content"):
            print("   ⏳ Review not ready yet (response missing content).")
            return None

        print(f"   ✅ Review ready! (Length: {len(data['content'])} characters)")
        return data["content"]

    def _generate_actionable_tasks(self, project_name: str, review_text: str, previous_review_text: str = None) -> str:
        """Phase 2c: Uses the LLM to turn Stanford's raw review into a prioritized,
        student-facing document — not a forwarded dump of the raw review.

        Amit's feedback (department head, primary output stakeholder) on this
        specifically: (1) the review needs to be tiered by severity/importance, not a
        flat list, and (2) it should read as something you'd hand directly to the
        student author, not an internal engineering/PM task table. Both are addressed
        in the REQUIRED OUTPUT FORMAT below.

        previous_review_text, when provided (a manuscript can go through multiple
        Stanford review cycles — see DatabaseManager.get_latest_stanford_review), adds
        a review-cycle comparison section: which previously-raised points appear to
        have been addressed in the new review. This is a comparison of REVIEW TEXT
        across Stanford cycles, not a manuscript diff — conceptually similar to
        ProgressTrackingAgent's delta-tracking but a genuinely different comparison
        (review-to-review, not text-to-text), so it isn't shared code with that agent.
        """
        if not review_text or str(review_text).strip() == "":
            self.logger.error("Empty review text provided for task generation.")
            return None

        print("   🧠 Sending Stanford review to LLM for task generation & novelty check...")

        comparison_block = ""
        if previous_review_text and str(previous_review_text).strip():
            comparison_block = f"""
        === PREVIOUS STANFORD REVIEW (from an earlier review cycle for this same project) ===
        {previous_review_text}
        === END PREVIOUS REVIEW ===

        This manuscript has been reviewed by Stanford before. Compare the CURRENT review
        (below) against the PREVIOUS one above and include a "## Progress Since Last Review"
        section (see REQUIRED OUTPUT FORMAT) that says, for each point raised in the
        previous review, whether it appears to have been addressed, partially addressed,
        or not addressed — based only on whether the current review still raises it,
        raises it more mildly, or no longer mentions it. Do not guess about the
        manuscript itself; judge only from what the two review texts say.
        """

        prompt = f"""
        You are an academic writing coach preparing peer-review feedback for a
        research student to read and act on directly. You have received an external
        peer-review from Stanford (paperreview.ai) for the project '{project_name}'.
        {comparison_block}
        Here is the CURRENT raw review text:
        ---
        {review_text}
        ---

        Read the entire review carefully and produce a Markdown document formatted for
        a student — clear, encouraging, organized with headers, no internal
        engineering/PM jargon. Use EXACTLY this structure:

        ## Novelty & Innovation
        2-4 sentences summarizing what the reviewer thought about the paper's
        originality and contribution, written to the student directly.
        {"## Progress Since Last Review" if previous_review_text and str(previous_review_text).strip() else ""}
        {"For each point the previous review raised: state it briefly, then say Addressed / Partially Addressed / Not Addressed, with one sentence of evidence from the current review's text." if previous_review_text and str(previous_review_text).strip() else ""}

        ## 🔴 Critical — Must Address Before Resubmission
        Numbered list. Issues that block acceptance: missing evaluation, unsupported
        claims, fundamental methodology gaps, missing baselines. Only include a genuine
        blocker here — do not inflate minor notes into this tier.

        ## 🟡 Important — Strengthens the Paper Significantly
        Numbered list. Real weaknesses that should be fixed but wouldn't alone block
        acceptance: missing related work, unclear methodology sections, insufficient
        ablations.

        ## 🟢 Minor — Polish & Cleanup
        Numbered list. Typos, formatting, missing citations, small clarity fixes.

        For EVERY task in all three tiers above, provide on the same or next line:
        - **What to do**: specific and concrete — avoid heavy LaTeX blocks if plain
          text/Unicode explains it cleanly.
        - **Estimated effort**: a real time estimate (e.g., "~2 hours", "~15 hours").
        - **Suggested deadline**: a specific day within the next 30-day sprint,
          scaled to effort (quick fixes first, heavy experimental work later).

        Assign each task to a tier based on what the REVIEW ITSELF says about its
        severity/impact on the paper's claims — do not default everything to one tier.
        If the review does not clearly justify any Critical-tier issue, it is fine for
        that section to be short or say "No blocking issues identified."
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
            self._alert_waterfall_exhausted("Stanford task-list generation", project_name)
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

        # Allocation: 40% intro, 35% body, 25% conclusion (sums to 100% of max_chars)
        intro_chars = int(max_chars * 0.40)
        body_chars = int(max_chars * 0.35)
        conclusion_chars = max_chars - intro_chars - body_chars  # remaining ~25%

        intro = paper_text[:intro_chars]
        mid_start = len(paper_text) // 2 - body_chars // 2
        body = paper_text[mid_start:mid_start + body_chars]
        conclusion = paper_text[-conclusion_chars:]

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

Organize the actionable task list into the SAME three severity tiers used across
this system's peer-review output (Stanford-sourced reviews use this identical
scheme), so a research team sees a consistent structure regardless of whether their
review came from Stanford or this internal fallback. Use EXACTLY this structure:

### 🔴 Critical — Must Address Before Resubmission
Numbered list. Issues that block acceptance: missing evaluation, unsupported
claims, fundamental methodology gaps, missing baselines. Only include a genuine
blocker here — do not inflate minor notes into this tier.

### 🟡 Important — Strengthens the Paper Significantly
Numbered list. Real weaknesses that should be fixed but wouldn't alone block
acceptance: missing related work, unclear methodology sections, insufficient
ablations.

### 🟢 Minor — Polish & Cleanup
Numbered list. Typos, formatting, missing citations, small clarity fixes.

For EVERY task in all three tiers, provide:
- **Task**: What specifically needs to be fixed or added
- **Effort Estimate**: Actual working time (e.g., "~3 hours", "~1 day")
- **Deadline**: Specific date within the next 30 days from today ({today_str})

Assign each task to a tier based on what the REVIEW ABOVE actually shows about its
severity/impact on the paper's claims — do not default everything to one tier. Be
concrete and surgical — avoid vague tasks like "improve the writing." If the review
does not clearly justify any Critical-tier issue, it is fine for that section to be
short or say "No blocking issues identified."
"""

    def _run_internal_review(self, project_name: str) -> bool:
        """
        Fallback peer review pipeline. Activates when Stanford pipeline fails.
        Produces a structured review using the internal LLM and existing literature data.
        Returns True on success, False on failure.
        """
        self.logger.info("Activating internal review fallback for project: %s", project_name)

        # Step 1: Get paper text via injected OverleafConnector, with the appendix
        # (if any) separated out. clean_latex_text() strips all \appendix / \section{}
        # markup down to bare words, so this split must happen upstream in the
        # connector — otherwise truncation below can't tell a real conclusion from
        # trailing appendix filler and may silently show the LLM the wrong one.
        project_path = os.path.join(Config.OVERLEAF_DIR, project_name)
        paper_text, appendix_text = self.connector.read_all_tex_files_split(project_path)

        # Step 2: Minimum text check (on the substantive body only — a paper padded
        # mostly with appendix content still shouldn't pass as "enough to review")
        min_len = getattr(Config, 'MIN_REVIEW_LENGTH', MINIMUM_REVIEW_LENGTH)
        if not paper_text or len(paper_text.strip()) < min_len:
            self.logger.warning(
                "Project '%s' has insufficient text (%d chars < %d minimum). "
                "Skipping internal review.", project_name, len(paper_text or ""), min_len
            )
            self._update_stanford_state(project_name, "SKIPPED_INSUFFICIENT_TEXT")
            return False

        # Step 3: Truncate paper text — the tail bucket is now guaranteed to be the
        # paper's actual conclusion/discussion, not an appendix.
        truncated_text = self._truncate_paper_text(paper_text)
        if appendix_text:
            self.logger.info(
                "Excluded %d chars of appendix content from internal review for '%s'.",
                len(appendix_text), project_name
            )

        # Step 4: Load related papers from CSV
        related_papers_str = self._load_related_papers_from_csv(project_name)

        # Step 5: Build prompt and call LLM (single call)
        prompt = self._build_internal_review_prompt(project_name, truncated_text, related_papers_str)
        try:
            review_content = self.ask_llm(prompt)
        except RuntimeError as e:
            self.logger.error("Internal review LLM call failed for %s: %s", project_name, str(e))
            self._alert_waterfall_exhausted("internal peer review", project_name)
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

    def _process_project(self, project: str):
        """Per-project logic run in parallel via ThreadPoolExecutor."""
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

        elif state["status"] == "READY_FOR_UPLOAD":
            pdf_path = self._get_project_pdf_path(project)
            if not pdf_path:
                self.logger.warning("No PDF found for %s. Cannot upload.", project)
            else:
                cooldown = self._stanford_cooldown_remaining()
                if cooldown > 0:
                    self.logger.warning(
                        "Stanford assumed down this run (%.0fs cooldown remaining) — "
                        "skipping browser upload for '%s' without attempting it.",
                        cooldown, project
                    )
                    token = None
                else:
                    token = self.upload_to_stanford(project, pdf_path)
                    self._record_stanford_outcome(success=bool(token))

                if token:
                    saved = self._update_stanford_state(
                        project, "WAITING_FOR_REVIEW", datetime.now().isoformat(),
                        token=token, upload_failures=0
                    )
                    if saved:
                        self.logger.info("✅ Project state changed to WAITING_FOR_REVIEW in DB.")
                    else:
                        # The upload succeeded and Stanford has already issued a token for
                        # it, but that token failed to persist. Without an alert, this is
                        # silent: state stays READY_FOR_UPLOAD, so the next run re-uploads
                        # the same manuscript to Stanford — burning another submission
                        # against exactly the rate limits Focus Area 3 is protecting.
                        self.logger.error(
                            "Stanford upload for '%s' succeeded but the DB write failed — "
                            "the review token was lost. Project will be re-uploaded to "
                            "Stanford next run.", project
                        )
                        if self.notifier:
                            try:
                                self.notifier.send_admin_alert(
                                    subject=f"Stanford Token Lost: {project}",
                                    message=(
                                        f"Project '{project}' was successfully uploaded to Stanford "
                                        f"and a review token was issued, but saving it to the database "
                                        f"failed. The project will be re-uploaded on the next scheduled "
                                        f"run, which may waste a Stanford submission unnecessarily. "
                                        f"Check database connectivity/disk space."
                                    )
                                )
                            except Exception:
                                pass
                else:
                    # Stanford's own upload endpoint can fail transiently (rate limits on
                    # longer papers, brief outages) as well as permanently. Treating every
                    # failure as permanent burns an LLM waterfall call on internal review
                    # for what may just be "try again next run". Only give up on Stanford
                    # after repeated consecutive failures.
                    failures = state.get("upload_failures", 0) + 1
                    if failures < Config.STANFORD_MAX_UPLOAD_RETRIES:
                        self.logger.warning(
                            "Stanford upload failed for '%s' (%d/%d) — will retry next scheduled run "
                            "before falling back to internal review.",
                            project, failures, Config.STANFORD_MAX_UPLOAD_RETRIES
                        )
                        self._update_stanford_state(project, "READY_FOR_UPLOAD", upload_failures=failures)
                    else:
                        self.logger.warning(
                            "Stanford upload failed for '%s' %d times in a row. Activating internal fallback.",
                            project, failures
                        )
                        self._update_stanford_state(project, "READY_FOR_UPLOAD", upload_failures=0)
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
                        return
                except Exception as e:
                    self.logger.warning("Could not parse upload time for %s: %s", project, str(e))

            token = state.get("token")
            if not token:
                self.logger.warning(
                    "Project '%s' is WAITING_FOR_REVIEW but has no stored token. Will try again next run.",
                    project
                )
                return

            print("⏳ Project is waiting for review. Checking paperreview.ai (Phase 2)...")
            review_text = self._fetch_review_from_stanford(token)
            if review_text:
                # Fetch whatever the most recent PRIOR review cycle was (if any) before
                # saving this one, so "previous" always means a genuinely earlier cycle.
                previous_review_text = self.db.get_latest_stanford_review(project) if self.db else None
                tasks = self._generate_actionable_tasks(project, review_text, previous_review_text=previous_review_text)
                if tasks is not None and tasks.strip():
                    self._update_stanford_state(project, "REVIEW_COMPLETED")
                    if self.db:
                        saved = self.db.save_stanford_review(project, review_text)
                        if not saved:
                            self.logger.warning(
                                "Failed to save Stanford review history for '%s' — the next "
                                "review cycle's comparison will be missing this cycle.", project
                            )
                    print("✅ Phase 2 complete. Tasks generated and DB state updated to REVIEW_COMPLETED.")
                    self.logger.info("Sending Stanford task list email for %s...", project)
                    self.notifier.send_stanford_tasks(
                        project_name=project,
                        md_content=tasks
                    )
            else:
                # Stanford themselves warn processing "can take hours or even longer" — a not-ready
                # review here is the normal case, not a failure. Keep waiting; the 48h timeout above
                # is the only trigger for giving up on Stanford.
                print("   ⏭️ Review not ready yet. Will try again next run.")

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

    def run(self):
        self.logger.info("Starting Research Enhancement cycle.")
        max_workers = getattr(Config, 'ENHANCEMENT_MAX_WORKERS', 3)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._process_project, p): p for p in self.projects}
            for future in as_completed(futures):
                project = futures[future]
                try:
                    future.result()
                except Exception as e:
                    self.logger.error("Unhandled error processing project '%s': %s", project, str(e))
                    if self.db:
                        self.db.log_agent_run(
                            agent_name=self.agent_name,
                            project_name=project,
                            status="FAILURE",
                            error_message=str(e),
                            finished_at=datetime.now().isoformat()
                        )
                    if self.notifier:
                        try:
                            self.notifier.send_admin_alert(
                                subject=f"ResearchEnhancementAgent — Project Failed: {project}",
                                message=(
                                    f"Unhandled error while processing '{project}':\n\n{e}\n\n"
                                    f"See ResearchEnhancementAgent.log for the full traceback."
                                )
                            )
                        except Exception:
                            pass  # do not let alert failure mask the original error
        self.logger.info("Research Enhancement cycle completed.")