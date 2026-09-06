"""Integration tests for ResearchEnhancementAgent."""

from unittest.mock import MagicMock, patch

import pytest

from agents.research_enhancement_agent import ResearchEnhancementAgent


class TestResearchEnhancementAgent:
    """Integration tests for ResearchEnhancementAgent Stanford workflow."""

    @pytest.fixture
    def enhancement_agent(self, db_in_memory, mock_notifier, sample_project_name):
        """Create a ResearchEnhancementAgent with mocked dependencies."""
        agent = ResearchEnhancementAgent(
            overleaf_projects=[sample_project_name],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        return agent

    def test_get_stanford_state_returns_ready_for_new_project(self, enhancement_agent, db_in_memory, sample_project_name):
        """Asserts that fresh DB returns 'READY_FOR_UPLOAD' status."""
        db_in_memory.add_project(sample_project_name, "test@example.com")
        result = db_in_memory.get_project_state(sample_project_name)
        assert result["stanford_status"] == "READY_FOR_UPLOAD"

    def test_update_stanford_state_persists_to_db(self, enhancement_agent, db_in_memory, sample_project_name):
        """Asserts that state update persists and can be retrieved."""
        db_in_memory.add_project(sample_project_name, "test@example.com")
        db_in_memory.update_project_state(sample_project_name, stanford_status="WAITING_FOR_REVIEW")
        result = db_in_memory.get_project_state(sample_project_name)
        assert result["stanford_status"] == "WAITING_FOR_REVIEW"

    def test_stanford_token_persists_to_db(self, db_in_memory, sample_project_name):
        """Asserts that stanford_token can be written and read back via the slim state getter."""
        db_in_memory.add_project(sample_project_name, "test@example.com")
        db_in_memory.update_project_state(sample_project_name, stanford_token="tok_abc123xyz")
        result = db_in_memory.get_project_state_slim(sample_project_name)
        assert result["stanford_token"] == "tok_abc123xyz"

    def test_get_project_pdf_path_finds_pdf(self, enhancement_agent, tmp_path, monkeypatch):
        """Asserts that PDF path is returned when file exists."""
        from config import Config
        project_name = "test_project"
        project_dir = tmp_path / project_name
        project_dir.mkdir()
        pdf_file = project_dir / "paper.pdf"
        pdf_file.write_text("fake pdf")
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))
        result = enhancement_agent._get_project_pdf_path(project_name)
        assert result == str(pdf_file)

    def test_get_project_pdf_path_returns_none_when_missing(self, enhancement_agent, tmp_path, monkeypatch):
        """Asserts that None is returned when PDF does not exist."""
        from config import Config
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(tmp_path))
        result = enhancement_agent._get_project_pdf_path("empty_project")
        assert result is None

    def test_upload_to_stanford_returns_none_on_invalid_path(self, enhancement_agent):
        """Asserts that upload returns None when pdf_path is None."""
        result = enhancement_agent.upload_to_stanford(project_name="Test", pdf_path=None)
        assert result is None

    def test_generate_actionable_tasks_calls_llm(self, enhancement_agent, sample_project_name):
        """Asserts that LLM is called during task generation."""
        with patch("agents.base_agent.BaseAgent.ask_llm", return_value="Task 1\nTask 2"):
            result = enhancement_agent._generate_actionable_tasks(project_name=sample_project_name, review_text="Test review")
            assert result is not None

    def test_generate_actionable_tasks_handles_empty_review(self, enhancement_agent, sample_project_name):
        """Asserts that empty review text returns None."""
        result = enhancement_agent._generate_actionable_tasks(project_name=sample_project_name, review_text="")
        assert result is None

    def test_generate_actionable_tasks_alerts_admin_on_waterfall_exhaustion(
        self, enhancement_agent, mock_notifier, sample_project_name
    ):
        """LLM failure policy (agents/base_agent.py): when ask_llm raises RuntimeError
        (full waterfall exhausted), the method must return None — a sentinel the
        caller cannot mistake for real content — NOT a truthy placeholder string.
        A previous version's placeholder string was silently treated as success by
        the caller's `if tasks is not None and tasks.strip():` guard, marking the
        project REVIEW_COMPLETED and emailing the placeholder to the student as if
        it were their real action plan. Must still send exactly one admin alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("All providers exhausted")):
            result = enhancement_agent._generate_actionable_tasks(
                project_name=sample_project_name, review_text="Test review"
            )

        assert result is None
        mock_notifier.send_admin_alert.assert_called_once()
        _, kwargs = mock_notifier.send_admin_alert.call_args
        assert sample_project_name in kwargs["subject"]

    def test_generate_actionable_tasks_dedups_alert_for_same_project(
        self, enhancement_agent, mock_notifier, sample_project_name
    ):
        """Two waterfall-exhaustion failures for the SAME project in one run produce
        only one admin alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            enhancement_agent._generate_actionable_tasks(project_name=sample_project_name, review_text="review 1")
            enhancement_agent._generate_actionable_tasks(project_name=sample_project_name, review_text="review 2")

        mock_notifier.send_admin_alert.assert_called_once()

    def test_generate_actionable_tasks_alerts_separately_for_different_projects(
        self, enhancement_agent, mock_notifier
    ):
        """Waterfall-exhaustion failures for DIFFERENT projects each get their own alert."""
        with patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("exhausted")):
            enhancement_agent._generate_actionable_tasks(project_name="ProjectA", review_text="review")
            enhancement_agent._generate_actionable_tasks(project_name="ProjectB", review_text="review")

        assert mock_notifier.send_admin_alert.call_count == 2


class TestStanfordReviewTieringAndFormat:
    """Amit's feedback: the Stanford review output must be tiered by severity/
    importance and formatted as a document suitable to hand directly to the student
    author — not a flat pass-through of the raw review."""

    @pytest.fixture
    def enhancement_agent(self, db_in_memory, mock_notifier, sample_project_name):
        agent = ResearchEnhancementAgent(
            overleaf_projects=[sample_project_name],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        return agent

    def test_prompt_requires_severity_tiers(self, enhancement_agent, sample_project_name):
        """The prompt sent to the LLM must require Critical/Important/Minor tiers,
        not a flat numbered list."""
        captured = {}

        def _capture(prompt, *a, **kw):
            captured["prompt"] = prompt
            return "## Novelty & Innovation\nGood.\n## 🔴 Critical\n1. Fix X.\n## 🟡 Important\n1. Fix Y.\n## 🟢 Minor\n1. Fix Z."

        with patch.object(enhancement_agent, "ask_llm", side_effect=_capture):
            enhancement_agent._generate_actionable_tasks(sample_project_name, "raw stanford review text")

        prompt = captured["prompt"]
        assert "Critical" in prompt
        assert "Important" in prompt
        assert "Minor" in prompt

    def test_prompt_frames_output_for_student_not_internal_dump(self, enhancement_agent, sample_project_name):
        """The prompt must explicitly frame the output as student-facing, not an
        internal engineering/PM task table (the old prompt said 'Academic Research
        Manager' producing a 'Task Description/Estimated Effort/Recommended Deadline'
        table — internal-reader framing)."""
        captured = {}

        def _capture(prompt, *a, **kw):
            captured["prompt"] = prompt
            return "## Novelty & Innovation\nGood."

        with patch.object(enhancement_agent, "ask_llm", side_effect=_capture):
            enhancement_agent._generate_actionable_tasks(sample_project_name, "raw stanford review text")

        prompt = captured["prompt"].lower()
        assert "student" in prompt

    def test_generate_actionable_tasks_still_returns_llm_output_unchanged_shape(
        self, enhancement_agent, sample_project_name
    ):
        """The return value and save-to-disk behavior are unchanged — this is a
        prompt-only redesign, not a schema/parsing change downstream."""
        with patch.object(enhancement_agent, "ask_llm", return_value="## Novelty & Innovation\nGood."):
            result = enhancement_agent._generate_actionable_tasks(sample_project_name, "raw review")
        assert result == "## Novelty & Innovation\nGood."


class TestStanfordReviewCycleComparison:
    """Amit's feedback: compare a new Stanford review against the previous one for
    the same project and surface whether previously-raised points were addressed."""

    @pytest.fixture
    def enhancement_agent(self, db_in_memory, mock_notifier, sample_project_name):
        agent = ResearchEnhancementAgent(
            overleaf_projects=[sample_project_name],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        return agent

    def test_no_previous_review_omits_comparison_section(self, enhancement_agent, sample_project_name):
        """First review cycle (no previous_review_text) must NOT ask for a comparison
        section — there's nothing to compare against yet."""
        captured = {}

        def _capture(prompt, *a, **kw):
            captured["prompt"] = prompt
            return "## Novelty & Innovation\nGood."

        with patch.object(enhancement_agent, "ask_llm", side_effect=_capture):
            enhancement_agent._generate_actionable_tasks(sample_project_name, "current review", previous_review_text=None)

        assert "Progress Since Last Review" not in captured["prompt"]
        assert "PREVIOUS STANFORD REVIEW" not in captured["prompt"]

    def test_previous_review_triggers_comparison_section(self, enhancement_agent, sample_project_name):
        """Second+ review cycle (previous_review_text provided) must include both the
        previous review text and instructions to compare against it."""
        captured = {}

        def _capture(prompt, *a, **kw):
            captured["prompt"] = prompt
            return "## Novelty & Innovation\nGood.\n## Progress Since Last Review\n..."

        with patch.object(enhancement_agent, "ask_llm", side_effect=_capture):
            enhancement_agent._generate_actionable_tasks(
                sample_project_name, "current review text", previous_review_text="OLD review text: missing baselines"
            )

        prompt = captured["prompt"]
        assert "Progress Since Last Review" in prompt
        assert "OLD review text: missing baselines" in prompt

    def test_process_project_reads_previous_review_before_saving_new_one(
        self, enhancement_agent, db_in_memory, sample_project_name, mock_notifier
    ):
        """End-to-end: on a second review cycle, the PREVIOUS review (already in DB)
        is passed to task generation, and the NEW review gets appended to history
        afterward — not before, so 'previous' never means the review being processed."""
        from datetime import datetime

        db_in_memory.add_project(sample_project_name, "researcher@example.com")
        db_in_memory.save_stanford_review(sample_project_name, "FIRST review cycle text")
        db_in_memory.update_project_state(
            sample_project_name,
            stanford_status="WAITING_FOR_REVIEW",
            stanford_token="tok_123",
            last_upload_time=datetime.now().isoformat(),  # recent, so the 48h-stuck alert path doesn't fire
        )

        captured = {}

        def _capture(prompt, *a, **kw):
            captured["prompt"] = prompt
            return "## Novelty & Innovation\nGood."

        with patch.object(enhancement_agent, "_fetch_review_from_stanford", return_value="SECOND review cycle text"), \
             patch.object(enhancement_agent, "ask_llm", side_effect=_capture):
            enhancement_agent._process_project(sample_project_name)

        assert "FIRST review cycle text" in captured["prompt"]
        # The new review must now be the latest in history (appended, not overwritten).
        assert db_in_memory.get_latest_stanford_review(sample_project_name) == "SECOND review cycle text"

    def test_process_project_skips_completion_and_email_on_task_generation_failure(
        self, enhancement_agent, db_in_memory, sample_project_name, mock_notifier
    ):
        """LLM failure policy end-to-end: when task generation waterfall-exhausts
        during Phase 2, the project must NOT transition to REVIEW_COMPLETED, the
        fetched review must NOT be saved to stanford_review_history (both would
        permanently mark this review cycle as consumed with no real tasks ever
        generated for it), and no email must be sent — regression test for the real
        bug where a placeholder string was treated as success. State must remain
        WAITING_FOR_REVIEW so the next run retries against the same token."""
        from datetime import datetime

        db_in_memory.add_project(sample_project_name, "researcher@example.com")
        db_in_memory.update_project_state(
            sample_project_name,
            stanford_status="WAITING_FOR_REVIEW",
            stanford_token="tok_123",
            last_upload_time=datetime.now().isoformat(),
        )

        with patch.object(enhancement_agent, "_fetch_review_from_stanford", return_value="A real fetched review"), \
             patch("agents.base_agent.BaseAgent.ask_llm", side_effect=RuntimeError("All providers exhausted")):
            enhancement_agent._process_project(sample_project_name)

        state = db_in_memory.get_project_state_slim(sample_project_name)
        assert state["stanford_status"] == "WAITING_FOR_REVIEW"
        assert db_in_memory.get_latest_stanford_review(sample_project_name) is None
        mock_notifier.send_stanford_tasks.assert_not_called()
        mock_notifier.send_admin_alert.assert_called_once()

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM agent_runs WHERE project_name = ? ORDER BY id DESC LIMIT 1",
                (sample_project_name,)
            )
            row = cursor.fetchone()
        assert row["status"] == "FAILURE"

    def test_database_manager_review_history_is_append_only(self, db_in_memory, sample_project_name):
        """save_stanford_review appends; get_latest_stanford_review always returns the
        most recently saved row, and earlier cycles are never overwritten or lost."""
        db_in_memory.add_project(sample_project_name, "researcher@example.com")
        assert db_in_memory.get_latest_stanford_review(sample_project_name) is None

        db_in_memory.save_stanford_review(sample_project_name, "cycle 1 text")
        assert db_in_memory.get_latest_stanford_review(sample_project_name) == "cycle 1 text"

        db_in_memory.save_stanford_review(sample_project_name, "cycle 2 text")
        assert db_in_memory.get_latest_stanford_review(sample_project_name) == "cycle 2 text"

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM stanford_review_history WHERE project_name = ?",
                (sample_project_name,)
            )
            assert cursor.fetchone()[0] == 2  # both cycles preserved, not overwritten

class TestNoPdfAvailableForReviewFallback:
    """A1 regression tests: README/silent-failure fix — when a project is
    READY_FOR_UPLOAD but _get_project_pdf_path() finds no PDF on disk (almost
    always a symptom of DataIngestionAgent failing to obtain a compiled PDF
    upstream), _process_project must increment upload_failures, alert an admin
    with wording distinct from a generic Stanford-upload-failure alert, and
    either retry later (below threshold, SKIPPED) or fall back to the internal
    review pipeline (at/above threshold)."""

    @pytest.fixture
    def enhancement_agent(self, db_in_memory, mock_notifier, sample_project_name):
        agent = ResearchEnhancementAgent(
            overleaf_projects=[sample_project_name],
            db=db_in_memory,
            notifier=mock_notifier,
        )
        return agent

    def test_no_pdf_below_threshold_increments_failures_and_alerts_skipped(
        self, enhancement_agent, db_in_memory, sample_project_name, mock_notifier
    ):
        """Below Config.STANFORD_MAX_UPLOAD_RETRIES: upload_failures increments by
        exactly 1, a 'No PDF Available' admin alert fires (distinct wording from a
        generic Stanford-upload-failure alert), and log_agent_run is called with
        status=SKIPPED (not SUCCESS) for this project this run."""
        from config import Config

        db_in_memory.add_project(sample_project_name, "researcher@example.com")
        db_in_memory.update_project_state(
            sample_project_name, stanford_status="READY_FOR_UPLOAD", stanford_upload_failures=0
        )
        assert Config.STANFORD_MAX_UPLOAD_RETRIES >= 2  # otherwise this scenario can't be "below threshold"

        with patch.object(enhancement_agent, "_get_project_pdf_path", return_value=None), \
             patch.object(enhancement_agent, "_run_internal_review") as mock_fallback:
            enhancement_agent._process_project(sample_project_name)

        mock_fallback.assert_not_called()

        state = db_in_memory.get_project_state_slim(sample_project_name)
        assert state["stanford_status"] == "READY_FOR_UPLOAD"
        assert state["stanford_upload_failures"] == 1

        mock_notifier.send_admin_alert.assert_called_once()
        _, kwargs = mock_notifier.send_admin_alert.call_args
        assert "No PDF Available" in kwargs["subject"]
        assert sample_project_name in kwargs["subject"]
        # Wording must point at the ingestion side, distinguishing this from a
        # generic Stanford-upload-failure alert.
        assert "Overleaf" in kwargs["message"] or "ingestion" in kwargs["message"].lower() \
            or "DataIngestionAgent" in kwargs["message"]

        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM agent_runs WHERE project_name = ? ORDER BY id DESC LIMIT 1",
                (sample_project_name,)
            )
            row = cursor.fetchone()
        assert row["status"] == "SKIPPED"

    def test_no_pdf_at_threshold_resets_failures_and_triggers_internal_review(
        self, enhancement_agent, db_in_memory, sample_project_name, mock_notifier
    ):
        """At/above Config.STANFORD_MAX_UPLOAD_RETRIES: upload_failures resets to 0,
        a second/different admin alert fires, and _run_internal_review is actually
        invoked for the project."""
        from config import Config

        db_in_memory.add_project(sample_project_name, "researcher@example.com")
        starting_failures = Config.STANFORD_MAX_UPLOAD_RETRIES - 1
        db_in_memory.update_project_state(
            sample_project_name,
            stanford_status="READY_FOR_UPLOAD",
            stanford_upload_failures=starting_failures,
        )

        with patch.object(enhancement_agent, "_get_project_pdf_path", return_value=None), \
             patch.object(enhancement_agent, "_run_internal_review", return_value=True) as mock_fallback:
            enhancement_agent._process_project(sample_project_name)

        mock_fallback.assert_called_once_with(sample_project_name)

        state = db_in_memory.get_project_state_slim(sample_project_name)
        assert state["stanford_upload_failures"] == 0

        mock_notifier.send_admin_alert.assert_called_once()
        _, kwargs = mock_notifier.send_admin_alert.call_args
        assert "No PDF Available" in kwargs["subject"]
        assert sample_project_name in kwargs["subject"]
        # This alert's message must differ from the below-threshold one (mentions
        # the internal fallback being activated, not just "will retry").
        assert "internal" in kwargs["message"].lower()

        # Falls through to the bottom-of-method SUCCESS log (mirrors the sibling
        # genuine-Stanford-failure branch's existing pattern), since
        # _run_internal_review itself is mocked here.
        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM agent_runs WHERE project_name = ? ORDER BY id DESC LIMIT 1",
                (sample_project_name,)
            )
            row = cursor.fetchone()
        assert row["status"] == "SUCCESS"

    # ─── no-PDF-review alert throttle tests (mirrors the ingestion-side
    #     pdf_alert_last_sent_at throttle test structure exactly, but against
    #     the distinct no_pdf_review_alert_last_sent_at column) ─────────────

    def test_no_pdf_alert_throttled_within_window_then_resent_after_window(
        self, enhancement_agent, db_in_memory, sample_project_name, mock_notifier, monkeypatch
    ):
        """First no-PDF failure sends the admin alert. An immediate second no-PDF
        failure within the 24h throttle window must NOT re-send it. A failure
        occurring after the window has elapsed (simulated by moving the stored
        no_pdf_review_alert_last_sent_at timestamp into the past) DOES re-send it.

        Config.STANFORD_MAX_UPLOAD_RETRIES is monkeypatched up so this test can
        exercise multiple below-threshold ("SKIPPED, will retry") runs in a row —
        the default (2) only allows a single below-threshold run before the
        internal-review fallback takes over, which isn't enough to demonstrate
        the throttle across repeated below-threshold failures."""
        from datetime import datetime, timedelta
        from config import Config

        monkeypatch.setattr(Config, "STANFORD_MAX_UPLOAD_RETRIES", 5)

        db_in_memory.add_project(sample_project_name, "researcher@example.com")
        db_in_memory.update_project_state(
            sample_project_name, stanford_status="READY_FOR_UPLOAD", stanford_upload_failures=0
        )

        # --- Run 1: first detection -> alert sent, timestamp persisted ---
        with patch.object(enhancement_agent, "_get_project_pdf_path", return_value=None), \
             patch.object(enhancement_agent, "_run_internal_review"):
            enhancement_agent._process_project(sample_project_name)

        assert mock_notifier.send_admin_alert.call_count == 1
        state = db_in_memory.get_project_state(sample_project_name)
        persisted_ts = state["no_pdf_review_alert_last_sent_at"]
        assert persisted_ts is not None, (
            "Expected no_pdf_review_alert_last_sent_at to be persisted after the first alert"
        )

        # --- Run 2: still failing, WITHIN the throttle window ---
        with patch.object(enhancement_agent, "_get_project_pdf_path", return_value=None), \
             patch.object(enhancement_agent, "_run_internal_review"):
            enhancement_agent._process_project(sample_project_name)

        assert mock_notifier.send_admin_alert.call_count == 1, (
            "Alert must NOT be re-sent within the throttle window"
        )

        # --- Run 3: same failure, but the stored timestamp is now > 24h old ---
        stale_ts = (datetime.now() - timedelta(hours=25)).isoformat()
        db_in_memory.update_project_state(
            sample_project_name, no_pdf_review_alert_last_sent_at=stale_ts
        )
        with patch.object(enhancement_agent, "_get_project_pdf_path", return_value=None), \
             patch.object(enhancement_agent, "_run_internal_review"):
            enhancement_agent._process_project(sample_project_name)

        assert mock_notifier.send_admin_alert.call_count == 2, (
            "Alert MUST be re-sent once the throttle window has elapsed"
        )

    def test_no_pdf_alert_clears_on_internal_review_fallback(
        self, enhancement_agent, db_in_memory, sample_project_name, mock_notifier
    ):
        """When the failure count reaches the internal-review fallback threshold,
        the stored no_pdf_review_alert_last_sent_at timestamp is cleared back to
        None -- a later, genuinely new no-PDF occurrence should alert immediately
        rather than inheriting a stale cooldown."""
        from datetime import datetime
        from config import Config

        db_in_memory.add_project(sample_project_name, "researcher@example.com")
        db_in_memory.update_project_state(
            sample_project_name,
            stanford_status="READY_FOR_UPLOAD",
            stanford_upload_failures=0,
            no_pdf_review_alert_last_sent_at=datetime.now().isoformat(),
        )

        starting_failures = Config.STANFORD_MAX_UPLOAD_RETRIES - 1
        db_in_memory.update_project_state(
            sample_project_name, stanford_upload_failures=starting_failures
        )

        with patch.object(enhancement_agent, "_get_project_pdf_path", return_value=None), \
             patch.object(enhancement_agent, "_run_internal_review", return_value=True):
            enhancement_agent._process_project(sample_project_name)

        state = db_in_memory.get_project_state(sample_project_name)
        assert state["no_pdf_review_alert_last_sent_at"] is None


class TestGetLatestStanfordReviewScopedPerProject:
    def test_get_latest_stanford_review_scoped_per_project(self, db_in_memory):
        """Review history for one project must never leak into another project's
        comparison."""
        db_in_memory.save_stanford_review("ProjectA", "A's review")
        db_in_memory.save_stanford_review("ProjectB", "B's review")

        assert db_in_memory.get_latest_stanford_review("ProjectA") == "A's review"
        assert db_in_memory.get_latest_stanford_review("ProjectB") == "B's review"
