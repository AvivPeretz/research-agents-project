"""Unit tests for check_overleaf_session.py -- the standalone session-warming script.

This script must do exactly one thing: call DataIngestionAgent.check_session_health()
and alert on failure. It must never invoke sync_all_projects() or any other pipeline
logic, since it is meant to run on a much tighter cadence (e.g. daily) than the full
ingestion cycle and must stay cheap and side-effect-free beyond the health check
itself.
"""

from unittest.mock import MagicMock, patch

import check_overleaf_session


def test_healthy_session_returns_zero_and_sends_no_alert():
    mock_agent = MagicMock()
    mock_agent.check_session_health.return_value = True
    mock_notifier = MagicMock()

    with patch("check_overleaf_session.DataIngestionAgent", return_value=mock_agent) as mock_dia, \
         patch("check_overleaf_session.NotificationAgent", return_value=mock_notifier):
        exit_code = check_overleaf_session.main()

    mock_agent.check_session_health.assert_called_once()
    mock_agent.sync_all_projects.assert_not_called()
    mock_notifier.send_admin_alert.assert_not_called()
    assert exit_code == 0


def test_unhealthy_session_alerts_with_exact_recovery_command_and_returns_nonzero():
    mock_agent = MagicMock()
    mock_agent.check_session_health.return_value = False
    mock_notifier = MagicMock()

    with patch("check_overleaf_session.DataIngestionAgent", return_value=mock_agent), \
         patch("check_overleaf_session.NotificationAgent", return_value=mock_notifier):
        exit_code = check_overleaf_session.main()

    mock_agent.check_session_health.assert_called_once()
    mock_agent.sync_all_projects.assert_not_called()
    mock_notifier.send_admin_alert.assert_called_once()
    message = mock_notifier.send_admin_alert.call_args.kwargs.get("message", "")
    assert "python3 reauth_overleaf.py" in message
    assert exit_code == 1


def test_only_calls_check_session_health_no_other_agent_methods():
    """Guards against scope creep: this script must touch nothing on the agent
    besides check_session_health(), regardless of the health result."""
    mock_agent = MagicMock()
    mock_agent.check_session_health.return_value = True
    mock_notifier = MagicMock()

    with patch("check_overleaf_session.DataIngestionAgent", return_value=mock_agent), \
         patch("check_overleaf_session.NotificationAgent", return_value=mock_notifier):
        check_overleaf_session.main()

    called_methods = {call[0] for call in mock_agent.method_calls}
    assert called_methods == {"check_session_health"}
