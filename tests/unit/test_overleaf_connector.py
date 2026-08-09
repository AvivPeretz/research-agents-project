"""Unit tests for OverleafConnector LaTeX cleaning and file operations."""

import pytest

from utils.overleaf_connector import OverleafConnector
from config import Config


class TestCleanLatex:
    """Tests for LaTeX document cleaning functionality."""

    def test_clean_latex_removes_comments(self):
        """Asserts that LaTeX comments (%) are removed from output."""
        connector = OverleafConnector()
        input_text = "Some text % this is a comment\nMore text"
        result = connector.clean_latex_text(input_text)
        assert "%" not in result

    def test_clean_latex_removes_usepackage(self):
        """Asserts that \\usepackage commands are removed from output."""
        connector = OverleafConnector()
        input_text = r"Text \usepackage{amsmath} more text"
        result = connector.clean_latex_text(input_text)
        assert r"\usepackage" not in result

    def test_clean_latex_removes_documentclass(self):
        """Asserts that \\documentclass commands are removed from output."""
        connector = OverleafConnector()
        input_text = r"\documentclass{article} Some content"
        result = connector.clean_latex_text(input_text)
        assert r"\documentclass" not in result

    def test_clean_latex_unwraps_textbf(self):
        """Asserts that \\textbf{text} is unwrapped to just text."""
        connector = OverleafConnector()
        input_text = r"\textbf{Hello} world"
        result = connector.clean_latex_text(input_text)
        assert "Hello" in result
        assert r"\textbf" not in result

    def test_clean_latex_unwraps_textit(self):
        """Asserts that \\textit{text} is unwrapped to just text."""
        connector = OverleafConnector()
        input_text = r"\textit{World} of text"
        result = connector.clean_latex_text(input_text)
        assert "World" in result
        assert r"\textit" not in result

    def test_clean_latex_removes_standalone_commands(self):
        """Asserts that \\maketitle and \\clearpage are removed."""
        connector = OverleafConnector()
        input_text = r"Text \maketitle more \clearpage end"
        result = connector.clean_latex_text(input_text)
        assert r"\maketitle" not in result
        assert r"\clearpage" not in result

    def test_clean_latex_removes_curly_braces(self):
        """Asserts that all curly braces { } are removed from output."""
        connector = OverleafConnector()
        input_text = r"Text with {braces} and more"
        result = connector.clean_latex_text(input_text)
        assert "{" not in result
        assert "}" not in result

    def test_clean_latex_collapses_multiple_blank_lines(self):
        """Asserts that multiple consecutive blank lines are collapsed to one."""
        connector = OverleafConnector()
        input_text = "Line 1\n\n\n\nLine 2\n\n\nLine 3"
        result = connector.clean_latex_text(input_text)
        assert "\n\n\n" not in result
        assert "Line 1" in result
        assert "Line 2" in result

    def test_clean_latex_empty_input_returns_empty(self):
        """Asserts that empty string input returns empty string."""
        connector = OverleafConnector()
        result = connector.clean_latex_text("")
        assert result == ""

    def test_clean_latex_preserves_real_text(self):
        """Asserts that real paragraph text survives the cleaning process."""
        connector = OverleafConnector()
        input_text = r"""
        Machine learning is a subfield of artificial intelligence.
        \usepackage{amsmath}
        % This is a comment
        \textbf{Deep learning} has revolutionized computer vision.
        """
        result = connector.clean_latex_text(input_text)
        assert "Machine learning" in result
        assert "artificial intelligence" in result
        assert "Deep learning" in result
        assert "computer vision" in result


class TestReadAndCleanTexFile:
    """Tests for reading and cleaning LaTeX files."""

    def test_read_and_clean_tex_file_success(self, temp_project_dir):
        """Asserts that reading and cleaning a valid tex file returns non-empty cleaned string."""
        connector = OverleafConnector()
        result = connector.read_and_clean_tex_file(str(temp_project_dir), "main.tex")
        assert result != ""
        assert isinstance(result, str)
        # Should not contain LaTeX directives
        assert r"\documentclass" not in result
        assert r"\usepackage" not in result

    def test_read_and_clean_tex_file_missing_file(self, tmp_path):
        """Asserts that reading a non-existent file returns empty string."""
        connector = OverleafConnector()
        result = connector.read_and_clean_tex_file(str(tmp_path), "nonexistent.tex")
        assert result == ""

    def test_read_and_clean_tex_file_empty_file(self, tmp_path):
        """Asserts that reading an empty tex file returns empty string."""
        empty_tex = tmp_path / "empty.tex"
        empty_tex.write_text("")
        connector = OverleafConnector()
        result = connector.read_and_clean_tex_file(str(tmp_path), "empty.tex")
        assert result == ""

    # TEST A — FIX 1: default base_storage_path must use Config.OVERLEAF_DIR
    def test_default_base_storage_path_uses_config_overleaf_dir(self):
        """Instantiating OverleafConnector() with no args must use Config.OVERLEAF_DIR."""
        connector = OverleafConnector()
        assert connector.base_storage_path == str(Config.OVERLEAF_DIR)
        assert connector.base_storage_path != "overleaf_projects"

    # TEST C — FIX 7: invalid UTF-8 bytes in a .tex file return "" without raising
    def test_read_and_clean_tex_file_invalid_utf8_returns_empty(self, tmp_path):
        """Files with invalid UTF-8 encoding must return '' instead of raising UnicodeDecodeError."""
        bad_tex = tmp_path / "main.tex"
        bad_tex.write_bytes(b"\xff\xfe invalid utf-8 bytes here")
        connector = OverleafConnector()
        result = connector.read_and_clean_tex_file(str(tmp_path), "main.tex")
        assert result == ""


class TestReadAllTexFilesRaw:
    """Tests for the raw (uncleaned) multi-file reader used by structural sampling."""

    def test_read_all_tex_files_raw_preserves_latex_commands(self, tmp_path):
        """Raw reader must NOT strip \\section or other LaTeX commands."""
        (tmp_path / "main.tex").write_text(r"\section{Introduction} Some text here.")
        connector = OverleafConnector()
        result = connector.read_all_tex_files_raw(str(tmp_path))
        assert r"\section{Introduction}" in result

    def test_read_all_tex_files_raw_missing_dir_returns_empty(self, tmp_path):
        connector = OverleafConnector()
        result = connector.read_all_tex_files_raw(str(tmp_path / "does_not_exist"))
        assert result == ""

    def test_read_all_tex_files_still_returns_cleaned_text(self, temp_project_dir):
        """Existing public method must keep returning cleaned text (no \\section markers)."""
        connector = OverleafConnector()
        result = connector.read_all_tex_files(str(temp_project_dir))
        assert result != ""
        assert r"\documentclass" not in result
        assert r"\usepackage" not in result


class TestExtractRepresentativeSample:
    """Tests for structure-aware manuscript sampling."""

    STRUCTURED_DOC = r"""
\documentclass{article}
\begin{document}

\begin{abstract}
This paper studies post-quantum cryptography traffic fingerprinting.
\end{abstract}

\section{Introduction}
Post-quantum cryptography introduces new traffic patterns worth studying.
This section motivates the problem and reviews prior work in the area.

\section{Methodology}
We use an Isolation Forest and a One-Class SVM to detect anomalies.
The pipeline extracts flow-level features from captured network traffic.

\section{Results}
Our approach achieves strong detection accuracy across all tested scenarios.
We compare against three baseline methods and report precision and recall.

\section{Conclusion}
This work demonstrates that automated traffic recording combined with
feature extraction and classification can reliably detect PQC traffic
patterns in real-world network deployments.

\end{document}
"""

    def test_includes_headings_from_across_the_document(self):
        """Sample must span the whole doc's structure, not just the opening section."""
        connector = OverleafConnector()
        sample = connector.extract_representative_sample(self.STRUCTURED_DOC, max_chars=4000)
        assert "Introduction" in sample
        assert "Methodology" in sample
        assert "Results" in sample
        assert "Conclusion" in sample

    def test_includes_abstract_when_present(self):
        connector = OverleafConnector()
        sample = connector.extract_representative_sample(self.STRUCTURED_DOC, max_chars=4000)
        assert "post-quantum cryptography traffic fingerprinting" in sample

    def test_never_exceeds_max_chars(self):
        connector = OverleafConnector()
        sample = connector.extract_representative_sample(self.STRUCTURED_DOC, max_chars=200)
        assert len(sample) <= 200

    def test_unstructured_document_falls_back_to_prefix_truncation(self):
        """A document with no \\section/\\chapter commands must fall back to today's behavior."""
        connector = OverleafConnector()
        flat_doc = "Just a long block of prose with no LaTeX sectioning commands at all. " * 50
        sample = connector.extract_representative_sample(flat_doc, max_chars=100)
        expected = connector.clean_latex_text(flat_doc)[:100]
        assert sample == expected

    def test_empty_input_returns_empty_string(self):
        connector = OverleafConnector()
        assert connector.extract_representative_sample("", max_chars=4000) == ""

    def test_output_has_no_leftover_latex_commands(self):
        """Assembled sample must be cleaned, not raw LaTeX."""
        connector = OverleafConnector()
        sample = connector.extract_representative_sample(self.STRUCTURED_DOC, max_chars=4000)
        assert r"\section" not in sample
        assert r"\begin" not in sample

    def test_heading_with_nested_braces_is_not_truncated(self):
        """Headings with nested braces (e.g. \\ref inside \\section) must be captured in full."""
        connector = OverleafConnector()
        doc = r"""
\section{Results (Fig.~\ref{fig:1})}
We observed a significant improvement over the baseline.

\section{\textbf{Motivation}}
This work is motivated by real-world deployment constraints.
"""
        sample = connector.extract_representative_sample(doc, max_chars=4000)
        assert "Results (Fig.~" in sample
        assert "Motivation" in sample
        assert ")}" not in sample
        assert r"\ref" not in sample
        assert r"\textbf" not in sample
