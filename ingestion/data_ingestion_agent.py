import os
import json
import shutil
import zipfile
import time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

class DataIngestionAgent:
    """
    Data Ingestion Agent: Performs a Delta Sync.
    Downloads BOTH the source ZIP (for text delta extraction) AND the compiled PDF (for Stanford review).
    """
    def __init__(self):
        self.email = os.getenv("OVERLEAF_EMAIL")
        self.password = os.getenv("OVERLEAF_PASSWORD")
        self.state_file = "overleaf_state.json"
        self.registry_file = "sync_registry.json"
        self.download_dir = os.path.abspath("overleaf_projects")
        
        if not os.path.exists(self.download_dir):
            os.makedirs(self.download_dir)

    def _perform_manual_login(self):
        """Fallback method: Opens browser for user to log in."""
        print("\n🛑 No saved session found! Initiating manual login...")
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
            print("⏳ Waiting up to 60 seconds for you to reach the dashboard...")
            
            try:
                page.wait_for_url("**/project**", timeout=60000)
                print("✅ Reached dashboard! Saving session securely...")
                context.storage_state(path=self.state_file)
                time.sleep(2)
            except Exception as e:
                print(f"❌ Login failed or timed out: {e}")
            finally:
                context.close()
                browser.close()

    def _load_registry(self) -> dict:
        if os.path.exists(self.registry_file):
            with open(self.registry_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save_registry(self, registry: dict):
        with open(self.registry_file, 'w', encoding='utf-8') as f:
            json.dump(registry, f, indent=4)

    def sync_all_projects(self) -> list:
        print("🤖 Starting Delta Sync Ingestion Cycle...")
        
        if not os.path.exists(self.state_file):
            self._perform_manual_login()
            
        if not os.path.exists(self.state_file):
            print("❌ Still no session file. Aborting sync.")
            return []

        registry = self._load_registry()
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
                
                page.wait_for_selector('a[href^="/project/"]', state='attached', timeout=20000)
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
                    
                    is_new = project_name not in registry
                    is_modified = not is_new and registry[project_name] != last_modified_text
                    
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
                        pdf_download_url = f"https://www.overleaf.com{href}/output/output.pdf"
                        print(f"   📄 Downloading compiled PDF...")
                        try:
                            with page.expect_download(timeout=15000) as pdf_download_info:
                                page.evaluate(f"window.location.href = '{pdf_download_url}'")
                            pdf_download = pdf_download_info.value
                            # Save PDF directly into the extracted folder
                            pdf_path = os.path.join(extract_path, f"{safe_project_name}.pdf")
                            pdf_download.save_as(pdf_path)
                            print(f"   ✅ PDF downloaded successfully.")
                        except Exception as e:
                            print(f"   ⚠️ Could not download PDF (it might not be compiled in Overleaf): {e}")
                        
                        registry[project_name] = last_modified_text
                        updated_projects.append(project_name)
                        print(f"✅ Synced '{project_name}'.")
                    else:
                        print(f"⏭️  [SKIPPED] '{project_name}' is up to date.")

                self._save_registry(registry)
                
            except Exception as e:
                print(f"❌ Sync failed: {e}")
                
            finally:
                context.close()
                browser.close()
                
        print(f"🎉 Ingestion complete! {len(updated_projects)} projects were downloaded.")
        return updated_projects

if __name__ == "__main__":
    agent = DataIngestionAgent()
    agent.sync_all_projects()