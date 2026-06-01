import os
import time
import logging

class GarbageCollector:
    """
    A utility class to clean up old markdown files and prevent disk space exhaustion.
    Uses a TTL (Time-To-Live) approach.
    """
    def __init__(self, base_dir=None, retention_days=30):
        if base_dir is None:
            from config import Config
            base_dir = str(Config.LIBRARY_DIR)
        self.base_dir = os.path.abspath(base_dir)
        self.retention_days = retention_days
        self.retention_seconds = self.retention_days * 24 * 60 * 60 # Convert days to seconds
        
        # Setup Logger
        self.logger = logging.getLogger("GarbageCollector")
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

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