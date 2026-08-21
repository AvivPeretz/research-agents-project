"""Integration test for main.py's first-sync eligibility bug.

Reproduces the scenario where a brand-new project is downloaded during the
ingestion phase but then gets excluded from every downstream agent phase
(literature/progress/enhancement) in that same run, because those phases
filter against `all_projects` computed *before* ingestion ran.

All agent classes main.py imports are mocked so no real Playwright/LLM/DB
calls happen. DatabaseManager is mocked so `db.get_project_count() == 0`
doesn't trigger a real JSON migration.
"""

from unittest.mock import MagicMock, patch

import pytest

from config import Config


class TestMainFirstSync:
    def test_first_ever_project_included_in_same_run(self, tmp_path, monkeypatch):
        """A project that did not exist on disk before this run (i.e. its very
        first sync) must still be passed to the Literature Research Agent in
        the same `--agent all` run that downloaded it."""
        overleaf_dir = tmp_path / "overleaf_projects"
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(overleaf_dir))
        # overleaf_projects/ starts out not existing at all -> get_all_active_projects() returns []

        monkeypatch.setattr(
            "sys.argv",
            ["main.py", "--project", "brand_new_project", "--agent", "all"],
        )

        mock_scraper = MagicMock()

        def fake_sync_all_projects():
            # Simulate the real download: the project directory now exists on disk.
            (overleaf_dir / "brand_new_project").mkdir(parents=True)
            return ["brand_new_project"]

        mock_scraper.sync_all_projects.side_effect = fake_sync_all_projects

        mock_db = MagicMock()
        mock_db.get_project_count.return_value = 1  # skip migration path

        with patch("main.Config.validate", return_value=None), \
             patch("main.DatabaseManager", return_value=mock_db), \
             patch("main.NotificationAgent") as mock_notifier_cls, \
             patch("main.DataIngestionAgent", return_value=mock_scraper), \
             patch("main.LiteratureResearchAgent") as mock_lit_agent_cls, \
             patch("main.ProgressTrackingAgent") as mock_prog_agent_cls, \
             patch("main.ResearchEnhancementAgent") as mock_enh_agent_cls, \
             patch("main.SupervisorStatusAgent") as mock_supervisor_cls, \
             patch("main.GarbageCollector") as mock_gc_cls:

            import main
            main.main()

        # Literature agent must have been constructed and run for the brand-new project.
        mock_lit_agent_cls.assert_called_once()
        _, kwargs = mock_lit_agent_cls.call_args
        assert kwargs["active_projects"] == ["brand_new_project"]
        mock_lit_agent_cls.return_value.run.assert_called_once()

    def test_gc_only_path_unaffected_by_union_fix(self, tmp_path, monkeypatch):
        """Regression guard: `--agent gc` must keep working (ingestion doesn't run,
        so `updated_projects` stays empty and the union is a no-op)."""
        overleaf_dir = tmp_path / "overleaf_projects"
        overleaf_dir.mkdir(parents=True)
        monkeypatch.setattr(Config, "OVERLEAF_DIR", str(overleaf_dir))
        monkeypatch.setattr("sys.argv", ["main.py", "--agent", "gc"])

        mock_db = MagicMock()
        mock_db.get_project_count.return_value = 1

        with patch("main.Config.validate", return_value=None), \
             patch("main.DatabaseManager", return_value=mock_db), \
             patch("main.NotificationAgent"), \
             patch("main.DataIngestionAgent") as mock_ingestion_cls, \
             patch("main.LiteratureResearchAgent") as mock_lit_agent_cls, \
             patch("main.ProgressTrackingAgent") as mock_prog_agent_cls, \
             patch("main.ResearchEnhancementAgent") as mock_enh_agent_cls, \
             patch("main.SupervisorStatusAgent") as mock_supervisor_cls, \
             patch("main.GarbageCollector") as mock_gc_cls:

            import main
            main.main()

        mock_ingestion_cls.assert_not_called()
        mock_lit_agent_cls.assert_not_called()
        mock_prog_agent_cls.assert_not_called()
        mock_enh_agent_cls.assert_not_called()
        mock_supervisor_cls.assert_not_called()
        mock_gc_cls.return_value.run.assert_called_once()
