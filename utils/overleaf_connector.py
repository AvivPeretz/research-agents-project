import os
import re
import logging
from config import Config

from config import Config

class OverleafConnector:
    """
    A utility class dedicated to parsing and cleaning local TeX files.
    Web scraping and downloading are now handled externally by the Data Ingestion Agent.
    """
    def __init__(self, base_storage_path: str = None):
        if base_storage_path is None:
            base_storage_path = str(Config.OVERLEAF_DIR)
        self.base_storage_path = base_storage_path
        
        self.logger = logging.getLogger("OverleafConnector")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)

    # Informal editorial/reviewer annotations researchers leave inline as notes to
    # themselves or collaborators — not manuscript content. \hl{...} (the soul/xcolor
    # "highlight" command) is the only such convention actually present in this
    # codebase's real tracked manuscripts (re-verified by scanning every .tex file in
    # both live test projects, PQTrace and Udi Aharon's PhD book, for this and several
    # other common informal-annotation commands — \todo, \comment, \note, \marginpar,
    # \reviewer, \sout, \textcolor{red}{...}, \fixme, \XXX, \TODO — none of which
    # appear anywhere in either project; only \hl{...} does, e.g. `\hl{A: this is a
    # lab, let try to think on different name}`). PQTrace also defines an unused
    # `\chen{...}` macro that expands to `\hl{\textbf{Chen:} #1}` — it is never
    # actually invoked in the current manuscript, so stripping raw \hl{...} covers
    # every real instance today; if `\chen{...}` (or a similar wrapper macro) ever
    # comes into use without expanding through \hl first, this would need revisiting.
    #
    # Shared here (moved from ProgressTrackingAgent, which was the first consumer to
    # need it) so LiteratureResearchAgent's keyword extraction and
    # ResearchEnhancementAgent's internal-review fallback get the same protection
    # instead of duplicating the regex three times. Deliberately NOT folded into
    # clean_latex_text() itself: clean_latex_text() is also the default path for
    # dashboard.py's human-facing "Tex Changes" view, where an operator reviewing
    # what changed in a manuscript may actually want to SEE a collaborator's inline
    # note (e.g. "Chen: please expand this section") — that's a different, legitimate
    # use case this session wasn't asked to change. strip_editorial_annotations() is
    # an explicit, opt-in step each LLM-facing caller applies to raw text BEFORE
    # cleaning, not a change to clean_latex_text()'s own default behavior.
    #
    # Must run on RAW .tex source, BEFORE clean_latex_text(): clean_latex_text()
    # unwraps `\hl{...}` down to its bare inner text (the same generic pass it uses
    # for \textbf, \emph, etc.), so by the time cleaned text exists the annotation is
    # byte-for-byte indistinguishable from real manuscript prose and can no longer be
    # identified or removed.
    #
    # Bounded one-level brace nesting so `\hl{\textbf{Chen:} ...}`-style nested
    # content is handled correctly and the regex doesn't undercount closing braces
    # (matches the same pattern _HEADING_RE below already uses for this reason).
    _EDITORIAL_ANNOTATION_RE = re.compile(
        r'\\hl\{((?:[^{}]|\{[^{}]*\})*)\}',
        re.DOTALL
    )

    def strip_editorial_annotations(self, raw_text: str) -> str:
        """Removes informal reviewer/editorial notes (see _EDITORIAL_ANNOTATION_RE)
        from raw LaTeX source so they're never treated as real manuscript content by
        an LLM-facing consumer. A no-op (returns input unchanged) if raw_text is
        empty or contains no such annotations. Callers must apply this to RAW text,
        before calling clean_latex_text() (directly or via another method that calls
        it internally) — see the comment on _EDITORIAL_ANNOTATION_RE above."""
        if not raw_text:
            return raw_text
        return self._EDITORIAL_ANNOTATION_RE.sub('', raw_text)

    def clean_latex_text(self, raw_tex: str) -> str:
        """
        Cleans basic LaTeX formatting commands from the text to make it readable for the LLM.
        """
        self.logger.debug("Cleaning LaTeX formatting to extract plain text...")
        
        # 1. Remove comments
        clean_text = re.sub(r'%.*$', '', raw_tex, flags=re.MULTILINE)
        
        # 2. Remove entire setup commands and environments we don't need to read
        clean_text = re.sub(r'\\documentclass\[.*?\]\{.*?\}|\\documentclass\{.*?\}', '', clean_text)
        clean_text = re.sub(r'\\usepackage\[.*?\]\{.*?\}|\\usepackage\{.*?\}', '', clean_text)
        clean_text = re.sub(r'\\begin\{document\}|\\end\{document\}', '', clean_text)
        
        # 3. Extract text from formatting commands (e.g., \textbf{Hello} -> Hello)
        # Pass 1 — handle simple formatting commands
        clean_text = re.sub(r'\\(textbf|textit|emph|text|mathrm|mathbf|mathit)\{([^}]*)\}', r'\2', clean_text)
        # Pass 2 — remove other non-math commands that wrap content (only if content has no backslash)
        clean_text = re.sub(r'\\(?!begin|end|frac|sum|int|prod|lim|sqrt|left|right|over)[a-zA-Z]+\{([^\\}][^}]*)\}', r'\1', clean_text)
        
        # 4. Remove standalone commands (like \maketitle, \clearpage)
        clean_text = re.sub(r'\\[a-zA-Z]+\*?', '', clean_text)
        
        # 5. Clean up multiple empty lines and curly braces
        clean_text = clean_text.replace('{', '').replace('}', '')
        clean_text = re.sub(r'\n\s*\n', '\n\n', clean_text)
        
        return clean_text.strip()

    _ABSTRACT_RE = re.compile(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', re.DOTALL)
    # Captures the heading argument allowing up to two levels of nested
    # braces (e.g. \section{Results (Fig.~\ref{fig:1})} or
    # \section{\textbf{Motivation}}), since a naive [^}]* stops at the
    # first inner '}' and truncates/corrupts real academic headings.
    _HEADING_RE = re.compile(
        r'\\(?:chapter|section|subsection|subsubsection)\*?'
        r'(?:\[[^\]]*\])?'
        r'\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}'
    )

    def extract_representative_sample(self, raw_tex: str, max_chars: int, heading_body_chars: int = 300) -> str:
        """
        Builds a structure-aware sample of a LaTeX document instead of a blind
        prefix truncation: abstract in full, every heading with a short excerpt
        of the text that follows it, and the tail of the last section extended
        to fill any remaining budget. Falls back to prefix truncation when the
        document has no detectable \\chapter/\\section/\\subsection markers.
        """
        if not raw_tex or not raw_tex.strip():
            return ""

        headings = list(self._HEADING_RE.finditer(raw_tex))
        if not headings:
            return self.clean_latex_text(raw_tex)[:max_chars]

        parts = []

        # Only pull the abstract out as a dedicated section when it appears
        # before the first heading. If it appears after (e.g. a book-class
        # chapter with its own \begin{abstract}), it will already be picked
        # up naturally inside that heading's body excerpt below, so treating
        # it as a separate section here would double-count it and waste
        # budget on duplicate content.
        abstract_match = self._ABSTRACT_RE.search(raw_tex)
        abstract_clean = ""
        if abstract_match and abstract_match.start() < headings[0].start():
            abstract_clean = self.clean_latex_text(abstract_match.group(1)).strip()
            if abstract_clean:
                parts.append(abstract_clean)
            else:
                abstract_clean = ""

        # Derive the per-heading excerpt size from the actual remaining
        # budget and heading count, instead of always using the fixed
        # heading_body_chars. Without this, num_headings * heading_body_chars
        # can exceed max_chars long before the tail-fill pass runs, silently
        # truncating away later headings (including the conclusion).
        used = len(abstract_clean)
        per_heading_budget = max(
            80,
            min(heading_body_chars, (max_chars - used) // max(len(headings), 1) - 40),
        )

        for i, match in enumerate(headings):
            heading_text = self.clean_latex_text(match.group(1)).strip()
            body_start = match.end()
            body_end = headings[i + 1].start() if i + 1 < len(headings) else len(raw_tex)
            body_clean = self.clean_latex_text(raw_tex[body_start:body_end]).strip()
            excerpt = body_clean[:per_heading_budget]
            parts.append(f"{heading_text}\n{excerpt}".strip())

        sample = "\n\n".join(p for p in parts if p)

        remaining_budget = max_chars - len(sample)
        if remaining_budget > 0:
            last_match = headings[-1]
            last_body_clean = self.clean_latex_text(raw_tex[last_match.end():]).strip()
            extra = last_body_clean[per_heading_budget:per_heading_budget + remaining_budget]
            if extra:
                sample += extra

        return sample[:max_chars]

    def read_all_tex_files_raw(self, project_path: str) -> str:
        """Reads ALL .tex files in the project directory, concatenated, without LaTeX cleaning."""
        text_content = ""
        if not os.path.exists(project_path):
            return ""
        for root, _, files in os.walk(project_path):
            for file in sorted(files):
                if file.endswith('.tex'):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            text_content += f.read() + "\n"
                    except OSError as e:
                        self.logger.warning("Failed to read %s: %s", file_path, str(e))
        return text_content

    def read_all_tex_files(self, project_path: str) -> str:
        """Reads and cleans ALL .tex files in the project directory, concatenated."""
        text_content = self.read_all_tex_files_raw(project_path)
        return self.clean_latex_text(text_content) if text_content else ""

    # Matches \input{target} and \include{target}, where target may or may not
    # include the .tex extension and may include a relative subdirectory path
    # (e.g. \input{ft-ann/ft-ann}). Deliberately does NOT match \includegraphics
    # (a common false-positive risk for a naive \\include.* pattern) because the
    # alternation is anchored to the literal end of "input"/"include" via the
    # opening brace immediately following it.
    _INPUT_INCLUDE_RE = re.compile(r'\\(?:input|include)\{([^}]+)\}')

    def _resolve_input_include_target(self, project_path: str, target: str) -> str:
        """Resolves an \\input/\\include target (as written in the LaTeX source) to
        an absolute file path under project_path, or None if no such file exists.
        Real LaTeX resolves \\input/\\include paths relative to the root document's
        directory (i.e. project_path here), not relative to the including file's own
        directory — so every target is resolved against project_path regardless of
        how deeply nested the file doing the including is."""
        target = target.strip()
        candidates = [target] if target.endswith('.tex') else [f"{target}.tex", target]
        for candidate in candidates:
            candidate_path = os.path.normpath(os.path.join(project_path, candidate))
            if os.path.isfile(candidate_path):
                return candidate_path
        return None

    def read_manuscript_tex_files_raw(self, project_path: str, main_file: str = "main.tex", notifier=None) -> str:
        """Reads only the .tex files actually reachable from main_file (default
        "main.tex", matching this codebase's existing entry-point convention — see
        read_and_clean_tex_file()'s default and LiteratureResearchAgent's use of
        extract_representative_sample()), by recursively resolving \\input{...} and
        \\include{...} directives, and concatenates their raw content in traversal
        order (root file first, then each referenced file in the order it is
        referenced, depth-first).

        This is the fix for a real data-corruption bug: read_all_tex_files_raw()
        blindly concatenates EVERY .tex file physically present anywhere under
        project_path, with no regard for whether it's actually part of the compiled
        manuscript. A stale, unreferenced file left in the project directory (e.g.
        an old draft renamed to old_version.tex) was silently treated as new
        manuscript content and corrupted a real delta calculation. This method
        instead treats "the manuscript" as exactly the set of files reachable from
        the root document, so an unreferenced stray .tex file is correctly excluded.

        \\input/\\include directives inside LaTeX comments (e.g. a chapter
        commented out with a leading %, as in Udi Aharon's real main.tex where
        `%\\include{eg-tgn/eg-tgn}` and `%\\input{Chapters/Appendix}` are both
        commented out) are correctly ignored — those files are not part of the
        currently-compiled document and must not be pulled in.

        Fallback behavior:
        - If main_file doesn't exist under project_path at all, there's no known
          entry point to resolve a reachable-file tree from, so this falls back to
          the legacy "read every .tex file found" behavior (read_all_tex_files_raw)
          rather than silently returning empty text and losing real content. Taking
          this fallback silently reintroduces the exact contamination bug this
          method exists to fix (a stray/unreferenced file getting counted as new
          manuscript content), so an optional `notifier` (an object exposing
          send_admin_alert(subject=..., message=...), e.g. NotificationAgent) can be
          passed in to have an admin alerted whenever it happens — see the
          ProgressTrackingAgent.check_text_changes call site, which passes its own
          self.notifier through here for exactly this reason.
        - If main_file exists but has no resolvable \\input/\\include structure at
          all (i.e. it's a fully self-contained document, like PQTrace's real
          main.tex), the correct manuscript is exactly main_file's own content —
          this falls out naturally from the traversal below with no special-casing
          needed, and correctly excludes any other unreferenced .tex file (like
          PQTrace's stale old_version.tex) since nothing in main.tex references it.
        """
        if not os.path.exists(project_path):
            return ""

        main_path = os.path.join(project_path, main_file)
        if not os.path.isfile(main_path):
            project_name = os.path.basename(os.path.normpath(project_path))
            self.logger.warning(
                "%s not found under %s; no entry point available to resolve "
                "\\input/\\include structure from. Falling back to reading every "
                ".tex file found (legacy behavior).",
                main_file, project_path
            )
            if notifier:
                try:
                    notifier.send_admin_alert(
                        subject=f"OverleafConnector — root document not found: {project_name}",
                        message=(
                            f"Expected root document '{main_file}' was not found for "
                            f"project '{project_name}' (looked under {project_path}). "
                            f"Because there is no known entry point to resolve "
                            f"\\input/\\include structure from, this run fell back to "
                            f"reading every .tex file found anywhere in the project "
                            f"directory (the legacy, contamination-prone behavior). As "
                            f"a result, this project's progress-tracking output may "
                            f"currently include stray/unreferenced file content — e.g. "
                            f"old chapter drafts or renamed files may be counted as new "
                            f"content.\n\n"
                            f"To fix: rename/create the project's actual root document "
                            f"to '{main_file}', or update the caller to pass the "
                            f"correct main_file for this project."
                        )
                    )
                except Exception:
                    pass  # do not let alert failure mask the fallback read itself
            return self.read_all_tex_files_raw(project_path)

        visited = set()
        ordered_contents = []

        def _visit(file_path: str):
            real_path = os.path.realpath(file_path)
            if real_path in visited:
                return
            visited.add(real_path)
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    raw = f.read()
            except OSError as e:
                self.logger.warning("Failed to read %s: %s", file_path, str(e))
                return

            ordered_contents.append(raw)

            # Strip comments before scanning for \input/\include targets (but NOT
            # from the raw content appended above) so a commented-out directive like
            # `%\include{eg-tgn/eg-tgn}` is never followed.
            scan_text = re.sub(r'%.*$', '', raw, flags=re.MULTILINE)
            for target in self._INPUT_INCLUDE_RE.findall(scan_text):
                resolved = self._resolve_input_include_target(project_path, target)
                if resolved:
                    _visit(resolved)
                else:
                    self.logger.debug(
                        "Could not resolve \\input/\\include target '%s' referenced "
                        "from %s; skipping.", target, file_path
                    )

        _visit(main_path)
        return "".join(content + "\n" for content in ordered_contents)

    # Markers that conventionally start an appendix in LaTeX. clean_latex_text() strips
    # all command syntax, so this split MUST happen on the raw (uncleaned) source —
    # by the time text is cleaned there is no reliable trace left that a section was
    # ever an appendix, and truncation logic downstream would silently treat trailing
    # appendix content as if it were the paper's actual conclusion.
    _APPENDIX_MARKER_RE = re.compile(
        r'\\appendix\b|\\begin\{appendi(?:x|ces)\}|\\section\*?\{\s*Appendi(?:x|ces)',
        re.IGNORECASE
    )

    def read_all_tex_files_split(self, project_path: str) -> tuple:
        """Like read_all_tex_files(), but returns (body, appendix) separately, both
        already cleaned. Appendix content is whatever follows the first recognized
        appendix marker in the raw source; if no marker is found, appendix is "" and
        body is the full cleaned text (identical to read_all_tex_files())."""
        raw_text = self.read_all_tex_files_raw(project_path)
        if not raw_text:
            return "", ""

        # This method's only production caller (ResearchEnhancementAgent's internal
        # peer-review fallback) is LLM-facing, so editorial annotations (\hl{...})
        # are stripped here by default — unlike read_all_tex_files() above, which
        # dashboard.py also uses for a human-facing manuscript-diff view where
        # stripping isn't necessarily wanted (see strip_editorial_annotations()'s
        # docstring for the full reasoning). Safe to change this method's default
        # specifically because it has exactly one production consumer.
        raw_text = self.strip_editorial_annotations(raw_text)

        match = self._APPENDIX_MARKER_RE.search(raw_text)
        if not match:
            return self.clean_latex_text(raw_text), ""

        raw_body = raw_text[:match.start()]
        raw_appendix = raw_text[match.start():]
        return self.clean_latex_text(raw_body), self.clean_latex_text(raw_appendix)

    def read_and_clean_tex_file(self, project_path: str, main_file: str = "main.tex") -> str:
        """
        Reads the main .tex file of the project from the local directory and returns the cleaned text.
        """
        file_path = os.path.join(project_path, main_file)
        if not os.path.exists(file_path):
            self.logger.error("File not found: %s", file_path)
            return ""
            
        try:
            with open(file_path, 'r', encoding='utf-8') as file:
                raw_tex = file.read()
        except (UnicodeDecodeError, OSError) as e:
            self.logger.error("Failed to read tex file %s: %s", file_path, str(e))
            return ""

        return self.clean_latex_text(raw_tex)