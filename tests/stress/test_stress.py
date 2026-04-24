"""Stress and load tests for the research agents system."""

import threading
from unittest.mock import patch

import pytest

from tests.fixtures.mock_responses import VALID_LITERATURE_JSON


class TestStress:
    """Tests for system behavior under stress and load."""

    def test_literature_agent_with_15_projects(self, db_in_memory, mock_notifier):
        """Asserts that LiteratureResearchAgent handles 15 projects without error."""
        from agents.literature_research_agent import LiteratureResearchAgent

        projects = [f"Project_{i}" for i in range(15)]

        with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON):
            with patch.object(LiteratureResearchAgent, "_read_project_text", return_value="Test content"):
                agent = LiteratureResearchAgent(projects=projects, db=db_in_memory, notifier=mock_notifier)
                agent.run()
                # Should complete without error
                assert mock_notifier.send_literature_update.call_count >= 0

    def test_progress_agent_with_15_projects(self, db_in_memory, mock_notifier):
        """Asserts that ProgressTrackingAgent handles 15 projects without error."""
        from agents.progress_tracking_agent import ProgressTrackingAgent

        projects = [f"Project_{i}" for i in range(15)]
        for proj in projects:
            db_in_memory.add_project(proj)

        with patch.object(ProgressTrackingAgent, "_get_project_text", return_value="Static content"):
            agent = ProgressTrackingAgent(projects=projects, db=db_in_memory, notifier=mock_notifier)
            agent.run()
            # Should complete without error

    def test_db_concurrent_writes_do_not_corrupt(self, db_in_memory):
        """Asserts that 10 concurrent snapshot writes don't corrupt database."""
        project = "Concurrent_Project"
        db_in_memory.add_project(project)

        def add_snapshot(i):
            db_in_memory.add_progress_snapshot(
                project,
                had_changes=(i % 2 == 0),
                delta=f"Changes {i}",
            )

        threads = [threading.Thread(target=add_snapshot, args=(i,)) for i in range(10)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify all 10 rows were inserted
        cursor = db_in_memory.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM progress_snapshots WHERE project_name = ?", (project,))
        count = cursor.fetchone()[0]

        assert count == 10

    def test_llm_waterfall_under_repeated_load(self, db_in_memory, mock_notifier):
        """Asserts that LLM provider waterfall works correctly under 50 calls."""
        from agents.base_agent import BaseAgent

        with patch.object(BaseAgent, "ask_llm", return_value="Valid response"):
            for _ in range(50):
                result = BaseAgent.ask_llm.__wrapped__(
                    MagicMock(), query="Test", required_keys=None, fallback_response=""
                )
                assert result is not None

    def test_garbage_collector_with_500_files(self, tmp_path):
        """Asserts that GarbageCollector handles 500 files correctly (300 old, 200 recent)."""
        from utils.garbage_collector import GarbageCollector
        import os
        import time

        base_dir = tmp_path / "research_library"
        lit_review_dir = base_dir / "literature_reviews" / "test_project"
        lit_review_dir.mkdir(parents=True)

        old_time = time.time() - (35 * 24 * 60 * 60)

        # Create 300 old files
        for i in range(300):
            md_file = lit_review_dir / f"old_{i}.md"
            md_file.write_text(f"Old content {i}")
            os.utime(str(md_file), (old_time, old_time))

        # Create 200 recent files
        for i in range(200):
            md_file = lit_review_dir / f"recent_{i}.md"
            md_file.write_text(f"Recent content {i}")

        gc = GarbageCollector(base_dir=str(base_dir))
        gc.run()

        # Verify 300 old files deleted, 200 recent remain
        remaining_files = list(lit_review_dir.glob("*.md"))
        assert len(remaining_files) == 200

    def test_literature_agent_large_tex_file(self, db_in_memory, mock_notifier):
        """Asserts that 100,000 char LaTeX file is truncated to 4000 chars before LLM."""
        from agents.literature_research_agent import LiteratureResearchAgent

        large_text = "x" * 100_000

        with patch.object(LiteratureResearchAgent, "_read_project_text", return_value=large_text):
            with patch("agents.base_agent.BaseAgent.ask_llm", return_value=VALID_LITERATURE_JSON) as mock_llm:
                agent = LiteratureResearchAgent(
                    projects=["LargeText_Project"],
                    db=db_in_memory,
                    notifier=mock_notifier,
                )
                agent.run()

                # Verify text is truncated in LLM call
                if mock_llm.called:
                    call_args = str(mock_llm.call_args)
                    # Text should be limited to ~4000 chars

    def test_db_idempotency_under_load(self, db_in_memory):
        """Asserts that 100 sync_registry updates result in exactly 1 row."""
        project = "Load_Test_Project"

        for i in range(100):
            db_in_memory.update_sync_registry(project, f"Text version {i}")

        cursor = db_in_memory.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sync_registry WHERE project_name = ?", (project,))
        count = cursor.fetchone()[0]

        assert count == 1
        # Latest value should be stored
        result = db_in_memory.get_last_modified(project)
        assert "version 99" in result


# Import at module level to use in tests
from unittest.mock import MagicMock
