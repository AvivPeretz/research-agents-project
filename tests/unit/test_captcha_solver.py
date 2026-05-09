"""Unit tests for utils/captcha_solver.py — CapSolver API integration."""

from unittest.mock import MagicMock, patch, call

import pytest

from config import Config
from utils.captcha_solver import solve_recaptcha_v2, inject_recaptcha_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_post_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.json.return_value = data
    return mock


# ---------------------------------------------------------------------------
# solve_recaptcha_v2 tests
# ---------------------------------------------------------------------------


class TestSolveRecaptchaV2:
    """Unit tests for solve_recaptcha_v2."""

    def test_solve_recaptcha_returns_none_when_no_api_key(self):
        """When CAPSOLVER_API_KEY is empty, None is returned without any HTTP call."""
        with patch.object(Config, "CAPSOLVER_API_KEY", ""):
            with patch("utils.captcha_solver.requests.post") as mock_post:
                result = solve_recaptcha_v2("https://example.com", "site_key_123")
                mock_post.assert_not_called()

        assert result is None

    def test_solve_recaptcha_returns_token_on_success(self):
        """A successful CapSolver round-trip returns the gRecaptchaResponse token."""
        create_response = _make_post_response({"errorId": 0, "taskId": "task123"})
        result_response = _make_post_response({
            "status": "ready",
            "solution": {"gRecaptchaResponse": "test_token_abc"},
        })

        with patch.object(Config, "CAPSOLVER_API_KEY", "test_capsolver_key"):
            with patch(
                "utils.captcha_solver.requests.post",
                side_effect=[create_response, result_response],
            ):
                with patch("utils.captcha_solver.time.sleep"):
                    result = solve_recaptcha_v2("https://example.com", "site_key_123")

        assert result == "test_token_abc"

    def test_solve_recaptcha_returns_none_on_capsolver_error(self):
        """When createTask returns errorId != 0, None is returned immediately."""
        create_response = _make_post_response({
            "errorId": 1,
            "errorDescription": "Invalid key",
        })

        with patch.object(Config, "CAPSOLVER_API_KEY", "test_capsolver_key"):
            with patch(
                "utils.captcha_solver.requests.post",
                return_value=create_response,
            ):
                with patch("utils.captcha_solver.time.sleep"):
                    result = solve_recaptcha_v2("https://example.com", "site_key_123")

        assert result is None

    def test_solve_recaptcha_returns_none_on_timeout(self):
        """When getTaskResult always returns 'processing', None is returned after max attempts."""
        create_response = _make_post_response({"errorId": 0, "taskId": "task_timeout"})
        # One createTask call + 24 getTaskResult calls all return "processing"
        poll_responses = [
            _make_post_response({"status": "processing"}) for _ in range(24)
        ]

        with patch.object(Config, "CAPSOLVER_API_KEY", "test_capsolver_key"):
            with patch(
                "utils.captcha_solver.requests.post",
                side_effect=[create_response] + poll_responses,
            ):
                with patch("utils.captcha_solver.time.sleep"):
                    result = solve_recaptcha_v2("https://example.com", "site_key_123")

        assert result is None

    def test_solve_recaptcha_returns_none_on_gettask_error(self):
        """When getTaskResult returns errorId != 0 during polling, None is returned."""
        create_response = _make_post_response({"errorId": 0, "taskId": "task_err"})
        error_response = _make_post_response({
            "errorId": 2,
            "errorDescription": "Task failed",
        })

        with patch.object(Config, "CAPSOLVER_API_KEY", "test_capsolver_key"):
            with patch(
                "utils.captcha_solver.requests.post",
                side_effect=[create_response, error_response],
            ):
                with patch("utils.captcha_solver.time.sleep"):
                    result = solve_recaptcha_v2("https://example.com", "site_key_123")

        assert result is None

    def test_solve_recaptcha_returns_none_on_request_exception(self):
        """An exception during the HTTP request is caught and None is returned."""
        with patch.object(Config, "CAPSOLVER_API_KEY", "test_capsolver_key"):
            with patch(
                "utils.captcha_solver.requests.post",
                side_effect=Exception("connection refused"),
            ):
                result = solve_recaptcha_v2("https://example.com", "site_key_123")

        assert result is None
