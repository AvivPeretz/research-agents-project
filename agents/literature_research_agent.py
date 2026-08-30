import os
import json
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from pydantic import ValidationError
from domain.schemas import LiteratureReport
import re
import requests
from urllib.parse import urlparse
# Import the centralized configuration
from config import Config
from agents.base_agent import BaseAgent
from utils.library_manager import LibraryManager
from agents.notification_agent import NotificationAgent

# NEW: Import our dedicated fetcher
from utils.literature_fetcher import LiteratureFetcher
from utils.overleaf_connector import OverleafConnector
from utils.token_budget import truncate_paper_abstracts

class RelevanceFilterExhausted(Exception):
    """Raised by _filter_relevant_papers when the LLM relevance filter itself
    waterfall-exhausts (all providers unavailable). Distinct from a normal
    RuntimeError so _process_project can tell "filtering failed, do not
    proceed with unfiltered data" apart from any other RuntimeError that might
    otherwise be caught generically elsewhere in the call chain."""
    pass


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
        self.connector = OverleafConnector()

        self.db = db
        self.logger.info("LiteratureResearchAgent initialized with %d projects.", len(self.projects))

    # _alert_waterfall_exhausted and its instance-level dedup guard
    # (_waterfall_exhausted_alerted) now live on BaseAgent — promoted there once
    # ProgressTrackingAgent and ResearchEnhancementAgent needed the identical
    # per-project-per-run alert-on-waterfall-exhaustion pattern. Behavior for this
    # agent is unchanged: same dedup key (project_name), same guarded call sites below.

    def _read_project_text(self, project_name: str) -> str:
        """Reads all .tex files for the project and returns a structure-aware sample."""
        project_dir = os.path.join(Config.OVERLEAF_DIR, project_name)
        raw_text = self.connector.read_all_tex_files_raw(project_dir)
        if not raw_text:
            self.logger.warning("No valid LaTeX text extracted for project: %s", project_name)
            return ""
        # Strip informal editorial/reviewer notes (e.g. \hl{...}) before this text
        # feeds keyword extraction — otherwise a collaborator's inline comment (not
        # manuscript content) can pollute the search queries this agent generates.
        # Must happen on raw text, before extract_representative_sample()'s internal
        # clean_latex_text() calls unwrap \hl{...} into indistinguishable plain text
        # — see OverleafConnector.strip_editorial_annotations()'s docstring.
        raw_text = self.connector.strip_editorial_annotations(raw_text)
        max_chars = getattr(Config, 'MAX_PROJECT_TEXT_CHARS', 4000)
        sample = self.connector.extract_representative_sample(raw_text, max_chars)
        if len(raw_text) > max_chars:
            self.logger.info(
                "Sampled project text to %d chars for LLM (original: %d chars).",
                len(sample), len(raw_text)
            )
        return sample

    # Preambles the cheap/fast extraction model sometimes prepends despite being told
    # not to (e.g. "Based on the provided excerpt from the academic manuscript...").
    # A real keyword line is short; a sentence is not — both checks catch this.
    _KEYWORD_PREAMBLE_PREFIXES = (
        "based on", "here are", "here is", "the topic", "the method",
        "topic query", "method query", "sure", "certainly", "these are",
    )
    _MAX_KEYWORD_LINE_CHARS = 100

    @classmethod
    def _parse_keyword_lines(cls, response: str) -> list:
        """Extracts usable keyword lines from an LLM response, dropping any line that
        looks like prose (a preamble/explanation) rather than a short keyword list."""
        lines = [l.strip().replace('"', '').replace("'", "") for l in response.splitlines() if l.strip()]
        usable = [
            l for l in lines
            if len(l) <= cls._MAX_KEYWORD_LINE_CHARS
            and not l.lower().startswith(cls._KEYWORD_PREAMBLE_PREFIXES)
        ]
        return usable

    def extract_keywords_from_text(self, project_name: str, text: str) -> tuple[str, str]:
        """Returns (topic_keywords, method_keywords) derived from manuscript text."""
        if not text or text.strip() == "":
            self.logger.warning("Empty text provided for keyword extraction. Using project name as fallback.")
            return project_name, project_name + " method"

        self.logger.info("Extracting keywords based on manuscript text for: %s", project_name)

        def _build_prompt(strict: bool = False) -> str:
            prompt = f"""
        Analyze the following excerpt from an academic manuscript titled '{project_name}':

        {text}

        Generate TWO distinct search queries for academic databases (Semantic Scholar):
        1. TOPIC query (3-5 keywords): the core research domain and problem being solved.
        2. METHOD query (3-5 keywords): the specific technique or algorithmic approach used.

        Return EXACTLY two lines, no labels, no quotes, no preamble or explanation:
        <topic keywords>
        <method keywords>
        """
            if strict:
                prompt += (
                    "\nIMPORTANT: Reply with ONLY the two keyword lines. Do not include "
                    "any introductory phrase like 'Based on the manuscript' or 'Here are'. "
                    "Do not explain your reasoning."
                )
            return prompt

        try:
            response = self.ask_llm(_build_prompt(), model_override=Config.LLM_EXTRACTION_MODEL_NAME).strip()
            lines = self._parse_keyword_lines(response)

            if len(lines) < 2:
                self.logger.warning(
                    "Keyword extraction for '%s' returned an unexpected format "
                    "(likely a preamble instead of keywords). Retrying with a stricter prompt.",
                    project_name
                )
                response = self.ask_llm(
                    _build_prompt(strict=True), model_override=Config.LLM_EXTRACTION_MODEL_NAME
                ).strip()
                lines = self._parse_keyword_lines(response)

            if len(lines) >= 2:
                return lines[0], lines[1]
            if lines:
                return lines[0], lines[0] + " methodology"

            self.logger.warning(
                "Keyword extraction for '%s' produced no usable lines after retry. "
                "Falling back to project name.", project_name
            )
            return project_name, project_name + " method"
        except RuntimeError as e:
            self.logger.error("LLM failed to generate keywords: %s", str(e))
            self._alert_waterfall_exhausted("keyword extraction", project_name)
            return project_name, project_name + " method"

    def _filter_relevant_papers(self, project_name: str, text: str, papers: list) -> list:
        """Drops papers whose abstract is clearly off-topic using a quick LLM relevance check."""
        if not papers:
            return papers

        abstracts = "\n".join(
            f"{i+1}. {p.get('title','?')}: {(p.get('snippet') or '')[:300]}"
            for i, p in enumerate(papers)
        )
        prompt = f"""
        Research project: '{project_name}'
        Project summary (first 500 chars): {text[:500]}

        Below are candidate papers (number: title: abstract snippet):
        {abstracts}

        Return ONLY a comma-separated list of the numbers that are clearly relevant to this project's topic.
        Example: 1,3,5,7
        If unsure, include the paper. Exclude only papers that are obviously off-topic.
        """
        try:
            response = self.ask_llm(prompt, model_override=Config.LLM_EXTRACTION_MODEL_NAME).strip()
            keep_indices = {int(x.strip()) - 1 for x in response.split(",") if x.strip().isdigit()}
            filtered = [p for i, p in enumerate(papers) if i in keep_indices]
            dropped = len(papers) - len(filtered)
            if dropped:
                self.logger.info("Relevance filter dropped %d off-topic papers for %s.", dropped, project_name)
            return filtered if filtered else papers  # never return empty if filter misfires
        except RuntimeError as e:
            # Full LLM waterfall exhausted. This used to be caught by the bare
            # `except Exception` below and silently fall through to "using all
            # papers" with only a WARNING log entry nobody watches. Confirmed in
            # production logs (2026-08-19) that this exact path fired for BOTH real
            # test projects (PQTrace, Udi Aharon PhD Book v2) during the run whose
            # output Amit reviewed — directly explaining the "irrelevant papers"
            # complaint: the relevance filter wasn't imperfect, it never ran at all
            # that cycle.
            #
            # An admin alert now fires here (as with every other waterfall-exhaustion
            # site in this codebase), but alerting alone isn't enough: silently
            # degrading to the full UNFILTERED paper list still let obviously
            # off-topic papers reach the CSV/email that cycle even after the alert
            # was added. Since relevance filtering genuinely requires an LLM call
            # (there's no substitute filter to fall back to), the only safe behavior
            # when it's unavailable is to skip this project's literature update for
            # the cycle entirely — same as the "all search sources failed" case
            # below — rather than proceed with data known to be unfiltered. Signal
            # this back to the caller via a distinguishable exception so
            # _process_project can skip the CSV write and email send.
            self.logger.error(
                "Relevance filter failed for %s: %s. Skipping this project's literature "
                "update for this cycle (no unfiltered fallback).", project_name, str(e)
            )
            self._alert_waterfall_exhausted("literature relevance filtering", project_name)
            raise RelevanceFilterExhausted(str(e)) from e
        except Exception as e:
            # Non-waterfall failure (e.g. an unexpected response-parsing edge case).
            # No admin alert here — unlike waterfall exhaustion this isn't necessarily
            # a systemic issue worth paging on every occurrence — but still degrades
            # to unfiltered results rather than dropping papers outright.
            self.logger.warning("Relevance filter failed for %s: %s. Using all papers.", project_name, str(e))
            return papers

    # ==========================================
    # Part C: standing link-validity filter
    # ==========================================
    # Root-caused a real dead/garbled link (a Google Books row titled "with
    # Post-quantum Cryptography" — a truncated scrape fragment of an unrelated
    # ARES 2026 proceedings book, PQTrace 2026-08-27 run) reaching the CSV/email
    # despite the relevance filter running successfully that cycle: relevance
    # judged on title/snippet text alone, which says nothing about whether the
    # link behind it actually resolves to real content. This is therefore a
    # separate, permanent validation layer — not a substitute for the relevance
    # filter above, and not conditional on it having failed.
    _LINK_CHECK_TIMEOUT_SECONDS = 12
    _LINK_CHECK_MIN_CONTENT_CHARS = 200
    _LINK_CHECK_MAX_WORKERS = 3
    _LINK_CHECK_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    # Loose "this is an error/parked/boilerplate page, not a real paper page"
    # signal. Deliberately not exhaustive NLP — a defensible heuristic in the
    # style already used elsewhere in this codebase (e.g. the keyword-preamble
    # detection above): catch the obvious cases, don't try to be perfect.
    _LINK_CHECK_BOILERPLATE_MARKERS = (
        "page not found", "404 not found", "410 gone", "content not available",
        "no longer available", "could not be found", "resource not found",
        "domain is for sale", "buy this domain",
    )
    # Domains whose paper pages are known, trusted, client-rendered SPAs that
    # legitimately return a 2xx with an empty/near-empty body to a plain scripted
    # request (confirmed live: a real, valid semanticscholar.org/paper/<id> URL
    # returns HTTP 202 with a 0-byte body to requests.get() — the page only
    # renders content client-side via JS, it never server-renders HTML). Since
    # Semantic Scholar is this agent's PRIMARY search source (see
    # utils/literature_fetcher.py), applying the body-content check to it as
    # written would exclude nearly every paper this pipeline finds through its
    # default path — defeating the filter's purpose rather than protecting it.
    # These domains are structurally trusted (they're the source API's own
    # canonical paper URLs, not third-party scrape targets), so a bare 2xx status
    # is treated as sufficient for them; the content-sniffing check still applies
    # to every other domain.
    _LINK_CHECK_SPA_TRUSTED_DOMAINS = ("semanticscholar.org",)

    def _paper_link_is_valid(self, paper: dict) -> bool:
        """Real HTTP check for a single paper's link. Fail-closed: any exception,
        timeout, non-2xx final status, or a thin/boilerplate-looking body excludes
        the paper rather than risk crashing the whole agent run over one bad or
        slow link. Known limitation (see literature_research_agent tests/report):
        some legitimate publisher domains front real papers with a bot-challenge
        page (e.g. Cloudflare "Just a moment...") that also returns a non-2xx
        status to a plain scripted request — this filter will exclude those too,
        the same trade-off explicitly specified for this check (status+content
        must both pass), not a bug in the implementation. See
        _LINK_CHECK_SPA_TRUSTED_DOMAINS above for the one deliberate exception to
        the content check, and why it exists."""
        url = (paper.get("link") or paper.get("url") or "").strip()
        title = paper.get("title", "?")
        if not url:
            self.logger.warning("Link-validity check: paper '%s' has no link; excluding.", title)
            return False
        try:
            response = requests.get(
                url,
                headers={"User-Agent": self._LINK_CHECK_USER_AGENT},
                timeout=self._LINK_CHECK_TIMEOUT_SECONDS,
                allow_redirects=True,
            )
        except requests.exceptions.RequestException as e:
            self.logger.warning("Link-validity check failed for '%s' (%s): %s", title, url, e)
            return False

        if not (200 <= response.status_code < 300):
            self.logger.warning(
                "Link-validity check: '%s' returned status %d; excluding.", title, response.status_code
            )
            return False

        hostname = (urlparse(url).hostname or "").lower()
        is_trusted_spa = any(
            hostname == d or hostname.endswith("." + d) for d in self._LINK_CHECK_SPA_TRUSTED_DOMAINS
        )
        if is_trusted_spa:
            return True

        body = response.text or ""
        if len(body) < self._LINK_CHECK_MIN_CONTENT_CHARS:
            self.logger.warning(
                "Link-validity check: '%s' body too short (%d chars); excluding.", title, len(body)
            )
            return False

        lower_body = body.lower()
        if any(marker in lower_body for marker in self._LINK_CHECK_BOILERPLATE_MARKERS):
            self.logger.warning(
                "Link-validity check: '%s' looks like an error/parked page; excluding.", title
            )
            return False

        return True

    def _filter_dead_links(self, project: str, papers: list) -> list:
        """Drops papers whose link fails the real HTTP validity check above.
        Runs the per-paper checks in a small thread pool (consistent with this
        codebase's existing ThreadPoolExecutor usage for per-project parallelism)
        so Config.MAX_LITERATURE_PAPERS worth of sequential 12s-timeout requests
        can't make a single project's cycle unreasonably slow."""
        if not papers:
            return papers

        results = {}
        with ThreadPoolExecutor(max_workers=self._LINK_CHECK_MAX_WORKERS) as executor:
            future_to_idx = {
                executor.submit(self._paper_link_is_valid, p): i for i, p in enumerate(papers)
            }
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    # Defense in depth: _paper_link_is_valid already catches
                    # RequestException internally, but never let any other
                    # unexpected error from a single paper's check take down
                    # the whole project's literature run.
                    self.logger.warning(
                        "Unexpected error during link-validity check for '%s': %s. Excluding.",
                        papers[idx].get("title", "?"), e
                    )
                    results[idx] = False

        kept = [p for i, p in enumerate(papers) if results.get(i)]
        dropped = len(papers) - len(kept)
        if dropped:
            self.logger.info(
                "Link-validity filter dropped %d paper(s) with dead/invalid links for %s.",
                dropped, project
            )
        return kept

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
        data_str = json.dumps(scholar_data, indent=2, ensure_ascii=False)
        
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
                    "number of samples": "e.g., 20000. If the abstract/data does not report this, write EXACTLY 'N/A (Not available at source)' — do not guess a number. IF theoretical, write 'N/A (Theoretical)'",
                    "number of features": "e.g., 14. If not reported at the source, write EXACTLY 'N/A (Not available at source)'. IF theoretical, write 'N/A (Theoretical)'",
                    "number of classes": "e.g., 5. If not reported at the source, write EXACTLY 'N/A (Not available at source)'. IF theoretical, write 'N/A (Theoretical)'",
                    "location": "e.g., In-lab testbed. If not reported at the source, write EXACTLY 'N/A (Not available at source)'. IF theoretical, write 'N/A (Theoretical)'",
                    "for how long": "e.g., 3 weeks. If not reported at the source, write EXACTLY 'N/A (Not available at source)'",
                    "data representation": "e.g., Tabular CSV, Time-series, Images, Raw packet captures. If not reported at the source, write EXACTLY 'N/A (Not available at source)'. IF theoretical, write 'N/A (Theoretical)'",
                    "reproducible": "Must be EXACTLY 'Yes', 'No', or 'N/A'",
                    "how complicated is it?": "Must be EXACTLY 'High', 'Moderate', 'Low', or 'N/A'",
                    "is there privacy issues?": "Must be EXACTLY 'Yes', 'No', 'Minimal', 'High', or 'N/A'",
                    "can i control the application collected?": "Must be EXACTLY 'Yes', 'No', or 'N/A'"
                }}
            ]
        }}
        Important constraints:
        1. Keep the EXACT JSON keys as defined above — they map 1:1 to fixed output
           columns; a renamed or missing key means that column is silently blank for
           this paper, not an error you'll see.
        2. Obey the strict EXACTLY string match rules for dropdown fields (like reproducible, "how complicated is it?").
        3. ESCAPE ALL STRINGS properly. Do NOT use markdown blocks like ```json.
        4. NEVER invent/guess a numeric or factual value that isn't actually supported
           by the abstract or provided data. When genuinely unknown, use the EXACT
           string 'N/A (Not available at source)' as instructed per-field above, so
           readers can tell "the source didn't report this" apart from a data problem.
        """
        
        try:
            response = self.ask_llm(prompt)
            start = response.find('{')
            end = response.rfind('}') + 1
            
            if start == -1 or end == 0:
                raise ValueError(f"LLM response did not contain JSON brackets. Response preview: {response[:200]!r}")
                 
            raw_json = response[start:end]
            # Strip all ASCII control chars except \x09 (tab) and \x0a (newline)
            raw_json = re.sub(r'[\x00-\x08\x0b-\x0c\x0d\x0e-\x1f]', '', raw_json)
            validated_report = LiteratureReport.model_validate_json(raw_json)
            return validated_report.model_dump(by_alias=True)
            
        except ValidationError as e:
            self.logger.error("Pydantic Schema Validation Failed! LLM hallucinated bad structure: %s", str(e))
            return fallback_data
        except RuntimeError as e:
            self.logger.error("Failed to parse or extract JSON from LLM: %s", str(e))
            self._alert_waterfall_exhausted("literature summarization", project)
            return fallback_data
        except (ValueError, json.JSONDecodeError) as e:
            self.logger.error("Failed to parse or extract JSON from LLM: %s", str(e))
            return fallback_data

    def _process_project(self, project: str):
        """Per-project logic extracted so ThreadPoolExecutor can run projects in parallel."""
        if self.db:
            self.db.log_agent_run(
                agent_name=self.agent_name,
                project_name=project,
                status="STARTED",
                started_at=datetime.now().isoformat()
            )
        print(f"\n{'='*40}\n🔬 Literature Search & Data Extraction for: {project}\n{'='*40}")

        text = self._read_project_text(project)
        topic_kw, method_kw = self.extract_keywords_from_text(project, text) if text else (project, project + " method")

        all_papers = []
        seen_titles = set()

        for query in [topic_kw, method_kw]:
            if not query or not query.strip():
                continue
            results = self.fetcher.search(query)
            for paper in results:
                title = paper.get("title", "").lower().strip()
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    all_papers.append(paper)

        try:
            all_papers = self._filter_relevant_papers(project, text, all_papers)
        except RelevanceFilterExhausted:
            # The relevance filter itself waterfall-exhausted (admin already alerted
            # inside _filter_relevant_papers). Proceeding here would mean writing
            # unfiltered/unvetted papers to the CSV and emailing them out — exactly
            # the silent-degradation gap this exception exists to close. Skip this
            # project's literature update entirely for this cycle, same pattern as
            # the "all search sources failed" branch below.
            self.logger.error(
                "Skipping literature update for project '%s' this cycle: relevance "
                "filter unavailable (LLM waterfall exhausted).", project
            )
            if self.db:
                self.db.log_agent_run(
                    agent_name=self.agent_name,
                    project_name=project,
                    status="FAILURE",
                    finished_at=datetime.now().isoformat()
                )
            return
        all_papers = all_papers[:Config.MAX_LITERATURE_PAPERS]

        if not all_papers:
            self.logger.warning(
                "Semantic Scholar returned no results for project '%s'. Trying SerpAPI fallback...", project
            )
            all_papers = self.fetcher.fetch_from_serpapi(topic_kw)
            if not all_papers:
                self.logger.warning(
                    "SerpAPI returned no results for project '%s'. Trying scholarly as last resort...", project
                )
                all_papers = self.fetcher.fetch_from_scholarly(topic_kw)
            if not all_papers:
                self.logger.error(
                    "All search sources failed for project '%s'. No literature update will be sent.", project
                )
                if self.db:
                    self.db.log_agent_run(
                        agent_name=self.agent_name,
                        project_name=project,
                        status="FAILURE",
                        finished_at=datetime.now().isoformat()
                    )
                return

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
            return

        all_papers = self._filter_dead_links(project, all_papers)

        if not all_papers:
            self.logger.warning(
                "All papers for %s were excluded by the link-validity check. Skipping LLM processing.",
                project
            )
            if self.db:
                self.db.log_agent_run(
                    agent_name=self.agent_name,
                    project_name=project,
                    status="SUCCESS",
                    finished_at=datetime.now().isoformat()
                )
            return

        all_papers = truncate_paper_abstracts(
            all_papers,
            total_budget_chars=Config.TOTAL_ABSTRACT_BUDGET_CHARS,
            min_chars=Config.MIN_ABSTRACT_CHARS,
            max_chars=Config.MAX_ABSTRACT_CHARS,
        )

        keywords = f"{topic_kw} | {method_kw}"
        research_data = self.process_results_with_llm(project, keywords, all_papers)

        if not research_data.get("papers"):
            self.logger.warning("No parsed papers available for %s. Saving summary only.", project)

        links_section = "\n\n### 🔗 Direct Links to Found Papers:\n"
        for item in all_papers:
            url = item.get("link") or item.get("url", "")
            links_section += f"* [{item['title']}]({url})\n"

        summary_text = f"# Literature Review for: {project}\n\n**Keywords Used:** {keywords}\n\n{research_data.get('summary', 'No summary available.')}{links_section}"

        self.library.save_literature_summary(project, summary_text)
        self.logger.info("Saved literature markdown summary for project: %s", project)

        papers_list = research_data.get("papers", [])
        if papers_list:
            self.library.batch_append_to_project_literature_table(project, papers_list)
            self.logger.info("Batch-appended %d papers to rolling CSV table for %s.", len(papers_list), project)

        safe_name = project.replace(" ", "_")
        csv_file_path = os.path.join(Config.LIBRARY_DIR, "comparison_tables", safe_name, f"{safe_name}_rolling_table.csv")

        if not research_data.get("papers"):
            self.logger.info("No papers found for project '%s'. Skipping literature email.", project)
            return

        if not os.path.exists(csv_file_path):
            self.logger.warning(
                "Rolling CSV not found for project '%s'; sending email without attachment.", project
            )

        self.logger.info("Sending literature update email for %s...", project)
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

    def run(self):
        self.logger.info("Starting the literature research cycle.")
        with ThreadPoolExecutor(max_workers=getattr(Config, 'LITERATURE_MAX_WORKERS', 4)) as executor:
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
                                subject=f"LiteratureResearchAgent — Project Failed: {project}",
                                message=(
                                    f"Unhandled error while processing '{project}':\n\n{e}\n\n"
                                    f"See LiteratureResearchAgent.log for the full traceback."
                                )
                            )
                        except Exception:
                            pass  # do not let alert failure mask the original error
        self.logger.info("Literature research cycle completed successfully.")