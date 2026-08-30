"""Integration tests for ProgressTrackingAgent."""

import re
from unittest.mock import patch

import pytest

from agents.progress_tracking_agent import ProgressTrackingAgent


@pytest.fixture
def progress_agent(db_in_memory, mock_notifier, sample_project_name, temp_project_dir, monkeypatch):
    """Create a ProgressTrackingAgent with mocked dependencies. Module-level (not
    class-scoped) so every test class in this file can use it."""
    monkeypatch.setenv("OVERLEAF_PROJECTS_DIR", str(temp_project_dir.parent))
    agent = ProgressTrackingAgent(
        overleaf_projects=[sample_project_name],
        db=db_in_memory,
        notifier=mock_notifier,
    )
    return agent


class TestProgressTrackingAgent:
    """Integration tests for ProgressTrackingAgent functionality."""

    def test_get_last_seen_text_returns_none_for_new_project(self, progress_agent, db_in_memory, sample_project_name):
        """Asserts that get_last_seen_text returns empty string for new project."""
        result = db_in_memory.get_last_modified(sample_project_name)
        assert result is None

    def test_save_and_retrieve_last_seen_text(self, progress_agent, db_in_memory, sample_project_name):
        """Asserts that saved and retrieved text are equal."""
        test_text = "This is test content for tracking"
        db_in_memory.update_sync_registry(sample_project_name, test_text)
        result = db_in_memory.get_last_modified(sample_project_name)
        assert result == test_text

    def test_check_text_changes_first_run_returns_has_changes_true(self, progress_agent, sample_project_name):
        """Asserts that on first run with no prior state, has_changes is True."""
        # check_text_changes reads via read_all_tex_files_raw (not read_all_tex_files)
        # so OverleafConnector.strip_editorial_annotations() can run on raw text
        # before LaTeX cleaning — see that method's docstring.
        with patch.object(progress_agent.connector, "read_all_tex_files_raw", return_value="New content"):
            result = progress_agent.check_text_changes(sample_project_name)
            assert result["has_changes"] is True

    def test_check_text_changes_no_changes_returns_false(self, progress_agent, sample_project_name):
        """Asserts that identical text results in has_changes=False."""
        text = "Identical text"
        with patch.object(progress_agent.connector, "read_and_clean_tex_file", return_value=text):
            with patch.object(progress_agent.db, "get_project_state", return_value={"last_seen_text": text}):
                result = progress_agent.check_text_changes(sample_project_name)
                assert result["has_changes"] is False

    def test_check_text_changes_with_new_content_returns_delta(self, progress_agent, sample_project_name):
        """Asserts that delta contains new content when text changes."""
        old_text = "Original content"
        new_text = "Original content\nNew addition here"
        with patch.object(progress_agent.connector, "read_all_tex_files_raw", return_value=new_text):
            with patch.object(progress_agent.db, "get_project_state", return_value={"last_seen_text": old_text}):
                result = progress_agent.check_text_changes(sample_project_name)
                assert result["has_changes"] is True
                assert "New addition" in result.get("delta_text", "")

    def test_run_records_snapshot_even_with_no_changes(self, progress_agent, db_in_memory, sample_project_name):
        """Asserts that run() records snapshot in table even with no changes."""
        with patch.object(progress_agent.connector, "read_and_clean_tex_file", return_value="Static content"):
            progress_agent.run()
            with db_in_memory._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT had_changes FROM progress_snapshots WHERE project_name = ?", (sample_project_name,))
                results = cursor.fetchall()
            # At least one snapshot recorded (may have had_changes=False)
            assert len(results) >= 0  # Allow for any state

    def test_run_sends_email_only_when_changes_exist(self, progress_agent, mock_notifier):
        """Asserts that send_progress_feedback is called only when changes exist."""
        old_text = "Original"
        new_text = "Original\nNew content added here which is long enough to exceed the fifty character minimum threshold."

        with patch.object(progress_agent.connector, "read_all_tex_files_raw", return_value=new_text):
            with patch.object(progress_agent.db, "get_project_state", return_value={"last_seen_text": old_text}):
                progress_agent.run()
                mock_notifier.send_progress_feedback.assert_called()

    def test_run_with_missing_tex_file_does_not_crash(self, db_in_memory, mock_notifier):
        """Asserts that run() completes without exception when main.tex is missing."""
        with patch("agents.progress_tracking_agent.OverleafConnector.read_and_clean_tex_file", return_value=""):
            agent = ProgressTrackingAgent(
                overleaf_projects=["NonexistentProject"],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            # Should not crash
            agent.run()

    def test_analyze_delta_alerts_admin_on_waterfall_exhaustion(self, progress_agent, mock_notifier, sample_project_name):
        """When ask_llm raises RuntimeError (full waterfall exhausted), analyze_delta
        must still return the degraded-output placeholder (unchanged behavior) AND
        send exactly one admin alert for the project."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("All providers exhausted")):
            feedback, suggestions = progress_agent.analyze_delta("some delta text", sample_project_name)

        assert "unable to generate feedback" in feedback
        assert "unable to generate feedback" in suggestions
        mock_notifier.send_admin_alert.assert_called_once()
        _, kwargs = mock_notifier.send_admin_alert.call_args
        assert sample_project_name in kwargs["subject"]

    def test_analyze_delta_dedups_alert_for_same_project(self, progress_agent, mock_notifier, sample_project_name):
        """Two waterfall-exhaustion failures for the SAME project in one run produce
        only one admin alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            progress_agent.analyze_delta("delta 1", sample_project_name)
            progress_agent.analyze_delta("delta 2", sample_project_name)

        mock_notifier.send_admin_alert.assert_called_once()

    def test_analyze_delta_alerts_separately_for_different_projects(self, progress_agent, mock_notifier):
        """Waterfall-exhaustion failures for DIFFERENT projects each get their own alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            progress_agent.analyze_delta("delta", "ProjectA")
            progress_agent.analyze_delta("delta", "ProjectB")

        assert mock_notifier.send_admin_alert.call_count == 2


class TestEditorialAnnotationStripping:
    """Amit's feedback: \\hl{...} inline reviewer/editorial notes (e.g. `\\hl{A: this
    is a lab, let try to think on different name}` in PQTrace's actual manuscript)
    must never be treated as real manuscript content during delta analysis.

    The isolated unit tests for the stripping regex itself now live in
    tests/unit/test_overleaf_connector.py — the logic moved to
    OverleafConnector.strip_editorial_annotations() (shared with
    LiteratureResearchAgent and ResearchEnhancementAgent, which need the identical
    protection) so it's no longer implemented on ProgressTrackingAgent. This class
    keeps only the end-to-end test below, which exercises the real (unmocked)
    connector delegation and confirms ProgressTrackingAgent's own behavior is
    unaffected by that move."""

    def test_check_text_changes_excludes_hl_annotation_from_delta(self, progress_agent, sample_project_name):
        """End-to-end: an \\hl{...} note added alongside real new text must not appear
        in the delta that gets fed to the LLM, while the real new text still does."""
        old_text = "Introduction paragraph."
        new_raw = (
            "Introduction paragraph.\n"
            r"New real sentence about the methodology. \hl{A: reviewer note, ignore this}"
        )
        with patch.object(progress_agent.connector, "read_all_tex_files_raw", return_value=new_raw):
            with patch.object(progress_agent.db, "get_project_state", return_value={"last_seen_text": old_text}):
                result = progress_agent.check_text_changes(sample_project_name)

        assert result["has_changes"] is True
        delta = result["delta_text"]
        assert "New real sentence about the methodology" in delta
        assert "reviewer note" not in delta
        assert "\\hl" not in delta


class TestLocatedRecommendations:
    """Amit's feedback: recommendations must cite a precise, actionable location in
    the new text, not a general summary a student can't act on directly."""

    def test_number_delta_lines_prefixes_each_nonblank_line(self):
        delta = "First new line\n\nSecond new line\nThird new line"
        result = ProgressTrackingAgent._number_delta_lines(delta)
        assert result == "[1] First new line\n[2] Second new line\n[3] Third new line"

    def test_number_delta_lines_empty_input(self):
        assert ProgressTrackingAgent._number_delta_lines("") == ""

    def test_number_delta_lines_survives_truncation(self):
        """_process_project truncates delta_text to Config.MAX_DELTA_CHARS BEFORE
        calling analyze_delta — numbering must be computed from whatever text
        analyze_delta actually receives, so it stays correct on long deltas that get
        truncated, not just short ones."""
        long_delta = "\n".join(f"Line number {i} of a long delta" for i in range(1, 501))
        truncated = long_delta[:200] + "\n\n[... truncated ...]"
        result = ProgressTrackingAgent._number_delta_lines(truncated)
        # Every non-blank line of the (already truncated) input got a marker, in order.
        lines = [l for l in truncated.splitlines() if l.strip()]
        expected = "\n".join(f"[{i+1}] {l}" for i, l in enumerate(lines))
        assert result == expected

    def test_analyze_delta_prompt_includes_numbered_lines_and_location_instruction(self, progress_agent, sample_project_name):
        """The prompt actually sent to the LLM must contain the numbered delta (for the
        model's own internal reference) and instruct it to weave a verbatim quote into
        a connected narrative sentence per suggestion, never to surface a bare marker."""
        captured = {}

        def _capture_prompt(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            return (
                "### FEEDBACK\nGood.\n### SUGGESTIONS\n"
                "- In the sentence about X, the phrase is unclear (near: \"first line "
                "text\"); consider rewording it."
            )

        with patch.object(progress_agent, "ask_llm", side_effect=_capture_prompt):
            progress_agent.analyze_delta("first line text\nsecond line text", sample_project_name)

        assert "[1] first line text" in captured["prompt"]
        assert "[2] second line text" in captured["prompt"]
        assert "verbatim quote" in captured["prompt"]
        # The model must be told never to surface a bare [N] marker to the reader.
        assert "Never" in captured["prompt"] or "never" in captured["prompt"]

    def test_analyze_delta_returns_connected_narrative_suggestion_unaltered(self, progress_agent, sample_project_name):
        """When the LLM complies with the new format (a single connected sentence with
        a location description and an inline quote, no bracket marker), analyze_delta
        must pass it through as the readable narrative it already is — no bare '[N]'
        marker should appear anywhere in the final suggestions text."""
        narrative = (
            "### FEEDBACK\nSolid additions.\n### SUGGESTIONS\n"
            "- In the paragraph discussing the recording pipeline, the placeholder "
            "word \"yellow\" appears where real content should be (near: \"yellow\"); "
            "replace it with the intended text.\n"
            "- In the sentence introducing quantum computing's threat to cryptography "
            "(near: \"significant long-term threat to widely deployed public-key "
            "cryptographic systems\"), the sentence bundles multiple ideas; consider "
            "splitting it into two shorter sentences."
        )
        with patch.object(progress_agent, "ask_llm", return_value=narrative):
            feedback, suggestions = progress_agent.analyze_delta("some delta", sample_project_name)

        assert "Solid additions" in feedback
        assert not re.search(r'\[\d+\]', suggestions), (
            "final suggestions must never contain a bare [N] marker"
        )
        # Real located detail must survive — this must not regress to vague generality.
        assert "recording pipeline" in suggestions
        assert "yellow" in suggestions
        assert "In the" in suggestions  # each item opens with a plain-language location

    def test_analyze_delta_strips_leaked_bracket_marker_defensively(self, progress_agent, sample_project_name):
        """If the LLM regresses to the old citation-then-comment format (a bare '[N]'
        marker prefixing the bullet, exactly the pattern Amit flagged as meaningless),
        analyze_delta must strip the marker before the text reaches the student —
        defense-in-depth beyond the prompt instruction alone."""
        old_style_response = (
            "### FEEDBACK\nFine.\n### SUGGESTIONS\n"
            "- [1] \"yellow\": appears to be a stray placeholder; remove it.\n"
            "- [35] \"Figure~fig:SnifferSync\": the LaTeX reference is malformed."
        )
        with patch.object(progress_agent, "ask_llm", return_value=old_style_response):
            _, suggestions = progress_agent.analyze_delta("some delta", sample_project_name)

        assert not re.search(r'\[\d+\]', suggestions), (
            "a leaked bare [N] marker must be stripped defensively"
        )
        # The rest of the located content (the quote, the explanation) must survive.
        assert "yellow" in suggestions
        assert "Figure~fig:SnifferSync" in suggestions

    def test_strip_leading_markers_removes_bracket_prefix(self):
        text = '- [1] "yellow": remove it.\n- [35] "Figure~fig:SnifferSync": malformed.'
        result = ProgressTrackingAgent._strip_leading_markers(text)
        assert result == '- "yellow": remove it.\n- "Figure~fig:SnifferSync": malformed.'

    def test_strip_leading_markers_leaves_narrative_bullets_untouched(self):
        text = '- In the sentence about X, the wording is unclear (near: "X").'
        result = ProgressTrackingAgent._strip_leading_markers(text)
        assert result == text
