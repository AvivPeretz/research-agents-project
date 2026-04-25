import os
import shutil
import zipfile
import time
import random
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config import Config

class DataIngestionAgent:
    """
    Data Ingestion Agent: Performs a Delta Sync.
    Downloads BOTH the source ZIP (for text delta extraction) AND the compiled PDF.
    Uses stealth techniques to minimize CAPTCHA triggers during login.
    Utilizes the centralized SQLite database for sync state management.
    """
    def __init__(self, db=None, notifier=None):
        self.email = Config.OVERLEAF_EMAIL
        self.password = Config.OVERLEAF_PASSWORD
        self.db = db
        self.notifier = notifier
        self.state_file = Config.OVERLEAF_STATE_PATH
        self.download_dir = Config.OVERLEAF_DIR

        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

    def _human_delay(self, min_ms: int = 800, max_ms: int = 2200):
        """Pauses for a randomized duration to mimic human interaction timing."""
        time.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

    def _human_type(self, page, selector: str, text: str):
        """
        Types text into a field character by character with randomized delays,
        mimicking natural human keystroke cadence.
        """
        page.click(selector)
        self._human_delay(300, 700)
        for char in text:
            page.type(selector, char, delay=random.uniform(60, 180))

    def _build_stealth_context(self, playwright, headless: bool = None, accept_downloads: bool = False):
        """
        Creates a hardened browser context with stealth patches applied.
        Uses Config.PLAYWRIGHT_HEADLESS unless explicitly overridden.
        Returns (browser, context, page) as a tuple.
        """
        # Use Config value if not explicitly overridden by caller
        if headless is None:
            headless = Config.PLAYWRIGHT_HEADLESS

        browser = playwright.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
            accept_downloads=accept_downloads,
        )
        page = context.new_page()
        return browser, context, page

    def _perform_manual_login(self):
        """
        Opens a visible browser (always headless=False — requires human interaction)
        and pre-fills credentials. Waits for the user to complete any CAPTCHA manually,
        then saves the resulting session state to disk.
        """
        print("\n🛑 No saved session found or session expired! Initiating manual login...")

        if self.notifier:
            self.notifier.send_admin_alert(
                subject="Overleaf Manual Login Required",
                message=(
                    "The Overleaf session has expired or is missing. "
                    "The system has opened a stealth browser window. "
                    "Please complete the login within 90 seconds."
                )
            )

        with sync_playwright() as p:
            # Manual login always requires a visible browser — override headless to False
            browser, context, page = self._build_stealth_context(p, headless=False)

            try:
                page.goto("https://www.overleaf.com/login")
                self._human_delay(1500, 3000)

                print("🔑 Pre-filling credentials with human-like typing...")
                if self.email and self.password:
                    self._human_type(page, "input[name='email']", self.email)
                    self._human_delay(400, 900)
                    self._human_type(page, "input[name='password']", self.password)
                    self._human_delay(500, 1200)

                print("\n🚨 ACTION REQUIRED: Click 'Log In'. Solve reCAPTCHA manually if it appears.")
                print("⏳ Waiting up to 90 seconds for you to reach the dashboard...")

                page.wait_for_url("**/project", timeout=90000)
                print("✅ Reached dashboard! Saving session securely...")
                self._human_delay(1500, 2500)
                context.storage_state(path=self.state_file)

            except PlaywrightTimeoutError:
                print("❌ Login timed out. You did not reach the dashboard in time.")
            except Exception as e:
                print(f"❌ Login failed: {e}")
            finally:
                context.close()
                browser.close()

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

        if not os.path.exists(self.state_file):
            self._perform_manual_login()

        if not os.path.exists(self.state_file):
            print("❌ Still no session file. Aborting sync.")
            return []

        updated_projects = []

        with sync_playwright() as p:
            # Use Config.PLAYWRIGHT_HEADLESS (via _build_stealth_context default)
            browser, _, _ = self._build_stealth_context(p, accept_downloads=True)
            context = browser.new_context(
                storage_state=self.state_file,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                timezone_id="America/New_York",
                accept_downloads=True,
            )
            page = context.new_page()

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
                    browser.close()

                    if os.path.exists(self.state_file):
                        os.remove(self.state_file)

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

                    print(f"🔄 Retrying sync cycle (attempt {_retry_depth + 1}/{MAX_RETRY_DEPTH})...")
                    return self.sync_all_projects(_retry_depth=_retry_depth + 1)

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

            except Exception as e:
                print(f"❌ Sync failed: {e}")
            finally:
                context.close()
                browser.close()

        print(f"🎉 Ingestion complete! {len(updated_projects)} projects were downloaded.")
        return updated_projects