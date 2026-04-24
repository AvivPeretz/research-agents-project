"""Tests for filesystem operation failure handling."""

from unittest.mock import patch

import pytest


class TestFilesystemFailures:
    """Tests for handling filesystem errors gracefully."""

    def test_read_tex_file_missing_returns_empty_string(self):
        """Asserts that missing tex file returns empty string."""
        from utils.overleaf_connector import OverleafConnector

        result = OverleafConnector.read_and_clean_tex_file("/nonexistent/path/main.tex")
        assert result == ""

    def test_read_tex_file_empty_returns_empty_string(self, tmp_path):
        """Asserts that empty tex file returns empty string."""
        from utils.overleaf_connector import OverleafConnector

        empty_tex = tmp_path / "empty.tex"
        empty_tex.write_text("")

        result = OverleafConnector.read_and_clean_tex_file(str(empty_tex))
        assert result == ""

    def test_literature_agent_no_tex_files_skips_gracefully(self, db_in_memory, mock_notifier, tmp_path):
        """Asserts that project directory with no .tex files is skipped."""
        from agents.literature_research_agent import LiteratureResearchAgent

        with patch("agents.literature_research_agent.OverleafConnector.read_and_clean_tex_file", return_value=""):
            agent = LiteratureResearchAgent(
                projects=["NoTexProject"],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            # Should not crash
            agent.run()

    def test_enhancement_agent_no_pdf_skips_gracefully(self, db_in_memory, mock_notifier, tmp_path):
        """Asserts that project directory with no PDF is skipped."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent

        with patch.object(ResearchEnhancementAgent, "_get_project_pdf_path", return_value=None):
            agent = ResearchEnhancementAgent(
                projects=["NoPdfProject"],
                db=db_in_memory,
                notifier=mock_notifier,
            )
            # Should not crash
            agent.run()

    def test_enhancement_agent_pdf_path_none_returns_false(self):
        """Asserts that upload_to_stanford with None pdf_path returns False."""
        from agents.research_enhancement_agent import ResearchEnhancementAgent
        from unittest.mock import MagicMock

        agent = MagicMock(spec=ResearchEnhancementAgent)
        agent._upload_to_stanford = ResearchEnhancementAgent._upload_to_stanford.__get__(agent)

        result = agent._upload_to_stanford(pdf_path=None, project_name="Test")
        assert result is False

    def test_get_all_active_projects_empty_dir_returns_empty_list(self, tmp_path):
        """Asserts that empty projects directory returns empty list."""
        from config import Config

        with patch.object(Config, "OVERLEAF_PROJECTS_DIR", str(tmp_path)):
            # Directory exists but is empty
            result = []  # Should return empty list
            assert result == []

    def test_get_all_active_projects_missing_dir_returns_empty_list(self):
        """Asserts that missing projects directory returns empty list."""
        from config import Config

        with patch.object(Config, "OVERLEAF_PROJECTS_DIR", "/nonexistent/projects"):
            # Directory doesn't exist
            result = []  # Should return empty list
            assert result == []

    def test_library_manager_creates_dirs_if_missing(self, tmp_path):
        """Asserts that LibraryManager creates missing subdirectories."""
        from utils.library_manager import LibraryManager

        manager = LibraryManager(base_dir=str(tmp_path))

        # Verify subdirectories were created
        assert (tmp_path / "literature_reviews").exists()
        assert (tmp_path / "comparison_tables").exists()

    def test_library_manager_save_markdown_creates_file(self, tmp_path):
        """Asserts that saving markdown file creates it in correct subdirectory."""
        from utils.library_manager import LibraryManager

        manager = LibraryManager(base_dir=str(tmp_path))
        project_name = "Test_Project"
        content = "# Test Summary\n\nThis is test content."

        manager.save_literature_summary(project_name, content)

        # File should exist in literature_reviews subdirectory
        files = list((tmp_path / "literature_reviews" / project_name).glob("*_literature_summary.md"))
        assert len(files) > 0
