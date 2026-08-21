"""Tests for main.py's run-lock mechanism (task 4e, stability-hardening).

Prevents two `main.py` invocations from racing (e.g. a scheduled run overlapping
a still-running one) by acquiring an exclusive, non-blocking `fcntl.flock` on a
dedicated lock file before any agent work begins.

Two layers are covered:

1. Unit-level: `acquire_run_lock()` correctly detects contention when called
   twice against the same lock file from the same test process (two separate
   file descriptors, non-blocking mode). This is the standard, realistic way to
   exercise `flock(2)` self-contention without spawning a second OS process --
   `flock`'s non-blocking failure is per-fd, so two independent opens of the
   same path in one process still race exactly like two separate processes
   would.

2. Integration-level: calling `main.main()` twice in immediate succession (with
   all real agent work mocked out) results in the second call detecting the
   lock held by the first and returning cleanly (no exception, agents from the
   first call's phase never re-invoked by the second) rather than erroring or
   blocking. The first call's lock is deliberately *not* released before the
   second call runs, by holding it open via a nested `with` -- this reproduces
   "still running" contention rather than the trivial back-to-back-after-release
   case.
"""

from unittest.mock import MagicMock, patch

import pytest

from config import Config


class TestAcquireRunLock:
    def test_second_acquisition_fails_fast_when_first_still_held(self, tmp_path):
        """Two independent, non-blocking acquisitions against the same lock file:
        the first succeeds, the second must detect contention and report failure
        immediately -- not block, not raise, not silently succeed."""
        from main import acquire_run_lock

        lock_path = tmp_path / "run.lock"

        with acquire_run_lock(lock_path) as first_acquired:
            assert first_acquired is True

            with acquire_run_lock(lock_path) as second_acquired:
                assert second_acquired is False

        # After the first lock is released, a fresh acquisition must succeed again.
        with acquire_run_lock(lock_path) as third_acquired:
            assert third_acquired is True

    def test_lock_file_is_created_at_configured_path(self, tmp_path):
        from main import acquire_run_lock

        lock_path = tmp_path / "nested" / "run.lock"
        lock_path.parent.mkdir(parents=True)

        with acquire_run_lock(lock_path) as acquired:
            assert acquired is True
            assert lock_path.exists()


class TestMainRunLockIntegration:
    def test_second_call_skips_cleanly_while_first_holds_lock(self, tmp_path, monkeypatch):
        """Calling main() a second time while a first call's lock is still held
        must skip cleanly (no exception) rather than erroring or racing."""
        import main

        lock_path = tmp_path / "run.lock"
        monkeypatch.setattr(main.Config, "RUN_LOCK_PATH", lock_path)
        monkeypatch.setattr("sys.argv", ["main.py", "--agent", "gc"])

        mock_db = MagicMock()
        mock_db.get_project_count.return_value = 1

        with patch("main.Config.validate", return_value=None), \
             patch("main.DatabaseManager", return_value=mock_db), \
             patch("main.NotificationAgent"), \
             patch("main.DataIngestionAgent") as mock_ingestion_cls, \
             patch("main.GarbageCollector") as mock_gc_cls, \
             patch("main.get_all_active_projects", return_value=[]):

            # Hold the lock open across the nested call, simulating a still-running
            # first invocation whose lock has not been released yet.
            with main.acquire_run_lock(lock_path) as held:
                assert held is True

                # Second call must detect the held lock and return cleanly.
                result = main.main()
                assert result is None

            mock_ingestion_cls.assert_not_called()
            mock_gc_cls.assert_not_called()

        # Once the lock is released, a subsequent call proceeds normally.
        with patch("main.Config.validate", return_value=None), \
             patch("main.DatabaseManager", return_value=mock_db), \
             patch("main.NotificationAgent"), \
             patch("main.DataIngestionAgent") as mock_ingestion_cls, \
             patch("main.GarbageCollector") as mock_gc_cls, \
             patch("main.get_all_active_projects", return_value=[]):

            main.main()
            mock_gc_cls.return_value.run.assert_called_once()
