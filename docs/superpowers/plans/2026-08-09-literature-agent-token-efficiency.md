# Literature Agent Token Efficiency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `LiteratureResearchAgent`'s blind-truncation token usage with structure-aware manuscript sampling and adaptive paper-abstract truncation, so the agent extracts maximum understanding per token and can no longer reproduce the Groq 8000-TPM failure seen on the Udi Aharon PhD Book v2 test run.

**Architecture:** Two independent, additive changes wired into the existing `LiteratureResearchAgent` pipeline. Part A adds a structure-aware sampling method to `OverleafConnector`, replacing the prefix-truncation call site in `_read_project_text`. Part B adds a small pure-function module (`utils/token_budget.py`) that adaptively caps paper abstract lengths based on how many papers are in the batch, wired in right before the final summarization LLM call in `_process_project`.

**Tech Stack:** Python 3.12, pytest, regex (`re`), existing `OverleafConnector` / `LiteratureResearchAgent` classes.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-09-literature-agent-token-efficiency-design.md`
- `Config.MAX_PROJECT_TEXT_CHARS` (4000) is reused unchanged as the Part A sample budget — do not change its value.
- New config constants for Part B: `TOTAL_ABSTRACT_BUDGET_CHARS = 16000`, `MIN_ABSTRACT_CHARS = 300`, `MAX_ABSTRACT_CHARS = 1200`.
- `read_all_tex_files()` (existing public method, consumed by `ProgressTrackingAgent` and `ResearchEnhancementAgent`) must keep returning fully-cleaned text with identical behavior to today — no observable change for its existing callers.
- No change to the 15-paper pool cap, the relevance filter, or the `LiteratureReport` Pydantic schema.
- Existing test suite (`pytest tests/ -v`) must continue passing after every task.

---

### Task 1: Adaptive abstract-truncation budget module

**Files:**
- Modify: `config.py` (add 3 constants near `MAX_PROJECT_TEXT_CHARS`, config.py:104-105)
- Create: `utils/token_budget.py`
- Test: `tests/unit/test_token_budget.py`

**Interfaces:**
- Consumes: nothing (pure functions, no dependencies beyond stdlib)
- Produces:
  - `compute_per_paper_cap(num_papers: int, total_budget_chars: int, min_chars: int, max_chars: int) -> int`
  - `truncate_paper_abstracts(papers: list[dict], total_budget_chars: int, min_chars: int, max_chars: int) -> list[dict]` — returns a **new** list of **new** dicts (does not mutate input `papers` or its dicts); truncates whichever of `"abstract"` / `"snippet"` keys is present and non-empty on each paper, appending `"…"` when truncated; leaves all other keys and any abstract shorter than the cap untouched.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_token_budget.py`:

```python
"""Unit tests for the adaptive abstract-truncation budget helpers."""

from utils.token_budget import compute_per_paper_cap, truncate_paper_abstracts


class TestComputePerPaperCap:
    def test_few_papers_clamps_to_max(self):
        """3 papers over a 16000-char budget would compute to 5333/paper — must clamp to max_chars."""
        cap = compute_per_paper_cap(num_papers=3, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert cap == 1200

    def test_many_papers_clamps_to_min(self):
        """100 papers over a 16000-char budget would compute to 160/paper — must clamp to min_chars."""
        cap = compute_per_paper_cap(num_papers=100, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert cap == 300

    def test_mid_range_paper_count_uses_even_split(self):
        """15 papers over 16000 chars = ~1066/paper, within [min, max] so no clamping applied."""
        cap = compute_per_paper_cap(num_papers=15, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert cap == 16000 // 15

    def test_zero_papers_returns_max_without_dividing_by_zero(self):
        cap = compute_per_paper_cap(num_papers=0, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert cap == 1200


class TestTruncatePaperAbstracts:
    def test_truncates_long_abstract_field(self):
        papers = [{"title": "A", "abstract": "x" * 5000}]
        result = truncate_paper_abstracts(papers, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert len(result[0]["abstract"]) == 1201  # 1200 chars + ellipsis marker
        assert result[0]["abstract"].endswith("…")

    def test_truncates_snippet_field_when_abstract_absent(self):
        papers = [{"title": "A", "snippet": "y" * 5000}]
        result = truncate_paper_abstracts(papers, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert len(result[0]["snippet"]) == 1201
        assert result[0]["snippet"].endswith("…")

    def test_short_abstract_left_untouched(self):
        papers = [{"title": "A", "abstract": "short abstract text"}]
        result = truncate_paper_abstracts(papers, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert result[0]["abstract"] == "short abstract text"

    def test_other_fields_untouched(self):
        papers = [{"title": "A", "abstract": "x" * 5000, "year": "2024", "citationCount": "15"}]
        result = truncate_paper_abstracts(papers, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert result[0]["title"] == "A"
        assert result[0]["year"] == "2024"
        assert result[0]["citationCount"] == "15"

    def test_does_not_mutate_input(self):
        papers = [{"title": "A", "abstract": "x" * 5000}]
        original_abstract = papers[0]["abstract"]
        truncate_paper_abstracts(papers, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert papers[0]["abstract"] == original_abstract

    def test_empty_list_returns_empty_list(self):
        assert truncate_paper_abstracts([], total_budget_chars=16000, min_chars=300, max_chars=1200) == []

    def test_cap_scales_down_with_more_papers(self):
        """10 papers should get a larger per-paper cap than 15 papers from the same budget."""
        ten_papers = [{"title": str(i), "abstract": "x" * 5000} for i in range(10)]
        fifteen_papers = [{"title": str(i), "abstract": "x" * 5000} for i in range(15)]
        ten_result = truncate_paper_abstracts(ten_papers, total_budget_chars=16000, min_chars=300, max_chars=1200)
        fifteen_result = truncate_paper_abstracts(fifteen_papers, total_budget_chars=16000, min_chars=300, max_chars=1200)
        assert len(ten_result[0]["abstract"]) >= len(fifteen_result[0]["abstract"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_token_budget.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'utils.token_budget'`

- [ ] **Step 3: Add config constants**

In `config.py`, immediately after the existing `MAX_PROJECT_TEXT_CHARS` line (config.py:105), add:

```python
    # Total char budget for the paper-abstract JSON payload sent to the LLM
    # summarization call, split adaptively across however many papers are in
    # that batch (see utils/token_budget.py). Sized to keep the whole payload
    # comfortably under Groq's 8000 TPM ceiling even at the full 15-paper cap.
    TOTAL_ABSTRACT_BUDGET_CHARS: int = 16000
    # Floor and ceiling on the per-paper abstract cap computed from the budget above
    MIN_ABSTRACT_CHARS: int = 300
    MAX_ABSTRACT_CHARS: int = 1200
```

- [ ] **Step 4: Implement `utils/token_budget.py`**

```python
"""Adaptive character-budget helpers for capping LLM payload sizes.

Used by LiteratureResearchAgent to keep the paper-abstract JSON payload sent
to the summarization LLM call under a fixed total character budget, regardless
of how many papers are in a given batch (see docs/superpowers/specs/
2026-08-09-literature-agent-token-efficiency-design.md).
"""


def compute_per_paper_cap(num_papers: int, total_budget_chars: int, min_chars: int, max_chars: int) -> int:
    """Splits total_budget_chars evenly across num_papers, clamped to [min_chars, max_chars]."""
    if num_papers <= 0:
        return max_chars
    raw_cap = total_budget_chars // num_papers
    return max(min_chars, min(raw_cap, max_chars))


def truncate_paper_abstracts(papers: list, total_budget_chars: int, min_chars: int, max_chars: int) -> list:
    """Returns a new list of new paper dicts with 'abstract'/'snippet' fields capped.

    Does not mutate the input list or its dicts. Leaves abstracts already
    shorter than the computed cap untouched. Appends '…' to truncated text.
    """
    if not papers:
        return papers

    cap = compute_per_paper_cap(len(papers), total_budget_chars, min_chars, max_chars)

    truncated = []
    for paper in papers:
        new_paper = dict(paper)
        for field in ("abstract", "snippet"):
            value = new_paper.get(field)
            if value and len(value) > cap:
                new_paper[field] = value[:cap] + "…"
        truncated.append(new_paper)
    return truncated
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_token_budget.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Commit**

```bash
git add config.py utils/token_budget.py tests/unit/test_token_budget.py
git commit -m "feat: add adaptive abstract-truncation budget module"
```

---

### Task 2: Structure-aware manuscript sampling in OverleafConnector

**Files:**
- Modify: `utils/overleaf_connector.py` (refactor `read_all_tex_files`, add `read_all_tex_files_raw` and `extract_representative_sample`)
- Test: `tests/unit/test_overleaf_connector.py` (append new test classes)

**Interfaces:**
- Consumes: `OverleafConnector.clean_latex_text(raw_tex: str) -> str` (existing method, unchanged)
- Produces:
  - `OverleafConnector.read_all_tex_files_raw(project_path: str) -> str` — concatenated raw (uncleaned) text from all `.tex` files in `project_path`, `""` if none found or path doesn't exist.
  - `OverleafConnector.extract_representative_sample(raw_tex: str, max_chars: int, heading_body_chars: int = 300) -> str` — structure-aware sample, capped at `max_chars`, falls back to `clean_latex_text(raw_tex)[:max_chars]` when no `\chapter`/`\section`/`\subsection`/`\subsubsection` commands are found.
- `read_all_tex_files(project_path: str) -> str` keeps its existing signature and behavior (fully cleaned text), now implemented by delegating to `read_all_tex_files_raw` + `clean_latex_text`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_overleaf_connector.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_overleaf_connector.py -v -k "RepresentativeSample or ReadAllTexFilesRaw"`
Expected: FAIL with `AttributeError: 'OverleafConnector' object has no attribute 'read_all_tex_files_raw'` (and similarly for `extract_representative_sample`)

- [ ] **Step 3: Implement the raw reader (refactor) and sampling method**

In `utils/overleaf_connector.py`, replace the existing `read_all_tex_files` method (utils/overleaf_connector.py:55-69) with:

```python
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
```

Then add the sampling method and its regexes near the top of the class (after `clean_latex_text`, before `read_all_tex_files_raw`):

```python
    _ABSTRACT_RE = re.compile(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', re.DOTALL)
    _HEADING_RE = re.compile(r'\\(?:chapter|section|subsection|subsubsection)\*?\{([^}]*)\}')

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

        abstract_match = self._ABSTRACT_RE.search(raw_tex)
        if abstract_match:
            abstract_clean = self.clean_latex_text(abstract_match.group(1)).strip()
            if abstract_clean:
                parts.append(abstract_clean)

        for i, match in enumerate(headings):
            heading_text = match.group(1)
            body_start = match.end()
            body_end = headings[i + 1].start() if i + 1 < len(headings) else len(raw_tex)
            body_clean = self.clean_latex_text(raw_tex[body_start:body_end]).strip()
            excerpt = body_clean[:heading_body_chars]
            parts.append(f"{heading_text}\n{excerpt}".strip())

        sample = "\n\n".join(p for p in parts if p)

        remaining_budget = max_chars - len(sample)
        if remaining_budget > 0:
            last_match = headings[-1]
            last_body_clean = self.clean_latex_text(raw_tex[last_match.end():]).strip()
            extra = last_body_clean[heading_body_chars:heading_body_chars + remaining_budget]
            if extra:
                sample += extra

        return sample[:max_chars]
```

Add `import re` at the top of the file if not already present (it already is, per the existing `clean_latex_text` implementation).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_overleaf_connector.py -v`
Expected: PASS (all tests, including pre-existing ones)

- [ ] **Step 5: Run the full unit suite to check for regressions**

Run: `pytest tests/unit/ -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add utils/overleaf_connector.py tests/unit/test_overleaf_connector.py
git commit -m "feat: add structure-aware manuscript sampling to OverleafConnector"
```

---

### Task 3: Wire manuscript sampling into LiteratureResearchAgent

**Files:**
- Modify: `agents/literature_research_agent.py:35-45` (`_read_project_text`)
- Test: `tests/integration/test_literature_agent.py` (append new test class)

**Interfaces:**
- Consumes: `OverleafConnector.read_all_tex_files_raw` and `OverleafConnector.extract_representative_sample` from Task 2.
- Produces: `_read_project_text(self, project_name: str) -> str` — same signature as today, now returns a structure-aware sample instead of a prefix truncation.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_literature_agent.py`, inside `class TestLiteratureResearchAgent`:

```python
    def test_read_project_text_uses_structural_sampling(self, literature_agent, sample_project_name, tmp_path, monkeypatch):
        """Asserts _read_project_text samples across the whole document, not just the prefix."""
        from config import Config

        project_dir = tmp_path / sample_project_name
        project_dir.mkdir()
        long_structured_doc = r"""
\documentclass{article}
\begin{document}
\section{Introduction}
""" + ("Filler introduction text. " * 200) + r"""
\section{Conclusion}
This conclusion mentions a unique marker: ZEBRA_MARKER_TOKEN.
\end{document}
"""
        (project_dir / "main.tex").write_text(long_structured_doc)
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))

        result = literature_agent._read_project_text(sample_project_name)

        assert "ZEBRA_MARKER_TOKEN" in result
        assert len(result) <= Config.MAX_PROJECT_TEXT_CHARS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_literature_agent.py -v -k test_read_project_text_uses_structural_sampling`
Expected: FAIL — `ZEBRA_MARKER_TOKEN` not in result, because today's prefix truncation (4000 chars of ~5400-char filler intro) never reaches the conclusion section.

- [ ] **Step 3: Update `_read_project_text`**

In `agents/literature_research_agent.py`, replace the method body (agents/literature_research_agent.py:35-45):

```python
    def _read_project_text(self, project_name: str) -> str:
        """Reads all .tex files for the project and returns a structure-aware sample."""
        project_dir = os.path.join(Config.OVERLEAF_DIR, project_name)
        raw_text = self.connector.read_all_tex_files_raw(project_dir)
        if not raw_text:
            self.logger.warning("No valid LaTeX text extracted for project: %s", project_name)
            return ""
        max_chars = getattr(Config, 'MAX_PROJECT_TEXT_CHARS', 4000)
        sample = self.connector.extract_representative_sample(raw_text, max_chars)
        if len(raw_text) > max_chars:
            self.logger.info(
                "Sampled project text to %d chars for LLM (original: %d chars).",
                len(sample), len(raw_text)
            )
        return sample
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_literature_agent.py -v -k test_read_project_text_uses_structural_sampling`
Expected: PASS

- [ ] **Step 5: Run the full literature agent test file to check for regressions**

Run: `pytest tests/integration/test_literature_agent.py -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add agents/literature_research_agent.py tests/integration/test_literature_agent.py
git commit -m "feat: use structure-aware manuscript sampling for keyword extraction"
```

---

### Task 4: Wire adaptive abstract truncation into LiteratureResearchAgent

**Files:**
- Modify: `agents/literature_research_agent.py:235-249` (`_process_project`)
- Test: `tests/integration/test_literature_agent.py` (append new test class)

**Interfaces:**
- Consumes: `truncate_paper_abstracts` from `utils/token_budget.py` (Task 1); `Config.TOTAL_ABSTRACT_BUDGET_CHARS`, `Config.MIN_ABSTRACT_CHARS`, `Config.MAX_ABSTRACT_CHARS` (Task 1).
- Produces: no new public interface — `_process_project` now truncates `all_papers`' abstracts before building the final summarization prompt, applied after `enrich_with_openalex` so it covers the Semantic Scholar, SerpAPI, and scholarly code paths uniformly (all three converge through the same enrichment + truncation + `process_results_with_llm` call).

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_literature_agent.py`, inside `class TestLiteratureResearchAgent`:

```python
    def test_process_project_truncates_abstracts_before_final_llm_call(self, mock_notifier, sample_project_name):
        """Asserts the JSON payload sent to the final LLM call has abstracts capped, not the raw 5000+ chars."""
        from tests.fixtures.mock_responses import VALID_LITERATURE_JSON

        long_abstract_papers = [
            {"title": f"Paper {i}", "abstract": "x" * 5000, "year": "2024", "citationCount": "1", "venue": "V", "link": "http://example.com"}
            for i in range(10)
        ]

        captured_prompts = []

        def fake_ask_llm(prompt):
            captured_prompts.append(prompt)
            return VALID_LITERATURE_JSON

        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=fake_ask_llm), \
             patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=long_abstract_papers), \
             patch("utils.literature_fetcher.LiteratureFetcher.enrich_with_openalex", side_effect=lambda papers: papers), \
             patch.object(LiteratureResearchAgent, "_read_project_text", return_value="Sample research text"):

            agent = LiteratureResearchAgent(active_projects=[sample_project_name], notifier=mock_notifier)
            agent.run()

        # The keyword-extraction call happens first; the summarization call is the one
        # containing the paper data, identifiable by the "Here are the enriched results" marker.
        summarization_prompts = [p for p in captured_prompts if "Here are the enriched results" in p]
        assert summarization_prompts, "Expected a summarization prompt containing paper data"
        payload_prompt = summarization_prompts[0]

        assert "xxxxxxxxxx" * 500 not in payload_prompt  # the raw 5000-char abstract must not survive intact
        assert "…" in payload_prompt  # truncation marker present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_literature_agent.py -v -k test_process_project_truncates_abstracts_before_final_llm_call`
Expected: FAIL — the raw 5000-char abstracts are still present in full (no truncation marker `"…"` in the payload).

- [ ] **Step 3: Wire in the truncation**

In `agents/literature_research_agent.py`, add the import near the top (with the other `utils` imports, agents/literature_research_agent.py:15-16):

```python
from utils.token_budget import truncate_paper_abstracts
```

Then in `_process_project`, immediately after the enrichment call and its not-`all_papers` guard (agents/literature_research_agent.py:235-246), before building `keywords`/`research_data` (agents/literature_research_agent.py:248-249), insert:

```python
        all_papers = truncate_paper_abstracts(
            all_papers,
            total_budget_chars=Config.TOTAL_ABSTRACT_BUDGET_CHARS,
            min_chars=Config.MIN_ABSTRACT_CHARS,
            max_chars=Config.MAX_ABSTRACT_CHARS,
        )
```

So the method reads, in order: enrich → not-`all_papers` guard → truncate → build `keywords` → call `process_results_with_llm`. This also means `links_section` (built from `all_papers` right after `process_results_with_llm`, agents/literature_research_agent.py:254-257) uses the truncated list — harmless, since it only reads `item.get("link")`/`item.get("url")`/`item["title"]`, none of which are touched by truncation.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_literature_agent.py -v -k test_process_project_truncates_abstracts_before_final_llm_call`
Expected: PASS

- [ ] **Step 5: Run the full literature agent test file and full unit suite to check for regressions**

Run: `pytest tests/integration/test_literature_agent.py tests/unit/ -v`
Expected: PASS, no regressions

- [ ] **Step 6: Commit**

```bash
git add agents/literature_research_agent.py tests/integration/test_literature_agent.py
git commit -m "feat: adaptively truncate paper abstracts before final summarization call"
```

---

### Task 5: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the complete test suite**

Run: `pytest tests/ -v`
Expected: PASS, 0 failures (250+ tests — the 4 new test files/classes added in Tasks 1-4 on top of the existing 250)

- [ ] **Step 2: Manually sanity-check sampling on a real downloaded project**

Run this from the project root (with venv activated) to eyeball the sample quality against a real manuscript already on disk from the PQTrace/Udi Aharon test runs:

```bash
python -c "
from utils.overleaf_connector import OverleafConnector
from config import Config
import os

connector = OverleafConnector()
for name in ['PQTrace', 'Udi Aharon PhD Book v2']:
    path = os.path.join(Config.OVERLEAF_DIR, name)
    raw = connector.read_all_tex_files_raw(path)
    sample = connector.extract_representative_sample(raw, Config.MAX_PROJECT_TEXT_CHARS)
    print(f'--- {name} ---')
    print(f'raw: {len(raw)} chars, sample: {len(sample)} chars')
    print(sample[:500])
    print('...')
    print()
"
```

Confirm the sample for "Udi Aharon PhD Book v2" includes headings/content beyond just the introduction (the failure mode this whole feature targets) — visually compare against the truncated 4000-char prefix it produced before this change.

- [ ] **Step 3: Commit if the manual check surfaces no issues**

No code changes expected at this step — this is a verification checkpoint. If the manual check reveals a problem, fix it as a new commit on top of Task 1-4's work before considering the plan complete.
