import os
import time
import urllib.parse
import requests
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from playwright.sync_api import sync_playwright

# Import centralized configuration
from config import Config

class LiteratureFetcher:
    """
    Handles literature retrieval using a Primary API (Semantic Scholar) 
    and a Fallback Web Scraper (Google Scholar via Playwright).
    Never raises an exception to the caller; always returns a list of results.
    """
    def __init__(self):
        self.logger = logging.getLogger("LiteratureFetcher")
        if not self.logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            self.logger.addHandler(ch)
            self.logger.setLevel(logging.INFO)
            
        self.state_file = Config.SCHOLAR_STATE_PATH
        self.dummy_email = Config.NOTIFICATION_SENDER_EMAIL
        self._last_semantic_scholar_call = 0.0
        self._min_seconds_between_calls = 300.0 / Config.SEMANTIC_SCHOLAR_RATE_LIMIT

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=False)
    def _fetch_from_semantic_scholar(self, keywords: str) -> list:
        """PRIMARY: Uses Semantic Scholar API. Retries up to 3 times on failure."""
        
        #enforce rate limit
        elapsed = time.time() - self._last_semantic_scholar_call
        if elapsed < self._min_seconds_between_calls:
            sleep_time = self._min_seconds_between_calls - elapsed
            self.logger.info("Rate limiting: sleeping %.2fs before Semantic Scholar call.", sleep_time)
            time.sleep(sleep_time)    

        self.logger.info("Attempting Semantic Scholar API for keywords: '%s'", keywords)
        query = urllib.parse.quote_plus(keywords)
        
        # Searching for papers from 2023 onwards, fetching 5 results
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit=5&year=2023-&fields=title,abstract,year,citationCount,venue,url"
        
        response = requests.get(url, timeout=15)
        self._last_semantic_scholar_call = time.time()
        response.raise_for_status() # Will trigger a retry if HTTP error occurs
        data = response.json()
        
        results = []
        if data.get('data'):
            for item in data['data']:
                results.append({
                    "title": item.get('title', 'Unknown Title'),
                    "link": item.get('url', ''),
                    # Map the abstract to our expected 'snippet' format
                    "snippet": item.get('abstract') or "No abstract available.",
                    "year": str(item.get('year', 'N/A')),
                    "citationCount": str(item.get('citationCount', 'N/A')),
                    "venue": item.get('venue') or "N/A"
                })
        return results

    def _perform_manual_login(self):
        """Fallback method: Opens browser for user to log into Google Scholar."""
        print("\n🛑 No Google Scholar session found! Initiating manual login...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=Config.PLAYWRIGHT_HEADLESS)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            
            login_url = "https://accounts.google.com/ServiceLogin?continue=https://scholar.google.com/"
            page.goto(login_url)
            
            print(f"🔑 Please log in using the dummy account: {self.dummy_email}")
            print("\n🚨 ACTION REQUIRED: Complete the login and solve any CAPTCHAs.")
            
            try:
                page.wait_for_url("https://scholar.google.com/**", timeout=90000)
                print("✅ Reached Google Scholar! Saving session securely...")
                context.storage_state(path=self.state_file)
                time.sleep(2)
            except Exception as e:
                print(f"❌ Login failed or timed out: {e}")
            finally:
                context.close()
                browser.close()

    def _fetch_from_google_scholar(self, keywords: str) -> list:
        """FALLBACK: Scrapes Google Scholar if the primary API fails."""
        self.logger.info("Falling back to Google Scholar scraping...")
        
        if not os.path.exists(self.state_file):
            self._perform_manual_login()
            
        if not os.path.exists(self.state_file):
            self.logger.error("No session file for Scholar. Cannot proceed with fallback scraping.")
            return []

        results = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=Config.PLAYWRIGHT_HEADLESS)
            context = browser.new_context(storage_state=self.state_file)
            page = context.new_page()
            
            try:
                query = urllib.parse.quote_plus(keywords)
                page.goto(f"https://scholar.google.com/scholar?q={query}&as_ylo=2023")
                
                if page.locator('form[id="captcha-form"]').count() > 0:
                    print("⚠️ Scholar CAPTCHA detected! Please solve it in the browser window.")
                    page.wait_for_selector('div.gs_ri', timeout=60000)
                
                # Fetch up to 3 results from the fallback
                page.wait_for_selector('div.gs_ri', timeout=Config.PLAYWRIGHT_TIMEOUT_MS)
                article_elements = page.locator('div.gs_ri').all()[:3]
                
                for el in article_elements:
                    title_el = el.locator('h3.gs_rt a')
                    if title_el.count() > 0:
                        title = title_el.inner_text()
                        link = title_el.get_attribute('href')
                        snippet = el.locator('div.gs_rs').inner_text() if el.locator('div.gs_rs').count() > 0 else "No snippet available."
                        
                        results.append({
                            "title": title,
                            "link": link,
                            "snippet": snippet,
                            # Fallback data lacks rich API info, so we use N/A
                            "year": "N/A",
                            "citationCount": "N/A",
                            "venue": "N/A"
                        })
            except Exception as e:
                self.logger.error("Error in fallback Google Scholar scraping: %s", str(e))
            finally:
                context.close()
                browser.close()
                
        return results

    def search(self, keywords: str) -> list:
        """
        Main entry point. Attempts Primary API first, then falls back to web scraping.
        Always returns a list of dictionaries.
        """
        if not keywords or keywords.strip() == "":
            self.logger.warning("Empty keywords provided. Returning empty list.")
            return []

        # 1. Try Primary API
        try:
            results = self._fetch_from_semantic_scholar(keywords)
            if results:
                self.logger.info("Successfully fetched %d results from Semantic Scholar API.", len(results))
                return results
            else:
                self.logger.warning("Semantic Scholar API returned empty results.")
        except Exception as e:
            self.logger.warning("Semantic Scholar API failed: %s", str(e))

        # 2. Try Fallback Scraper
        fallback_results = self._fetch_from_google_scholar(keywords)
        if fallback_results:
            self.logger.info("Successfully fetched %d results from Google Scholar fallback.", len(fallback_results))
        return fallback_results