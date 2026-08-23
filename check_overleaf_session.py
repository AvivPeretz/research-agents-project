"""
Session "warming" — a lightweight, standalone check meant to run on its own cadence
(e.g. daily via cron), independent of the full ingestion pipeline (main.py, run 2-3x/week).

It does exactly one thing: calls DataIngestionAgent.check_session_health() -- a fast,
read-only, non-destructive pre-flight check (never opens a visible browser, never
blocks waiting for a human) -- and sends an admin alert if the saved Overleaf session
has gone stale. It does NOT touch sync_all_projects() or any other pipeline logic, so
it is safe to run far more often than the full sync without risking a partial/broken
run or wasted LLM/network calls.

The rationale (see the stability-hardening plan, task 5a) is that periodic light
authenticated activity may help extend session lifetime, and — regardless of whether
that folk wisdom holds up — checking daily catches an expired session sooner than
waiting for the next scheduled full sync, shrinking the window during which the
pipeline silently has nothing to ingest.

Usage: python3 check_overleaf_session.py
Suggested cron cadence: daily (add to your own cron/scheduler; this script does not
self-schedule).
"""

import sys

from ingestion.data_ingestion_agent import DataIngestionAgent
from agents.notification_agent import NotificationAgent


def main() -> int:
    notifier = NotificationAgent(db=None)
    agent = DataIngestionAgent(db=None, notifier=notifier)

    healthy = agent.check_session_health()

    if healthy:
        print("✅ Overleaf session is healthy.")
        return 0

    message = (
        "The scheduled Overleaf session health check (session warming) failed: the "
        "saved session is missing or no longer authenticates. Run "
        "`python3 reauth_overleaf.py` on a machine with a display to re-authenticate "
        "before the next scheduled ingestion run."
    )
    print(f"❌ {message}")
    notifier.send_admin_alert(
        subject="Overleaf Session Invalid — Manual Login Required",
        message=message,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
