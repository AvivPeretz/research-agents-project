# Literature Agent Token Efficiency — Design

Date: 2026-08-09
Status: Approved for planning

## Problem

`LiteratureResearchAgent` makes several LLM calls per project, two of which scale
poorly with manuscript/result size and were implicated in a real failure during the
first PQTrace / Udi Aharon PhD Book v2 test run (2026-08-09):

1. **Manuscript sampling** (`_read_project_text` in
   `agents/literature_research_agent.py:35`) truncates the concatenated `.tex` text to
   the first `Config.MAX_PROJECT_TEXT_CHARS` (4000) characters before keyword
   extraction. For long documents this captures only the front matter — for Udi
   Aharon's 39,744-char manuscript, the sample was entirely introductory material,
   producing lower-quality search keywords than a sample spanning the whole document
   would.

2. **Paper-data payload** (`process_results_with_llm` at
   `agents/literature_research_agent.py:111`) serializes up to 15 enriched papers
   (each carrying a full abstract from Semantic Scholar/OpenAlex) into one JSON blob
   for a single summarization call. This call is what actually broke on Udi Aharon:
   Groq rejected the request with `Limit 8000, Requested 8149` (tokens per minute),
   and since the Gemini and OpenAI fallbacks were also broken at the time (see
   project memory `project_next_session_tasks.md` Task 1), the entire literature
   phase failed for that project with no papers and no email sent.

Goal: maximize the model's understanding of (a) the project's own research and (b)
the fetched related papers, while using the fewest tokens necessary and keeping
requests reliably under Groq's per-request token ceiling regardless of manuscript
length or paper count.

## Part A — Structural manuscript sampling

Replace the blind character-prefix truncation with a sample built from the
manuscript's actual structure, so the keyword-extraction LLM call sees the whole
document's shape instead of just its opening.

**New method**: `OverleafConnector.extract_representative_sample(raw_tex: str, max_chars: int) -> str`

Operates on the **raw**, uncleaned LaTeX (before `clean_latex_text` strips commands),
because structural markers (`\section{...}`, `\chapter{...}`, `\subsection{...}`,
`\subsubsection{...}`, `\begin{abstract}...\end{abstract}`) must still be intact to
detect document structure.

Algorithm:
1. Extract the `abstract` environment content in full, if present (small, highest
   signal — always included first).
2. Scan for all `\chapter`, `\section`, `\subsection`, `\subsubsection` commands in
   document order. For each: include the heading text itself, plus the first ~2
   sentences of body text that follow it (regex sentence split on `. ` / `.\n`,
   capped at a fixed char count per heading, e.g. 300 chars) — enough to convey what
   that part of the document covers without dumping its full content.
3. If characters remain under `max_chars` after steps 1-2, extend the **last**
   detected section (typically conclusion/discussion in an academic document) with
   additional trailing content, since it best reflects the current state/framing of
   the work.
4. Run `clean_latex_text()` over the assembled sample (same cleaning already used
   elsewhere) before returning it.
5. Hard cap the final result at `max_chars` regardless (safety net).
6. **Fallback**: if no structural markers are found anywhere in the raw text
   (unstructured single-file document), return `clean_latex_text(raw_tex)[:max_chars]`
   — today's behavior, unchanged. No regression for documents that don't use
   sectioning commands.

**Call site change**: `_read_project_text()` in `literature_research_agent.py` calls
`self.connector.extract_representative_sample(raw_text, max_chars)` instead of
`text_content[:max_chars]`. `read_all_tex_files` needs a variant/flag that returns
raw (uncleaned) concatenated text for this to operate on, alongside the existing
cleaned-text path used elsewhere (e.g. `ProgressTrackingAgent`, which must keep
getting fully cleaned text unchanged).

`Config.MAX_PROJECT_TEXT_CHARS` (4000) is reused unchanged as the sample budget —
this is a smarter allocation of the same budget, not a bigger one.

## Part B — Adaptive paper-abstract truncation

Cap the total size of the paper-data JSON payload sent to `process_results_with_llm`,
scaling the per-paper truncation to the actual number of papers in that call so the
total stays safely under Groq's per-request token ceiling no matter how many papers
were fetched.

**Formula** (derived from the actual failure: `Requested 8149` tokens for ~10 papers
implies abstracts were averaging 2000+ characters each):

```
TOTAL_ABSTRACT_BUDGET_CHARS = 16000   # targets ~6000 tokens for the full payload,
                                        # leaving ~25% margin under Groq's 8000 TPM
                                        # ceiling for prompt instructions, keywords,
                                        # JSON structure, and other fields per paper
MIN_ABSTRACT_CHARS = 300
MAX_ABSTRACT_CHARS = 1200

per_paper_cap = clamp(TOTAL_ABSTRACT_BUDGET_CHARS / num_papers, MIN_ABSTRACT_CHARS, MAX_ABSTRACT_CHARS)
```

Applied in `process_results_with_llm` (or immediately before it, in `_process_project`)
to whichever field holds the abstract text on each paper dict — `abstract` or
`snippet`, whichever is present and non-empty — before `json.dumps(scholar_data)`.
Truncation is a straight character slice with a trailing ellipsis marker (e.g. `…`)
so the LLM can tell the abstract was cut short rather than reading it as complete.
Title, year, venue, citationCount, url are untouched (already short, high value).

These three constants live in `config.py` next to `MAX_PROJECT_TEXT_CHARS`, not
hardcoded in the agent, matching the existing config convention in this codebase.

## Non-goals / explicitly out of scope

- Not changing the 15-paper pool cap or the relevance filter.
- Not batching the paper-summarization call into multiple smaller LLM calls (rejected
  during design discussion — adds calls/latency/failure surface instead of reducing
  them).
- Not addressing the broader multi-LLM waterfall reliability issue (Gemini 404,
  OpenAI no credits) — tracked separately in project memory, out of scope here.
- Not addressing parallel-project token contention (`ThreadPoolExecutor` runs
  multiple projects' literature cycles concurrently, and Groq's TPM limit is
  account-wide, not per-request) — this design reduces the size of any single
  request but does not coordinate concurrent requests against the shared quota.
  Worth flagging as a future concern if failures persist after this change.

## Testing

- Unit tests for `extract_representative_sample`: a structured multi-section
  document produces a sample spanning headings from across the document (not just
  the prefix); an unstructured document falls back to prefix truncation; output
  never exceeds `max_chars`.
- Unit tests for the adaptive abstract-truncation formula: verify the
  clamp boundaries (very few papers → capped at `MAX_ABSTRACT_CHARS`; many papers →
  capped at `MIN_ABSTRACT_CHARS`); verify already-short abstracts are left untouched
  (no padding, no unnecessary truncation marker).
- Existing test suite (`tests/unit`, `tests/integration` for `LiteratureAgent`) must
  continue passing — these two changes must not alter the agent's external behavior
  (schema, email content shape, file outputs) beyond input sizing.
