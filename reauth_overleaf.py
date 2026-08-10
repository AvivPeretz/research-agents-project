"""
Run this ON A MACHINE WITH A DISPLAY whenever the "Overleaf Session Invalid" admin
alert fires. It opens a visible browser, pre-fills credentials, and waits for you to
solve any reCAPTCHA manually, then saves the session to Config.OVERLEAF_STATE_PATH
-- the same file the scheduled pipeline's pre-run health check reads.

(setup_overleaf_session.py does the exact same thing under a different, possibly
more familiar name -- kept as an alias rather than two separate implementations.)

Usage: python3 reauth_overleaf.py
"""

from ingestion.data_ingestion_agent import DataIngestionAgent


def main():
    agent = DataIngestionAgent(db=None, notifier=None)
    agent._perform_manual_login()
    import os
    if os.path.exists(agent.state_file):
        print(f"✅ Session saved to {agent.state_file}")
    else:
        print("❌ Login did not complete — session file was not created.")


if __name__ == "__main__":
    main()
