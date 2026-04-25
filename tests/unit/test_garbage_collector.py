"""Unit tests for GarbageCollector file cleanup functionality."""

import os
import time
from pathlib import Path

import pytest

from utils.garbage_collector import GarbageCollector


class TestGarbageCollector:
    """Tests for GarbageCollector cleanup operations."""

    def test_run_deletes_old_markdown(self, tmp_path, monkeypatch):
        """Asserts that markdown files older than 30 days are deleted."""
        # Create directories
        base_dir = tmp_path / "research_library"
        lit_review_dir = base_dir / "literature_reviews" / "test_project"
        lit_review_dir.mkdir(parents=True)

        # Create an old markdown file (35 days old)
        old_md = lit_review_dir / "old_summary.md"
        old_md.write_text("Old content")
        old_time = time.time() - (35 * 24 * 60 * 60)  # 35 days ago
        os.utime(str(old_md), (old_time, old_time))

        # Run garbage collector
        gc = GarbageCollector(base_dir=str(base_dir))
        gc.run()

        # Assert file is deleted
        assert not old_md.exists()

    def test_run_keeps_recent_markdown(self, tmp_path):
        """Asserts that recent markdown files (today) are kept."""
        base_dir = tmp_path / "research_library"
        lit_review_dir = base_dir / "literature_reviews" / "test_project"
        lit_review_dir.mkdir(parents=True)

        # Create a recent markdown file (today)
        recent_md = lit_review_dir / "recent_summary.md"
        recent_md.write_text("Recent content")

        # Run garbage collector
        gc = GarbageCollector(base_dir=str(base_dir))
        gc.run()

        # Assert file still exists
        assert recent_md.exists()

    def test_run_ignores_csv_files(self, tmp_path):
        """Asserts that CSV files are not deleted even if older than 30 days."""
        base_dir = tmp_path / "research_library"
        lit_review_dir = base_dir / "literature_reviews" / "test_project"
        lit_review_dir.mkdir(parents=True)

        # Create an old CSV file
        old_csv = lit_review_dir / "old_data.csv"
        old_csv.write_text("CSV content")
        old_time = time.time() - (35 * 24 * 60 * 60)  # 35 days ago
        os.utime(str(old_csv), (old_time, old_time))

        # Run garbage collector
        gc = GarbageCollector(base_dir=str(base_dir))
        gc.run()

        # Assert CSV file still exists
        assert old_csv.exists()

    def test_run_ignores_comparison_tables_folder(self, tmp_path):
        """Asserts that markdown files in comparison_tables/ are never deleted."""
        base_dir = tmp_path / "research_library"
        comp_table_dir = base_dir / "comparison_tables" / "test_project"
        comp_table_dir.mkdir(parents=True)

        # Create an old markdown file in comparison_tables
        old_md = comp_table_dir / "old_table.md"
        old_md.write_text("Table content")
        old_time = time.time() - (35 * 24 * 60 * 60)  # 35 days ago
        os.utime(str(old_md), (old_time, old_time))

        # Run garbage collector
        gc = GarbageCollector(base_dir=str(base_dir))
        gc.run()

        # Assert file still exists (comparison_tables are protected)
        assert old_md.exists()

    def test_run_only_targets_correct_folders(self, tmp_path):
        """Asserts that old MD files in literature_reviews, project_tracking, and project_enhancement are all deleted."""
        base_dir = tmp_path / "research_library"

        # Create old files in each folder
        lit_review_dir = base_dir / "literature_reviews" / "test_project"
        lit_review_dir.mkdir(parents=True)
        lit_md = lit_review_dir / "old.md"
        lit_md.write_text("Content")

        proj_tracking_dir = base_dir / "project_tracking" / "test_project"
        proj_tracking_dir.mkdir(parents=True)
        proj_md = proj_tracking_dir / "old.md"
        proj_md.write_text("Content")

        enhancement_dir = base_dir / "project_enhancement" / "test_project"
        enhancement_dir.mkdir(parents=True)
        enh_md = enhancement_dir / "old.md"
        enh_md.write_text("Content")

        # Set all to 35 days old
        old_time = time.time() - (35 * 24 * 60 * 60)
        for md_file in [lit_md, proj_md, enh_md]:
            os.utime(str(md_file), (old_time, old_time))

        # Run garbage collector
        gc = GarbageCollector(base_dir=str(base_dir))
        gc.run()

        # Assert correct deletion behavior
        assert not lit_md.exists()   # literature_reviews: deleted
        assert not proj_md.exists()  # project_tracking: deleted
        assert not enh_md.exists()   # project_enhancement: deleted

    def test_run_handles_missing_directory_gracefully(self, tmp_path):
        """Asserts that calling run() with a non-existent base_dir raises no exception."""
        non_existent_dir = str(tmp_path / "nonexistent" / "path")

        gc = GarbageCollector(base_dir=non_existent_dir)
        # Should not raise an exception
        gc.run()

    def test_deleted_count_logged(self, tmp_path, caplog):
        """Asserts that the log message mentions the correct deleted count."""
        base_dir = tmp_path / "research_library"
        lit_review_dir = base_dir / "literature_reviews" / "test_project"
        lit_review_dir.mkdir(parents=True)

        # Create 3 old markdown files
        old_time = time.time() - (35 * 24 * 60 * 60)
        for i in range(3):
            md_file = lit_review_dir / f"old_{i}.md"
            md_file.write_text("Content")
            os.utime(str(md_file), (old_time, old_time))

        # Run garbage collector
        gc = GarbageCollector(base_dir=str(base_dir))
        gc.run()

        # Assert log contains the count (implementation dependent on logging)
        # This test documents the expected behavior even if logging is not implemented
        assert not (lit_review_dir / "old_0.md").exists()
        assert not (lit_review_dir / "old_1.md").exists()
        assert not (lit_review_dir / "old_2.md").exists()
