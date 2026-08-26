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