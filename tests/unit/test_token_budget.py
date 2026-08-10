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
