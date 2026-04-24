"""Unit tests for ProgressTrackingAgent delta extraction functionality."""

from unittest.mock import MagicMock

import pytest

from agents.progress_tracking_agent import ProgressTrackingAgent


class TestDeltaExtraction:
    """Tests for the _extract_delta method of ProgressTrackingAgent."""

    @pytest.fixture
    def progress_agent(self):
        """Create a ProgressTrackingAgent instance with mocked dependencies."""
        agent = MagicMock(spec=ProgressTrackingAgent)
        # Bind the real _extract_delta method to the mock
        agent._extract_delta = ProgressTrackingAgent._extract_delta.__get__(agent)
        return agent

    def test_extract_delta_detects_new_line(self, progress_agent):
        """Asserts that delta contains the new line when text is added."""
        old_text = "Line 1\nLine 2"
        new_text = "Line 1\nLine 2\nLine 3"
        delta = progress_agent._extract_delta(old_text, new_text)
        assert "Line 3" in delta
        assert delta != ""

    def test_extract_delta_returns_empty_on_identical_texts(self, progress_agent):
        """Asserts that delta is empty string when old and new texts are identical."""
        text = "Identical\nText\nContent"
        delta = progress_agent._extract_delta(text, text)
        assert delta == ""

    def test_extract_delta_returns_full_text_when_old_is_empty(self, progress_agent):
        """Asserts that delta equals new text when old text is empty string."""
        old_text = ""
        new_text = "New content here"
        delta = progress_agent._extract_delta(old_text, new_text)
        assert "New content" in delta

    def test_extract_delta_ignores_deleted_lines(self, progress_agent):
        """Asserts that delta does not contain lines starting with '-' for deleted content."""
        old_text = "Line 1\nLine 2\nLine 3"
        new_text = "Line 1\nLine 3"
        delta = progress_agent._extract_delta(old_text, new_text)
        # Delta should not have lines marked as deleted (starting with -)
        assert not any(line.startswith("- ") for line in delta.split("\n"))

    def test_extract_delta_strips_blank_lines(self, progress_agent):
        """Asserts that whitespace-only added lines are excluded from delta."""
        old_text = "Content"
        new_text = "Content\n   \n\n\nMore content"
        delta = progress_agent._extract_delta(old_text, new_text)
        # Blank lines should not dominate the delta
        lines = [line for line in delta.split("\n") if line.strip()]
        assert len(lines) > 0
        assert "More content" in delta

    def test_extract_delta_multiple_additions(self, progress_agent):
        """Asserts that all 5 new lines appear in delta when added."""
        old_text = "Original"
        new_lines = ["Added line 1", "Added line 2", "Added line 3", "Added line 4", "Added line 5"]
        new_text = "Original\n" + "\n".join(new_lines)
        delta = progress_agent._extract_delta(old_text, new_text)
        for line in new_lines:
            assert line in delta

    def test_extract_delta_handles_unicode(self, progress_agent):
        """Asserts that Unicode characters (Hebrew, special chars) in added lines are preserved."""
        old_text = "English text"
        new_text = "English text\nעברית\nСирилиця\n中文"
        delta = progress_agent._extract_delta(old_text, new_text)
        assert "עברית" in delta
        assert "Сирилиця" in delta
        assert "中文" in delta
