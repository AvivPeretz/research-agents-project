import os
import re
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
        self._openalex_warned = False

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=False)
    def _fetch_from_semantic_scholar(self, keywords: str, limit: int = 10) -> list:
        """PRIMARY: Uses Semantic Scholar API. Retries up to 3 times on failure."""

        #enforce rate limit
        elapsed = time.time() - self._last_semantic_scholar_call
        if elapsed < self._min_seconds_between_calls:
            sleep_time = self._min_seconds_between_calls - elapsed
            self.logger.info("Rate limiting: sleeping %.2fs before Semantic Scholar call.", sleep_time)
            time.sleep(sleep_time)

        self.logger.info("Attempting Semantic Scholar API for keywords: '%s'", keywords)
        query = urllib.parse.quote_plus(keywords)

        # Searching for papers from 2023 onwards, fetching configurable number of results
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit={limit}&year=2023-&fields=title,abstract,year,citationCount,venue,url"
        
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
                    page.wait_for_selector('div.gs_r.gs_or.gs_scl', timeout=60000)

                # Try current DOM selector, fall back to older alternatives
                primary_selector = 'div.gs_r.gs_or.gs_scl'
                fallback_selectors = ['div[data-rp]', 'div.gs_r']
                article_elements = []

                if page.locator(primary_selector).count() > 0:
                    page.wait_for_selector(primary_selector, timeout=Config.PLAYWRIGHT_TIMEOUT_MS)
                    article_elements = page.locator(primary_selector).all()[:3]
                else:
                    for sel in fallback_selectors:
                        if page.locator(sel).count() > 0:
                            page.wait_for_selector(sel, timeout=Config.PLAYWRIGHT_TIMEOUT_MS)
                            article_elements = page.locator(sel).all()[:3]
                            break

                for el in article_elements:
                    try:
                        title_el = el.locator('h3.gs_rt a')
                        if title_el.count() > 0:
                            title = title_el.inner_text()
                            link = title_el.get_attribute('href') or ""
                        else:
                            heading = el.locator('h3.gs_rt')
                            title = heading.inner_text() if heading.count() > 0 else ""
                            link = ""
                        if not title:
                            continue
                    except Exception:
                        continue

                    try:
                        snippet_el = el.locator('div.gs_rs')
                        snippet = snippet_el.inner_text() if snippet_el.count() > 0 else ""
                    except Exception:
                        snippet = ""

                    try:
                        block_text = el.inner_text()
                        year_match = re.search(r'\b(19|20)\d{2}\b', block_text)
                        year = year_match.group(0) if year_match else "N/A"
                    except Exception:
                        year = "N/A"

                    try:
                        block_text = el.inner_text()
                        cited_match = re.search(r'Cited by (\d+)', block_text)
                        citation_count = cited_match.group(1) if cited_match else "0"
                    except Exception:
                        citation_count = "0"

                    results.append({
                        "title": title,
                        "link": link,
                        "snippet": snippet,
                        "year": year,
                        "citationCount": citation_count,
                        "venue": "N/A",
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

    def _enrich_with_openalex(self, papers: list) -> list:
        """
        Enriches a list of paper dicts with metadata from the OpenAlex API.
        For each paper, queries OpenAlex by title to retrieve:
          - topics: list of automatically-assigned topic names (up to 5)
          - keywords: list of keyword strings
          - is_oa: boolean — whether the paper is open access
          - oa_url: direct open access URL if available

        Returns the same list of paper dicts, each extended with an
        'openalex' key containing the enrichment dict.
        If OPENALEX_API_KEY is empty or a paper cannot be found,
        the 'openalex' key is set to an empty dict for that paper.
        All errors are caught and logged — never raises.
        """
        if not Config.OPENALEX_API_KEY:
            if not self._openalex_warned:
                self.logger.warning(
                    "OPENALEX_API_KEY is not set; skipping OpenAlex enrichment."
                )
                self._openalex_warned = True
            for paper in papers:
                paper["openalex"] = {}
            return papers

        for paper in papers:
            title = paper.get("title", "")
            if not title:
                paper["openalex"] = {}
                continue

            try:
                encoded_title = urllib.parse.quote_plus(title)
                url = (
                    f"https://api.openalex.org/works"
                    f"?search={encoded_title}"
                    f"&select=title,keywords,topics,open_access,primary_location"
                    f"&api_key={Config.OPENALEX_API_KEY}"
                    f"&per_page=1"
                )
                response = requests.get(url, timeout=10)
                if response.status_code != 200:
                    self.logger.warning(
                        "OpenAlex returned status %d for '%s'", response.status_code, title
                    )
                    paper["openalex"] = {}
                    time.sleep(0.2)
                    continue

                data = response.json()
                results = data.get("results", [])
                if not results:
                    paper["openalex"] = {}
                    time.sleep(0.2)
                    continue

                result = results[0]
                topics = [t["display_name"] for t in result.get("topics", [])][:5]
                keywords = [k["display_name"] for k in result.get("keywords", [])]
                is_oa = result.get("open_access", {}).get("is_oa", False)
                oa_url = result.get("open_access", {}).get("oa_url", None)

                paper["openalex"] = {
                    "topics": topics,
                    "keywords": keywords,
                    "is_oa": is_oa,
                    "oa_url": oa_url,
                }
            except Exception as e:
                self.logger.error(
                    "OpenAlex enrichment error for '%s': %s", title, str(e)
                )
                paper["openalex"] = {}

            time.sleep(0.2)

        return papers

    def enrich_with_openalex(self, papers: list) -> list:
        """Public entry point for OpenAlex enrichment."""
        return self._enrich_with_openalex(papers)