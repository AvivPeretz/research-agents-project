"""Unit tests for Pydantic schema validation."""

import json

import pytest
from pydantic import ValidationError

from domain.schemas import LiteratureReport, PaperData, ProjectEvaluation, SupervisorReport


class TestPaperDataSchema:
    """Tests for PaperData Pydantic model validation."""

    def test_paper_data_valid_full(self):
        """Asserts that PaperData with all fields provided validates correctly."""
        data = {
            "paper_name": "Test Paper",
            "data_type": "images, text",
            "reproducible": "Yes",
            "complexity": "High",
            "privacy_issues": "Yes",
        }
        paper = PaperData(**data)
        assert paper.paper_name == "Test Paper"

    def test_paper_data_defaults_to_na(self):
        """Asserts that optional fields default to 'N/A' when not provided."""
        data = {"paper_name": "Only Name"}
        paper = PaperData(**data)
        assert paper.paper_name == "Only Name"
        assert paper.data_type == "N/A"
        assert paper.reproducible == "N/A"
        assert paper.complexity == "N/A"
        assert paper.privacy_issues == "N/A"

    def test_paper_data_invalid_reproducible(self):
        """Asserts that invalid reproducible value 'Maybe' raises ValidationError."""
        data = {
            "paper_name": "Test",
            "reproducible": "Maybe",
        }
        with pytest.raises(ValidationError):
            PaperData(**data)

    def test_paper_data_invalid_complexity(self):
        """Asserts that invalid complexity value 'Unknown' raises ValidationError."""
        data = {
            "paper_name": "Test",
            "complexity": "Unknown",
        }
        with pytest.raises(ValidationError):
            PaperData(**data)

    def test_paper_data_invalid_privacy(self):
        """Asserts that invalid privacy_issues value 'Partial' raises ValidationError."""
        data = {
            "paper_name": "Test",
            "privacy_issues": "Partial",
        }
        with pytest.raises(ValidationError):
            PaperData(**data)

    def test_paper_data_alias_population(self):
        """Asserts that populate using alias 'types of available data' works correctly."""
        data = {
            "paper_name": "Test",
            "types of available data": "sensor data, logs",
        }
        paper = PaperData(**data)
        assert paper.data_type == "sensor data, logs"


class TestLiteratureReportSchema:
    """Tests for LiteratureReport Pydantic model validation."""

    def test_literature_report_valid(self):
        """Asserts that LiteratureReport with valid summary and one paper passes validation."""
        data = {
            "summary": "This is a comprehensive literature review with more than fifty characters of text.",
            "papers": [
                {
                    "paper_name": "Sample Paper",
                }
            ],
        }
        report = LiteratureReport(**data)
        assert len(report.papers) == 1

    def test_literature_report_summary_too_short(self):
        """Asserts that summary with only 10 characters raises ValidationError."""
        data = {
            "summary": "Too short",
            "papers": [{"paper_name": "Test"}],
        }
        with pytest.raises(ValidationError):
            LiteratureReport(**data)

    def test_literature_report_empty_papers_list(self):
        """Asserts that empty papers list raises ValidationError."""
        data = {
            "summary": "This is a valid summary with more than fifty characters for sure.",
            "papers": [],
        }
        with pytest.raises(ValidationError):
            LiteratureReport(**data)


class TestProjectEvaluationSchema:
    """Tests for ProjectEvaluation Pydantic model validation."""

    def test_project_evaluation_valid_statuses(self):
        """Asserts that all three of ON_TRACK, NEEDS_ATTENTION, STALLED statuses are accepted."""
        statuses = ["ON_TRACK", "NEEDS_ATTENTION", "STALLED"]
        for status in statuses:
            data = {
                "project_name": "Test Project",
                "status": status,
                "rationale": "Test rationale.",
                "action_item": "No action needed.",
            }
            eval_obj = ProjectEvaluation(**data)
            assert eval_obj.status == status

    def test_project_evaluation_invalid_status(self):
        """Asserts that invalid status 'DELAYED' raises ValidationError."""
        data = {
            "project_name": "Test",
            "status": "DELAYED",
            "rationale": "Test rationale.",
            "action_item": "No action needed.",
        }
        with pytest.raises(ValidationError):
            ProjectEvaluation(**data)


class TestSupervisorReportSchema:
    """Tests for SupervisorReport Pydantic model validation."""

    def test_supervisor_report_valid(self):
        """Asserts that SupervisorReport with valid executive_summary and one evaluation passes."""
        data = {
            "executive_summary": "This is a comprehensive executive summary with substantial content.",
            "evaluations": [
                {
                    "project_name": "Test Project",
                    "status": "ON_TRACK",
                    "rationale": "Good progress has been made.",
                    "action_item": "No action needed.",
                }
            ],
        }
        report = SupervisorReport(**data)
        assert len(report.evaluations) == 1


class TestPydanticJsonParsing:
    """Tests for Pydantic JSON parsing functionality."""

    def test_model_validate_json_literature(self):
        """Asserts that parsing a full valid JSON string works with model_validate_json."""
        json_str = json.dumps(
            {
                "summary": "This is a comprehensive literature review covering recent advances in deep learning and neural networks with extensive analysis.",
                "papers": [
                    {
                        "paper_name": "Deep Learning Advances",
                    }
                ],
            }
        )
        report = LiteratureReport.model_validate_json(json_str)
        assert report.papers[0].paper_name == "Deep Learning Advances"

    def test_model_validate_json_broken(self):
        """Asserts that broken JSON string raises ValidationError or ValueError."""
        broken_json = '{this is not valid json}'
        with pytest.raises((ValidationError, ValueError, json.JSONDecodeError)):
            LiteratureReport.model_validate_json(broken_json)
