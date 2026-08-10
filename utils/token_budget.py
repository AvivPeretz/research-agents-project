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
