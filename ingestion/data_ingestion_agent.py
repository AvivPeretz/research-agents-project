import os
import logging
import shutil
import zipfile
import time
import random
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config import Config

class DataIngestionAgent:
    """
    Data Ingestion Agent: Performs a Delta Sync.
    Downloads BOTH the source ZIP (for text delta extraction) AND the compiled PDF.
    Uses a persistent Chrome profile to minimise CAPTCHA triggers and extend session lifetime.
    Utilises the centralised SQLite database for sync state management.
    """
    def __init__(self, db=None, notifier=None):
        self.logger = logging.getLogger("DataIngestionAgent")
        self.email = Config.OVERLEAF_EMAIL
        self.password = Config.OVERLEAF_PASSWORD
        self.db = db
        self.notifier = notifier
        self.user_data_dir = Config.OVERLEAF_USER_DATA_DIR
        self.download_dir = Config.OVERLEAF_DIR

        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

    def _human_delay(self, min_ms: int = 800, max_ms: int = 2200):
        """Pauses for a randomised duration to mimic human interaction timing."""
        time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    def _human_type(self, page, selector: str, text: str):
        """
        Types text into a field character by character with randomised delays,
        mimicking natural human keystroke cadence.
        """
        page.click(selector)
        self._human_delay(300, 700)
        page.locator(selector).fill(text)
        self._human_delay(300, 700)

    def _perform_manual_login(self):
        """
        Opens a visible browser using a persistent Chrome profile directory.
        Pre-fills credentials with human-like typing.
        Saves the entire Chrome profile on successful login.
        If reCAPTCHA appears, waits for the user to solve it manually.
        """
        print("\n🛑 No saved session found or session expired! Initiating login...")

        if self.notifier:
            self.notifier.send_admin_alert(
                subject="Overleaf Login Required",
                message=(
                    "The Overleaf session has expired or is missing. "
                    "A browser window has been opened. "
                    "Please complete the login manually if required."
                )
            )

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                self.user_data_dir,
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                timezone_id="America/New_York",
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://www.overleaf.com/login")
                self._human_delay(1500, 3000)

                print("🔑 Pre-filling credentials with human-like typing...")
                if self.email and self.password:
                    self._human_type(page, "#email", self.email)
                    self._human_delay(400, 900)
                    self._human_type(page, "#password", self.password)
                    self._human_delay(500, 1200)

                print("🤖 Clicking login button...")
                page.click('button[type="submit"]')

                print("⏳ Waiting for dashboard (up to 120s — solve CAPTCHA manually if prompted)...")
                page.wait_for_url("**/project", timeout=120000)
                print("✅ Login successful! Chrome profile saved.")
                self._human_delay(1500, 2500)

            except PlaywrightTimeoutError:
                print("❌ Login timed out. Dashboard not reached.")
            except Exception as e:
                print(f"❌ Login failed: {e}")
            finally:
                context.close()

    def sync_all_projects(self, _retry_depth: int = 0) -> list:
        """
        Performs a full delta sync cycle.
        _retry_depth prevents infinite recursion on repeated session failures.
        """
        MAX_RETRY_DEPTH = 2

        print("🤖 Starting Delta Sync Ingestion Cycle...")

        if not self.db:
            print("❌ Database connection missing! Aborting sync.")
            return []

        if not os.path.exists(self.user_data_dir) or not os.listdir(self.user_data_dir):
            self._perform_manual_login()

        if not os.path.exists(self.user_data_dir) or not os.listdir(self.user_data_dir):
            print("❌ Still no session profile. Aborting sync.")
            return []

        updated_projects = []
        _need_retry = False

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                self.user_data_dir,
                headless=Config.PLAYWRIGHT_HEADLESS,
                accept_downloads=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/135.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = context.pages[0] if context.pages else context.new_page()

            try:
                print("🌐 Navigating to dashboard...")
                page.goto("https://www.overleaf.com/project")
                self._human_delay(2000, 4000)

                try:
                    page.wait_for_selector(
                        'a[href^="/project/"]',
                        state='attached',
                        timeout=Config.PLAYWRIGHT_TIMEOUT_MS
                    )
                except PlaywrightTimeoutError:
                    print("⚠️ Timeout waiting for projects. Session may be invalid.")
                    context.close()

                    # Clear the stale profile so next run triggers re-login
                    if os.path.exists(self.user_data_dir):
                        shutil.rmtree(self.user_data_dir)

                    # Depth limit — prevent infinite recursion
                    if _retry_depth >= MAX_RETRY_DEPTH:
                        print(f"❌ Max retry depth ({MAX_RETRY_DEPTH}) reached. Aborting sync.")
                        if self.notifier:
                            self.notifier.send_admin_alert(
                                subject="Overleaf Sync Failed — Max Retries Reached",
                                message=(
                                    f"sync_all_projects() failed after {MAX_RETRY_DEPTH} retries. "
                                    "Manual login may be required."
                                )
                            )
                        return []

                    _need_retry = True

                rows = page.locator(
                    'tr:has(a[href^="/project/"]), li:has(a[href^="/project/"])'
                ).all()
                print(f"📊 Found {len(rows)} potential project rows on the dashboard.")

                for row in rows:
                    link = row.locator('a[href^="/project/"]').first
                    if link.count() == 0:
                        continue

                    project_name = link.inner_text().strip()
                    if not project_name:
                        continue

                    if self.db:
                        self.db.log_agent_run(
                            agent_name="DataIngestionAgent",
                            project_name=project_name,
                            status="STARTED",
                            started_at=datetime.now().isoformat()
                        )

                    last_modified_text = row.inner_text().strip()
                    db_last_modified = self.db.get_last_modified(project_name)

                    is_new = db_last_modified is None
                    is_modified = not is_new and db_last_modified != last_modified_text

                    if is_new or is_modified:
                        reason = "NEW" if is_new else "MODIFIED"
                        safe_project_name = project_name.replace(" ", "_")
                        href = link.get_attribute("href").rstrip('/')

                        print(f"🔄 [{reason}] '{project_name}' requires sync.")
                        self._human_delay(800, 1800)

                        # --- 1. DOWNLOAD ZIP SOURCE ---
                        zip_download_url = f"https://www.overleaf.com{href}/download/zip"
                        print("   📦 Downloading ZIP source...")
                        with page.expect_download() as zip_download_info:
                            page.evaluate(f"window.location.href = '{zip_download_url}'")

                        zip_download = zip_download_info.value
                        zip_path = os.path.join(self.download_dir, f"{safe_project_name}.zip")
                        zip_download.save_as(zip_path)

                        if not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                            self.logger.error("ZIP file missing or empty for '%s'. Skipping sync.", project_name)
                            continue

                        extract_path = os.path.join(self.download_dir, project_name)
                        if os.path.exists(extract_path):
                            shutil.rmtree(extract_path)

                        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                            zip_ref.extractall(extract_path)

                        os.remove(zip_path)

                        # --- 2. DOWNLOAD COMPILED PDF ---
                        print("   📄 Opening editor to download PDF via the 'File' menu...")
                        try:
                            editor_url = f"https://www.overleaf.com{href}"
                            page.goto(editor_url)
                            self._human_delay(7000, 10000)

                            print("   📂 Clicking the 'File' menu...")
                            file_btn = page.locator('text="File"').first
                            file_btn.click()
                            self._human_delay(1000, 2000)

                            print("   📥 Clicking 'Download as PDF'...")
                            pdf_btn = page.locator('text="Download as PDF"').first

                            with page.expect_download(timeout=30000) as pdf_download_info:
                                pdf_btn.click()

                            pdf_download = pdf_download_info.value
                            pdf_path = os.path.join(extract_path, f"{safe_project_name}.pdf")
                            pdf_download.save_as(pdf_path)
                            if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) == 0:
                                self.logger.warning("PDF file missing or empty for '%s'.", project_name)
                            else:
                                print("   ✅ PDF downloaded successfully.")

                        except Exception as e:
                            print(f"   ⚠️ Exception during PDF download: {e}")
                        finally:
                            page.goto("https://www.overleaf.com/project")
                            self._human_delay(2000, 3500)
                            page.wait_for_selector(
                                'a[href^="/project/"]',
                                state='attached',
                                timeout=Config.PLAYWRIGHT_TIMEOUT_MS
                            )

                        self.db.update_sync_registry(project_name, last_modified_text)
                        self.db.add_project(project_name, Config.OVERLEAF_EMAIL)

                        updated_projects.append(project_name)
                        print(f"✅ Synced '{project_name}'.")

                    else:
                        print(f"⏭️  [SKIPPED] '{project_name}' is up to date.")

                    if self.db:
                        self.db.log_agent_run(
                            agent_name="DataIngestionAgent",
                            project_name=project_name,
                            status="SUCCESS",
                            finished_at=datetime.now().isoformat()
                        )

            except Exception as e:
                print(f"❌ Sync failed: {e}")
            finally:
                context.close()

        if _need_retry:
            print(f"🔄 Retrying sync cycle (attempt {_retry_depth + 1}/{MAX_RETRY_DEPTH})...")
            return self.sync_all_projects(_retry_depth=_retry_depth + 1)

        print(f"🎉 Ingestion complete! {len(updated_projects)} projects were downloaded.")
        return updated_projects
