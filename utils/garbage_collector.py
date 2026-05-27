import os
import time
import logging
from datetime import datetime, timedelta

class GarbageCollector:
    """
    A utility class to clean up old markdown files and prevent disk space exhaustion.
    Uses a TTL (Time-To-Live) approach.
    """
    def __init__(self, base_dir=None, retention_days=30, db=None):
        if base_dir is None:
            from config import Config
            base_dir = str(Config.LIBRARY_DIR)
        self.base_dir = os.path.abspath(base_dir)
        self.retention_days = retention_days
        self.retention_seconds = self.retention_days * 24 * 60 * 60 # Convert days to seconds
        self.db = db

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

    def run(self, dry_run: bool = False):
        self.logger.info(f"Starting Garbage Collection. Retention policy: {self.retention_days} days.")

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

                        # Calculate file age based on its Last Modification Time
                        file_age_seconds = current_time - os.path.getmtime(filepath)

                        if file_age_seconds > self.retention_seconds:
                            if dry_run:
                                self.logger.info(f"[DRY RUN] Would delete: {file} (Age: {int(file_age_seconds/86400)} days)")
                                deleted_count += 1
                            else:
                                try:
                                    os.remove(filepath)
                                    self.logger.info(f"🗑️ Deleted old file: {file} (Age: {int(file_age_seconds/86400)} days)")
                                    deleted_count += 1
                                except Exception as e:
                                    self.logger.error(f"Failed to delete {file}: {e}")

        self.logger.info(f"Garbage collection cycle finished. Total files removed: {deleted_count}.")

        self._cleanup_progress_snapshots()