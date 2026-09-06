import os
import re
import difflib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import the centralized configuration
from config import Config
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from utils.overleaf_connector import OverleafConnector
from agents.notification_agent import NotificationAgent

class ProgressTrackingAgent(BaseAgent):
    """
    Agent responsible for tracking progress in Overleaf projects.
    It utilizes a 'Delta Memory' system managed via SQLite to only analyze 
    newly added or modified text, saving LLM tokens.
    """
    
    def __init__(self, overleaf_projects: list, notifier: NotificationAgent, db=None):
        super().__init__(agent_name="ProgressTrackingAgent")
        self.overleaf_projects = overleaf_projects
        self.library = LibraryManager()
        self.connector = OverleafConnector()
        self.notifier = notifier 
        
        # Dependency Injection for Database
        self.db = db 
        
        self.logger.info("ProgressTrackingAgent initialized with %d projects.", len(self.overleaf_projects))

    def _get_last_seen_text(self, project: str) -> str:
        """Retrieves the text from the previous run directly from SQLite."""
        if not self.db:
            self.logger.warning("No DB connection. Cannot retrieve last seen text for %s.", project)
            return ""
            
        try:
            state = self.db.get_project_state(project)
            if state and state.get('last_seen_text'):
                return state['last_seen_text']
        except Exception as e:
            self.logger.error("Failed to fetch last seen text from DB for %s: %s", project, str(e))
            
        return ""

    def _save_current_text(self, project: str, text: str):
        """Saves the current text to the SQLite database for future comparisons."""
        if not self.db:
            self.logger.warning("No DB connection. Cannot save current text for %s.", project)
            return
            
        try:
            self.db.update_project_state(project, last_seen_text=text)
        except Exception as e:
            self.logger.error("Failed to save current text state to DB for %s: %s", project, str(e))

    @staticmethod
    def _normalize_lines(text: str) -> list:
        """Collapse internal whitespace per line so LaTeX reflow doesn't trigger false deltas."""
        return [re.sub(r'\s+', ' ', l).strip() for l in text.splitlines() if l.strip()]

    def _extract_delta(self, old_text: str, new_text: str) -> str:
        """Compares old and new text and extracts ONLY the newly added or modified lines."""
        if not old_text.strip() and new_text.strip():
            return new_text

        old_lines = self._normalize_lines(old_text)
        new_lines = self._normalize_lines(new_text)

        diff = difflib.ndiff(old_lines, new_lines)
        added_lines = [line[2:] for line in diff if line.startswith('+ ')]
        return "\n".join(added_lines)

    def check_text_changes(self, project: str) -> dict:
        """
        Reads the actual text, compares it to DB memory, and extracts the Delta.
        """
        self.logger.info("Reading text from local Drop Folder for project: %s", project)

        project_path = os.path.join(self.connector.base_storage_path, project)
        # Deliberately not self.connector.read_all_tex_files() — editorial annotations
        # (see OverleafConnector.strip_editorial_annotations()'s docstring) must be
        # stripped from the RAW source before OverleafConnector.clean_latex_text()
        # unwraps them into indistinguishable plain text. This reproduces
        # read_all_tex_files()'s own raw-then-clean sequence with the strip step
        # inserted in between. The strip logic itself now lives on OverleafConnector
        # (shared with LiteratureResearchAgent and ResearchEnhancementAgent, which
        # need the identical protection) rather than duplicated here.
        #
        # Also deliberately not self.connector.read_all_tex_files_raw() — that method
        # blindly concatenates EVERY .tex file physically present anywhere under the
        # project directory, including stray/unreferenced files (e.g. a stale
        # old_version.tex left in a project after a rewrite) that aren't actually
        # part of the compiled manuscript. That real bug corrupted a real delta
        # calculation for the PQTrace project. read_manuscript_tex_files_raw()
        # instead resolves \input{...}/\include{...} starting from main.tex and only
        # reads files actually reachable from the root document (see its docstring
        # for the fallback behavior when main.tex is missing or self-contained).
        # notifier=self.notifier so an admin is alerted if this ever falls back to
        # the legacy read_all_tex_files_raw() behavior for a project whose root
        # document isn't main.tex — see read_manuscript_tex_files_raw's docstring.
        raw_text = self.connector.read_manuscript_tex_files_raw(project_path, notifier=self.notifier)
        raw_text = self.connector.strip_editorial_annotations(raw_text)
        current_text = self.connector.clean_latex_text(raw_text) if raw_text else ""

        if not current_text:
            self.logger.warning("No text found for %s.", project)
            return {"has_changes": False, "delta_text": ""}
            
        old_text = self._get_last_seen_text(project)
        
        if not old_text:
            self.logger.info("First time processing %s. The entire text is considered 'new'.", project)
            delta_text = current_text
        else:
            self.logger.info("Comparing current text to DB memory to extract Delta...")
            delta_text = self._extract_delta(old_text, current_text)
            
        self._save_current_text(project, current_text)
            
        if not delta_text.strip():
            self.logger.info("Text was modified, but no meaningful new additions were found.")
            return {"has_changes": False, "delta_text": ""}
            
        return {"has_changes": True, "delta_text": delta_text}

    @staticmethod
    def _number_delta_lines(delta_text: str) -> str:
        """Prefixes every non-blank line of the delta with a stable `[N]` marker before
        it's shown to the LLM, so recommendations can cite a precise location instead
        of a general summary. Deliberately computed from whatever delta_text is passed
        in — including an already-truncated delta (_process_project truncates to
        Config.MAX_DELTA_CHARS BEFORE calling analyze_delta) — so numbering always
        matches exactly what the LLM actually sees, regardless of how long the original
        delta was. `[N]` is delta-relative (line N of the new-additions excerpt in this
        analysis), not an absolute line number in the source .tex file: the diff this
        agent runs is against LaTeX-cleaned, whitespace-normalized text (see
        _normalize_lines), which has no stable mapping back to raw source line numbers
        — a delta-relative marker plus a verbatim quote (see analyze_delta's prompt) is
        the most precise anchor available without redesigning the diff engine itself."""
        numbered = []
        n = 0
        for line in delta_text.splitlines():
            if not line.strip():
                continue
            n += 1
            numbered.append(f"[{n}] {line}")
        return "\n".join(numbered)

    @staticmethod
    def _strip_leading_markers(text: str) -> str:
        """Defense-in-depth: if the LLM regresses to prefixing a bullet with a bare
        `[N]` reference marker (the exact pattern Amit flagged as meaningless to a
        reader who never sees the numbered source list — see analyze_delta's
        docstring), strip it rather than let it reach the student. This runs
        regardless of prompt compliance, so a location marker with zero narrative
        value can never leak into the email/saved feedback even if the model ignores
        the "never show [N]" instruction below."""
        cleaned_lines = []
        for line in text.splitlines():
            cleaned_lines.append(re.sub(r'^(\s*-?\s*)\[\d+\]\s*', r'\1', line))
        return "\n".join(cleaned_lines)

    def analyze_delta(self, delta_text: str, project: str = None) -> tuple:
        """Single LLM call returning (feedback, suggestions) — halves token usage vs two separate calls.

        project is used only to key the waterfall-exhaustion admin alert on RuntimeError
        (see BaseAgent._alert_waterfall_exhausted) — it does not affect the prompt or the
        degraded-output return value on failure.

        Recommendation format: each suggestion must read as ONE connected narrative
        sentence — "In the sentence about X, <issue>; <fix>. (near: "<quote>")" — not
        the earlier citation-then-comment format of a bare "[N]" marker followed by a
        detached quote and explanation. That earlier format is what a real student
        recipient (Amit, the department head reviewing PQTrace's actual output) called
        "disconnected from context": a `[N]` marker means nothing to a reader who never
        sees a numbered source list, and a short quote alone (e.g. a single stray word
        like "yellow") can't anchor context by itself. The numbered `[N]` lines are
        still shown to the LLM below — they remain useful so the model has a stable,
        unambiguous way to identify exactly which line it means internally — but the
        model is explicitly told never to surface a bare marker in its output; the
        location must instead be described in its own words from surrounding context,
        with the verbatim quote woven in as supporting, checkable evidence rather than
        a citation prefix. _strip_leading_markers is a defensive backstop for the same
        goal in case the model doesn't comply."""
        self.logger.info("Analyzing Delta (single LLM call for feedback + suggestions)...")
        numbered_delta = self._number_delta_lines(delta_text)
        prompt = f"""
        You are an expert academic reviewer and editor. Review the following NEW ADDITIONS or MODIFICATIONS to a research paper.
        Each line below is prefixed with a bracketed marker like [12] purely so YOU can
        keep track of exactly which line you mean — it is NOT a page or manuscript line
        number, and the student reading your feedback will NEVER see this numbered
        list. Never print a "[N]" token in your response.
        ---\n{numbered_delta}\n---

        Provide your response in EXACTLY this format (keep the headers):

        ### FEEDBACK
        A brief, constructive critique of these new changes regarding academic tone, clarity, and depth. Do not rewrite the text.

        ### SUGGESTIONS
        2-3 bullet points suggesting concrete, LOCATED improvements to phrasing and flow —
        not general statements. Each bullet must read as ONE connected, self-contained
        sentence (or two short sentences) that a non-specialist could follow without
        cross-referencing anything else. For EACH bullet:
        1. Open by describing WHERE the issue is, in your own words, drawn from the
           surrounding context you were shown — e.g. "In the sentence introducing
           quantum computing's threat to cryptography..." or "In the paragraph
           describing the synchronization protocol...". Never open with a bare "[N]"
           marker or line number — it is meaningless to the reader.
        2. Within that same sentence, weave in a short verbatim quote (5-15 words)
           copied exactly from that spot, as supporting evidence — e.g. "...(near:
           \"the development of practical quantum computing...\")..." — so the student
           can confirm the exact location with a text search. Do not put the quote
           first, as a prefix; it must read as evidence inside the sentence.
        3. In that same connected sentence or the one right after it, state the
           specific issue and what to do about it. Do not rewrite the text yourself.
        Do NOT split the location, the quote, and the explanation into separate,
        disconnected parts. Format each bullet as: "- <connected narrative sentence(s)>".
        """
        try:
            response = self.ask_llm(prompt)
            parts = response.split("### SUGGESTIONS", 1)
            if len(parts) == 2:
                feedback = parts[0].replace("### FEEDBACK", "").strip()
                suggestions = parts[1].strip()
            else:
                feedback = response.strip()
                suggestions = ""
            suggestions = self._strip_leading_markers(suggestions)
            print(f"\n📝 Focused Feedback on new changes:\n{feedback}\n")
            print(f"\n💡 Targeted Suggestions on new changes:\n{suggestions}\n")
            return feedback, suggestions
        except RuntimeError as e:
            # System-wide policy (see agents/base_agent.py module docstring "LLM
            # FAILURE POLICY"): on waterfall exhaustion, a call site producing
            # end-user-facing content must signal failure with a sentinel the
            # caller cannot mistake for real content — never a placeholder string,
            # since a truthy "unable to generate feedback" string reads exactly
            # like real feedback to a student and was previously being saved and
            # emailed unconditionally regardless of this failure. Returning
            # (None, None) forces _process_project to actually check for failure
            # rather than silently shipping degraded output.
            self.logger.error("LLM failed to generate analysis: %s", str(e))
            if project:
                self._alert_waterfall_exhausted("delta analysis", project)
            return None, None

    def _get_writing_velocity(self, project: str, days: int = 7) -> str:
        """
        Returns a human-readable velocity string: chars/day over the last N days.
        Returns 'N/A' if insufficient data or no DB.
        """
        if not self.db:
            return "N/A"
        try:
            snapshots = self.db.get_project_snapshots(project, days=days)
            active = [s for s in snapshots if s.get('had_changes')]
            if not active:
                return "0 chars/day"
            total_chars = sum(s.get('delta_char_count', 0) for s in active)
            velocity = total_chars / days
            return f"{velocity:.0f} chars/day"
        except Exception as e:
            self.logger.warning("Could not compute velocity for %s: %s", project, str(e))
            return "N/A"

    def _process_project(self, project: str):
        """Per-project logic extracted so ThreadPoolExecutor can run projects in parallel."""
        if self.db:
            self.db.log_agent_run(
                agent_name=self.agent_name,
                project_name=project,
                status="STARTED",
                started_at=datetime.now().isoformat()
            )
        print(f"\n{'-'*40}\n📂 Evaluating Project Updates: {project}\n{'-'*40}")
        velocity = self._get_writing_velocity(project)
        self.logger.info("Writing velocity for '%s' (last 7 days): %s", project, velocity)

        old_text_before_run = self._get_last_seen_text(project)

        changes = self.check_text_changes(project)
        has_changes = changes.get("has_changes", False)
        delta_text = changes.get("delta_text", "")

        if self.db:
            delta_char_count = len(delta_text) if has_changes else 0
            self.db.add_progress_snapshot(
                project_name=project,
                had_changes=has_changes,
                delta_char_count=delta_char_count
            )

        if has_changes:
            is_first_run = not old_text_before_run or old_text_before_run.strip() == ""

            if is_first_run:
                self.logger.info(
                    "First run for '%s' — baseline established. Skipping feedback email.", project
                )
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=project,
                        status="SUCCESS",
                        finished_at=datetime.now().isoformat()
                    )
                return

            min_delta = getattr(Config, 'MIN_DELTA_CHARS', 50)
            if len(delta_text.strip()) < min_delta:
                self.logger.info("Delta too small to process (<%d chars). Skipping feedback.", min_delta)
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=project,
                        status="SUCCESS",
                        finished_at=datetime.now().isoformat()
                    )
                return

            # Groq on_demand tier: 12k TPM. Single merged call uses ~4k tokens max.
            MAX_DELTA_CHARS = getattr(Config, 'MAX_DELTA_CHARS', 8000)
            if len(delta_text) > MAX_DELTA_CHARS:
                self.logger.info(
                    "Delta too large (%d chars). Truncating to %d chars for LLM.",
                    len(delta_text), MAX_DELTA_CHARS
                )
                delta_text = delta_text[:MAX_DELTA_CHARS] + "\n\n[... truncated ...]"

            feedback, suggestions = self.analyze_delta(delta_text, project)

            if feedback is None:
                # LLM failure policy: analyze_delta signals waterfall exhaustion via
                # (None, None) (admin already alerted inside analyze_delta). Do not
                # save or email a placeholder as if it were real feedback — skip
                # this cycle entirely and let the next scheduled run try again
                # against the delta it will compute at that point.
                self.logger.warning(
                    "Skipping feedback save/email for %s this cycle: analysis unavailable "
                    "(LLM waterfall exhausted).", project
                )
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=project,
                        status="FAILURE",
                        finished_at=datetime.now().isoformat()
                    )
                return

            self.library.save_tracking_feedback(project, feedback, suggestions)
            self.logger.info("Saved focused feedback and suggestions for %s.", project)

            combined_md = f"### 📝 Progress Feedback\n{feedback}\n\n### 💡 Targeted Suggestions\n{suggestions}"
            self.logger.info("Sending progress feedback email for %s...", project)
            self.notifier.send_progress_feedback(
                project_name=project,
                md_content=combined_md
            )
        else:
            self.logger.info("No actionable new text found for %s. Skipping LLM analysis.", project)

        if self.db:
            self.db.log_agent_run(
                agent_name=self.agent_name,
                project_name=project,
                status="SUCCESS",
                finished_at=datetime.now().isoformat()
            )

    def run(self):
        self.logger.info("Starting the progress tracking cycle.")
        max_workers = getattr(Config, 'PROGRESS_MAX_WORKERS', 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._process_project, p): p for p in self.overleaf_projects}
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
                                subject=f"ProgressTrackingAgent — Project Failed: {project}",
                                message=(
                                    f"Unhandled error while processing '{project}':\n\n{e}\n\n"
                                    f"See ProgressTrackingAgent.log for the full traceback."
                                )
                            )
                        except Exception:
                            pass  # do not let alert failure mask the original error
        self.logger.info("Progress tracking cycle completed.")