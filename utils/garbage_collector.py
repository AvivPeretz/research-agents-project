import os
import time
import logging
from datetime import datetime, timedelta

class GarbageCollector:
    """
    A utility class to clean up old markdown files and prevent disk space exhaustion.
    Uses a TTL (Time-To-Live) approach.
    """
    def __init__(self, base_dir=None, retention_days=30, db=None, notifier=None):
        if base_dir is None:
            from config import Config
            base_dir = str(Config.LIBRARY_DIR)
        self.base_dir = os.path.abspath(base_dir)
        self.retention_days = retention_days
        self.retention_seconds = self.retention_days * 24 * 60 * 60 # Convert days to seconds
        self.db = db
        self.notifier = notifier

        # Setup Logger
        self.logger = logging.getLogger("GarbageCollector")
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def _cleanup_progress_snapshots(self):
        """Deletes progress_snapshot rows older than PROGRESS_SNAPSHOT_TTL_DAYS."""
        if not self.db:
            return
        from config import Config
        ttl_days = Config.PROGRESS_SNAPSHOT_TTL_DAYS
        threshold = datetime.utcnow() - timedelta(days=ttl_days)
        threshold_str = threshold.strftime('%Y-%m-%d %H:%M:%S')
        try:
            with self.db._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM progress_snapshots WHERE snapshot_date < ?",
                    (threshold_str,)
                )
                deleted = cursor.rowcount
                conn.commit()
            self.logger.info(
                "Progress snapshot TTL cleanup: deleted %d rows older than %d days.",
                deleted, ttl_days
            )
        except Exception as e:
            self.logger.error("Failed to clean up progress_snapshots: %s", str(e))

    def _check_stale_agent_runs(self):
        """Flags agent/project pairs whose last recorded run is still STARTED after
        an unreasonable amount of time. A normal run always follows STARTED with
        SUCCESS or FAILURE -- a STARTED row with neither means the process was
        killed outright (crash, OOM, machine sleep) rather than failing in a way
        the code's own try/except could catch and report. No exception-based alert
        anywhere else in the system can see this failure mode, so it needs its own
        check here.
        """
        if not self.db:
            return
        from config import Config
        try:
            stale_runs = self.db.get_stale_started_runs(older_than_hours=Config.STALE_RUN_THRESHOLD_HOURS)
        except Exception as e:
            self.logger.error("Failed to check for stale agent runs: %s", str(e))
            return

        if not stale_runs:
            return

        self.logger.error("Found %d stale STARTED run(s) with no completion: %s", len(stale_runs), stale_runs)
        if self.notifier:
            try:
                lines = "\n".join(
                    f"- {r['agent_name']} / {r['project_name']} (started {r['started_at']})"
                    for r in stale_runs
                )
                self.notifier.send_admin_alert(
                    subject=f"{len(stale_runs)} Stuck Run(s) Detected",
                    message=(
                        "The following agent/project runs were marked STARTED but never "
                        "completed (no SUCCESS or FAILURE ever followed). This usually means "
                        "the process was killed or crashed outright rather than failing in a "
                        "way the code could catch — check whether the machine restarted, ran "
                        "out of memory, or the process was terminated mid-run:\n\n" + lines
                    )
                )
            except Exception as e:
                self.logger.error("Failed to send stale-run alert: %s", str(e))

    def run(self, dry_run: bool = False):
        self.logger.info("Starting Garbage Collection. Retention policy: %d days.", self.retention_days)

        # SAFEGUARD 1: Only scan specific folders. We ignore 'comparison_tables' entirely.
        target_folders = ["literature_reviews", "project_tracking", "project_enhancement"]
        current_time = time.time()
        deleted_count = 0

        for folder in target_folders:
            folder_path = os.path.join(self.base_dir, folder)
            if not os.path.exists(folder_path):
                continue

            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    # SAFEGUARD 2: Strictly target only .md (Markdown) files.
                    if file.endswith(".md"):
                        filepath = os.path.join(root, file)

                        try:
                            stat = os.stat(filepath)
                        except OSError:
                            continue

                        file_age_seconds = current_time - stat.st_mtime

                        if file_age_seconds > self.retention_seconds:
                            age_days = int(file_age_seconds / 86400)
                            if dry_run:
                                self.logger.info("[DRY RUN] Would delete: %s (Age: %d days)", file, age_days)
                                deleted_count += 1
                            else:
                                try:
                                    os.remove(filepath)
                                    self.logger.info("Deleted old file: %s (Age: %d days)", file, age_days)
                                    deleted_count += 1
                                except OSError as e:
                                    self.logger.error("Failed to delete %s: %s", filepath, str(e))

        self.logger.info("Garbage collection cycle finished. Total files removed: %d.", deleted_count)

        self._cleanup_progress_snapshots()
        self._check_stale_agent_runs()
