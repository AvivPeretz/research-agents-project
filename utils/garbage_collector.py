import os
import time
import logging

class GarbageCollector:
    """
    A utility class to clean up old markdown files and prevent disk space exhaustion.
    Uses a TTL (Time-To-Live) approach.
    """
    def __init__(self, base_dir="research_library", retention_days=30):
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

    def run(self):
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
                            try:
                                os.remove(filepath)
                                self.logger.info(f"🗑️ Deleted old file: {file} (Age: {int(file_age_seconds/86400)} days)")
                                deleted_count += 1
                            except Exception as e:
                                self.logger.error(f"Failed to delete {file}: {e}")
                                
        self.logger.info(f"Garbage collection cycle finished. Total files removed: {deleted_count}.")