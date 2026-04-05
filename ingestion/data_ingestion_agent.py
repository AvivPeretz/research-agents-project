import os
import shutil
import zipfile
import time
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# Import centralized configuration
from config import Config

class DataIngestionAgent:
    """
    Data Ingestion Agent: Performs a Delta Sync.
    Downloads BOTH the source ZIP (for text delta extraction) AND the compiled PDF.
    Now utilizes the centralized SQLite database for sync state management.
    """
    def __init__(self, db=None, notifier=None):
        self.email = Config.OVERLEAF_EMAIL
        self.password = Config.OVERLEAF_PASSWORD
        
        # Dependency Injection
        self.db = db
        self.notifier = notifier
        
        self.state_file = Config.OVERLEAF_STATE_PATH
        self.download_dir = Config.OVERLEAF_DIR
        
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

    def _perform_manual_login(self):
        """Fallback method: Opens browser for user to log in and alerts admin."""
        print("\n🛑 No saved session found or session expired! Initiating manual login...")
        
        if self.notifier:
            self.notifier.send_admin_alert(
                subject="Overleaf Manual Login Required",
                message="The Overleaf session has expired or is missing. The system has opened a browser window and is pausing for 90 seconds. Please log in manually."
            )
            
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.goto("https://www.overleaf.com/login")
            
            print("🔑 Pre-filling credentials...")
            if self.email and self.password:
                page.fill("input[name='email']", self.email)
                page.fill("input[name='password']", self.password)
                
            print("\n🚨 ACTION REQUIRED: Please log in manually and solve the reCAPTCHA if it appears.")
            print("⏳ Waiting up to 90 seconds for you to reach the dashboard...")
            
            try:
                page.wait_for_url("**/project", timeout=90000)
                print("✅ Reached dashboard! Saving session securely...")
                context.storage_state(path=self.state_file)
                time.sleep(2)
            except PlaywrightTimeoutError:
                print("❌ Login timed out. You did not reach the dashboard in time.")
            except Exception as e:
                print(f"❌ Login failed: {e}")
            finally:
                context.close()
                browser.close()

    def sync_all_projects(self) -> list:
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
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                storage_state=self.state_file,
                accept_downloads=True
            )
            page = context.new_page()
            
            try:
                print("🌐 Navigating to dashboard...")
                page.goto("https://www.overleaf.com/project")
                
                try:
                    page.wait_for_selector('a[href^="/project/"]', state='attached', timeout=Config.PLAYWRIGHT_TIMEOUT_MS)
                except PlaywrightTimeoutError:
                    print("⚠️ Timeout waiting for projects. The session might be invalid.")
                    context.close()
                    browser.close()
                    
                    if os.path.exists(self.state_file):
                        os.remove(self.state_file)
                        
                    print("🔄 Retrying sync cycle to prompt manual login...")
                    return self.sync_all_projects()
                
                time.sleep(2)
                
                rows = page.locator('tr:has(a[href^="/project/"]), li:has(a[href^="/project/"])').all()
                print(f"📊 Found {len(rows)} potential project rows on the dashboard.")
                
                for row in rows:
                    link = row.locator('a[href^="/project/"]').first
                    if link.count() == 0:
                        continue
                        
                    project_name = link.inner_text().strip()
                    if not project_name:
                        continue
                        
                    last_modified_text = row.inner_text().strip() 
                    
                    # --- NEW: Check SQLite Database instead of JSON ---
                    db_last_modified = self.db.get_last_modified(project_name)
                    
                    is_new = db_last_modified is None
                    is_modified = not is_new and db_last_modified != last_modified_text
                    
                    if is_new or is_modified:
                        reason = "NEW" if is_new else "MODIFIED"
                        safe_project_name = project_name.replace(" ", "_")
                        
                        href = link.get_attribute("href").rstrip('/')
                        
                        print(f"🔄 [{reason}] '{project_name}' requires sync.")
                        
                        # --- 1. DOWNLOAD ZIP SOURCE ---
                        zip_download_url = f"https://www.overleaf.com{href}/download/zip"
                        print(f"   📦 Downloading ZIP source...")
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
                        print(f"   📄 Opening editor to download PDF via the 'File' menu...")
                        try:
                            editor_url = f"https://www.overleaf.com{href}"
                            page.goto(editor_url)
                            page.wait_for_timeout(8000)
                            
                            print("   📂 Clicking the 'File' menu...")
                            file_btn = page.locator('text="File"').first
                            file_btn.click()
                            page.wait_for_timeout(1500)
                            
                            print("   📥 Clicking 'Download as PDF'...")
                            pdf_btn = page.locator('text="Download as PDF"').first
                            
                            with page.expect_download(timeout=30000) as pdf_download_info:
                                pdf_btn.click()
                                
                            pdf_download = pdf_download_info.value
                            pdf_path = os.path.join(extract_path, f"{safe_project_name}.pdf")
                            pdf_download.save_as(pdf_path)
                            print(f"   ✅ PDF downloaded successfully.")
                            
                        except Exception as e:
                            print(f"   ⚠️ Exception during PDF download: {e}")
                        finally:
                            page.goto("https://www.overleaf.com/project")
                            page.wait_for_selector('a[href^="/project/"]', state='attached', timeout=Config.PLAYWRIGHT_TIMEOUT_MS)
                        
                        # --- NEW: Update SQLite Database ---
                        self.db.update_sync_registry(project_name, last_modified_text)
                        # We also ensure the project exists in the master project_state table!
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