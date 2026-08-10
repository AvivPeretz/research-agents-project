"""
Run this ON A MACHINE WITH A DISPLAY to create or refresh the Overleaf session that
the scheduled pipeline actually uses.

This used to save a separate Chrome profile (Config.OVERLEAF_USER_DATA_DIR) that
nothing in the pipeline ever read back -- DataIngestionAgent authenticates from
Config.OVERLEAF_STATE_PATH instead. That meant running this script did not actually
fix an expired session. It now delegates to the same login flow the pipeline itself
uses, so it writes to the file that matters.

Usage: python3 setup_overleaf_session.py
(Equivalent to running reauth_overleaf.py -- kept under this name too since it's the
one operators are likely to already know.)
"""

from ingestion.data_ingestion_agent import DataIngestionAgent


def setup_session():
    agent = DataIngestionAgent(db=None, notifier=None)
    agent._perform_manual_login()


if __name__ == "__main__":
    setup_session()
