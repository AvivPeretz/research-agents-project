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

    def clean_latex_text(self, raw_tex: str) -> str:
        """
        Cleans basic LaTeX formatting commands from the text to make it readable for the LLM.
        """
        self.logger.info("Cleaning LaTeX formatting to extract plain text...")
        
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

    def read_all_tex_files(self, project_path: str) -> str:
        """Reads and cleans ALL .tex files in the project directory, concatenated."""
        text_content = self._read_all_tex_files_raw(project_path)
        return self.clean_latex_text(text_content) if text_content else ""

    def _read_all_tex_files_raw(self, project_path: str) -> str:
        """Reads (but does not clean) ALL .tex files in the project directory, concatenated."""
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
        raw_text = self._read_all_tex_files_raw(project_path)
        if not raw_text:
            return "", ""

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