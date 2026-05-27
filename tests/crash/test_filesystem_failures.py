import pytest
from unittest.mock import patch, MagicMock, mock_open
from utils.overleaf_connector import OverleafConnector
from agents.research_enhancement_agent import ResearchEnhancementAgent
from config import Config


class TestFilesystemFailures:

    def test_read_tex_file_missing_returns_empty_string(self):
        connector = OverleafConnector()
        result = connector.read_and_clean_tex_file("/nonexistent/path", "main.tex")
        assert result == ""

    def test_read_tex_file_empty_returns_empty_string(self, tmp_path):
        empty_tex = tmp_path / "main.tex"
        empty_tex.write_text("")
        connector = OverleafConnector()
        result = connector.read_and_clean_tex_file(str(tmp_path), "main.tex")
        assert result == ""

    def test_literature_agent_no_tex_files_skips_gracefully(self, tmp_path):
        from agents.literature_research_agent import LiteratureResearchAgent
        mock_notifier = MagicMock()
        with patch("utils.literature_fetcher.LiteratureFetcher.search", return_value=[]):
            with patch.object(OverleafConnector, "read_and_clean_tex_file", return_value=""):
                agent = LiteratureResearchAgent(active_projects=["EmptyProject"], notifier=mock_notifier)
                agent.run()  # Should not crash

    def test_enhancement_agent_no_pdf_skips_gracefully(self, tmp_path):
        mock_notifier = MagicMock()
        mock_db = MagicMock()
        mock_db.get_project_state.return_value = {"stanford_status": "READY_FOR_UPLOAD", "last_upload_time": None}
        agent = ResearchEnhancementAgent(
            overleaf_projects=["NoPdfProject"],
            notifier=mock_notifier,
            db=mock_db
        )
        with patch.object(Config, "OVERLEAF_DIR", str(tmp_path)):
            agent.run()  # No PDF found — should skip gracefully without crashing

    def test_enhancement_agent_pdf_path_none_returns_false(self):
        mock_notifier = MagicMock()
        mock_db = MagicMock()
        agent = ResearchEnhancementAgent(
            overleaf_projects=["Test"],
            notifier=mock_notifier,
            db=mock_db
        )
        result = agent.upload_to_stanford("Test", None)
        assert result is False

    def test_get_all_active_projects_empty_dir_returns_empty_list(self, tmp_path):
        with patch.object(Config, "OVERLEAF_DIR", tmp_path):
            from main import get_all_active_projects
            result = get_all_active_projects()
            assert result == []

    def test_get_all_active_projects_missing_dir_returns_empty_list(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with patch.object(Config, "OVERLEAF_DIR", missing):
            from main import get_all_active_projects
            result = get_all_active_projects()
            assert result == []

    def test_library_manager_creates_dirs_if_missing(self, tmp_path):
        from utils.library_manager import LibraryManager
        with patch.object(Config, "LIBRARY_DIR", tmp_path / "library"):
            lm = LibraryManager(base_dir=str(tmp_path / "library"))
            assert (tmp_path / "library" / "literature_reviews").exists()

    def test_library_manager_save_markdown_creates_file(self, tmp_path):
        from utils.library_manager import LibraryManager
        lm = LibraryManager(base_dir=str(tmp_path))
        path = lm.save_literature_summary("TestProject", "# Hello")
        assert path is not None

    # TEST D — FIX 8: _save_markdown must absorb OSError (e.g. disk full) and return None/False
    def test_library_manager_save_markdown_absorbs_oserror(self, tmp_path):
        """OSError during file write must not propagate; method returns None instead of raising."""
        from utils.library_manager import LibraryManager
        lm = LibraryManager(base_dir=str(tmp_path))
        with patch("builtins.open", side_effect=OSError("No space left on device")):
            result = lm._save_markdown("literature_reviews", "TestProject", "summary", "content")
        assert result is None