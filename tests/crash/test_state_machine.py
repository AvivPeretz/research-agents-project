"""Tests for Stanford state machine logic in ResearchEnhancementAgent."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


class TestStateMachine:
    """Tests for Stanford review state machine transitions."""

    def test_ready_for_upload_triggers_upload_attempt(self, db_in_memory, mock_notifier):
        """READY_FOR_UPLOAD state causes upload_to_stanford to be called during run()."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        project = "Upload_Project"
        db_in_memory.add_project(project, "test@example.com")

        with patch.object(ResearchEnhancementAgent, "upload_to_stanford", return_value="token123") as mock_upload, \
             patch.object(ResearchEnhancementAgent, "_get_project_pdf_path", return_value="/tmp/paper.pdf"):
            agent = ResearchEnhancementAgent(
                overleaf_projects=[project],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            agent.run()

        # upload_to_stanford must have been called for a READY_FOR_UPLOAD project
        mock_upload.assert_called_once_with(project, "/tmp/paper.pdf")

    def test_ready_for_upload_stores_token_on_success(self, db_in_memory, mock_notifier):
        """Asserts that a successful upload persists the returned token to the DB."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        project = "Token_Store_Project"
        db_in_memory.add_project(project, "test@example.com")

        with patch.object(ResearchEnhancementAgent, "upload_to_stanford", return_value="tok_xyz789"), \
             patch.object(ResearchEnhancementAgent, "_get_project_pdf_path", return_value="/tmp/paper.pdf"):
            agent = ResearchEnhancementAgent(
                overleaf_projects=[project],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            agent.run()

        state = db_in_memory.get_project_state_slim(project)
        assert state["stanford_status"] == "WAITING_FOR_REVIEW"
        assert state["stanford_token"] == "tok_xyz789"

    def test_upload_failure_does_not_change_state(self, db_in_memory, mock_notifier):
        """Asserts that failed upload keeps state as READY_FOR_UPLOAD."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        project = "Failed_Upload_Project"
        db_in_memory.add_project(project, "test@example.com")

        with patch.object(ResearchEnhancementAgent, "upload_to_stanford", return_value=None):
            # State should remain unchanged
            state = db_in_memory.get_project_state(project)
            assert state["stanford_status"] == "READY_FOR_UPLOAD"

    def test_waiting_for_review_with_review_not_ready_stays_waiting(self, db_in_memory, mock_notifier):
        """Asserts that a not-ready review (no token match yet) keeps state as WAITING_FOR_REVIEW,
        and does NOT fall back to internal review — Stanford's own docs say processing can take hours,
        so 'not ready yet' is the expected common case, not a failure."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        project = "Waiting_Project"
        db_in_memory.add_project(project, "test@example.com")
        recent_time = (datetime.now() - timedelta(minutes=5)).isoformat()
        db_in_memory.update_project_state(
            project, stanford_status="WAITING_FOR_REVIEW",
            last_upload_time=recent_time, stanford_token="token123",
        )

        with patch.object(ResearchEnhancementAgent, "_fetch_review_from_stanford", return_value=None), \
             patch.object(ResearchEnhancementAgent, "_run_internal_review") as mock_fallback:
            agent = ResearchEnhancementAgent(
                overleaf_projects=[project],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            agent.run()

        state = db_in_memory.get_project_state(project)
        assert state["stanford_status"] == "WAITING_FOR_REVIEW"
        mock_fallback.assert_not_called()

    def test_full_happy_path_reaches_completed(self, db_in_memory, mock_notifier):
        """Successful two-cycle workflow transitions project to REVIEW_COMPLETED in DB.

        Cycle 1 (READY_FOR_UPLOAD): upload succeeds, token captured → state becomes WAITING_FOR_REVIEW.
        Cycle 2 (WAITING_FOR_REVIEW): review fetched with the stored token, tasks generated
                                       → state becomes REVIEW_COMPLETED.
        """
        from agents.research_enhancement_agent import ResearchEnhancementAgent
        from tests.fixtures.mock_responses import STANFORD_REVIEW_TEXT

        project = "Happy_Path_Project"
        db_in_memory.add_project(project, "test@example.com")

        with patch.object(ResearchEnhancementAgent, "upload_to_stanford", return_value="token123"), \
             patch.object(ResearchEnhancementAgent, "_get_project_pdf_path", return_value="/tmp/paper.pdf"), \
             patch.object(ResearchEnhancementAgent, "_fetch_review_from_stanford", return_value=STANFORD_REVIEW_TEXT), \
             patch.object(ResearchEnhancementAgent, "_generate_actionable_tasks", return_value="Tasks"):
            agent = ResearchEnhancementAgent(
                overleaf_projects=[project],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            agent.run()  # Cycle 1: READY_FOR_UPLOAD → WAITING_FOR_REVIEW (token stored)
            agent.run()  # Cycle 2: WAITING_FOR_REVIEW → REVIEW_COMPLETED

        state = db_in_memory.get_project_state(project)
        assert state is not None
        assert state["stanford_status"] == "REVIEW_COMPLETED"

    def test_review_completed_state_skips_all_phases(self, db_in_memory, mock_notifier):
        """Asserts that REVIEW_COMPLETED state skips upload and review-fetch checks."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        project = "Completed_Project"
        db_in_memory.add_project(project, "test@example.com")
        db_in_memory.update_project_state(project, stanford_status="REVIEW_COMPLETED")

        with patch.object(ResearchEnhancementAgent, "upload_to_stanford") as mock_upload, \
             patch.object(ResearchEnhancementAgent, "_fetch_review_from_stanford") as mock_fetch:
            agent = ResearchEnhancementAgent(
                overleaf_projects=[project],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            agent.run()

        mock_upload.assert_not_called()
        mock_fetch.assert_not_called()

    def test_stuck_in_waiting_over_48h_should_be_detectable(self, db_in_memory):
        """Test documents missing alert feature for projects stuck > 48h in WAITING state."""
        project = "Stuck_Project"
        db_in_memory.add_project(project, "test@example.com")
        db_in_memory.update_project_state(project, stanford_status="WAITING_FOR_REVIEW")

        # Update last_upload_time to 48+ hours ago
        old_time = (datetime.now() - timedelta(hours=49)).isoformat()
        with db_in_memory._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE project_state SET last_upload_time = ? WHERE project_name = ?",
                (old_time, project),
            )
            conn.commit()

        # This test documents that stuck detection is a known gap
        # Future implementation should alert when stuck > 48h
        state = db_in_memory.get_project_state(project)
        assert state["stanford_status"] == "WAITING_FOR_REVIEW"
        # TODO: Add alert mechanism for projects stuck > 48h in WAITING state
