import os
import re
import time
import threading
import urllib.parse
import requests
import logging
from tenacity import retry, stop_after_attempt, wait_exponential
from playwright.sync_api import sync_playwright

# Import centralized configuration
from config import Config

try:
    from scholarly import scholarly as _scholarly_lib
    SCHOLARLY_AVAILABLE = True
except ImportError:
    _scholarly_lib = None
    SCHOLARLY_AVAILABLE = False

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
        self._rate_limit_lock = threading.Lock()
        self._openalex_warned = False
        self._stats = {
            "semantic_scholar_hits": 0,
            "semantic_scholar_misses": 0,
            "google_scholar_hits": 0,
            "total_calls": 0,
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=10), reraise=False)
    def _fetch_from_semantic_scholar(self, keywords: str, limit: int = 10) -> list:
        """PRIMARY: Uses Semantic Scholar API. Retries up to 3 times on failure."""

        with self._rate_limit_lock:
            now = time.time()
            sleep_time = max(0.0, self._min_seconds_between_calls - (now - self._last_semantic_scholar_call))
            # Reserve the slot before releasing the lock so concurrent callers queue up
            self._last_semantic_scholar_call = now + sleep_time

        if sleep_time > 0:
            self.logger.info("Rate limiting: sleeping %.2fs before Semantic Scholar call.", sleep_time)
            time.sleep(sleep_time)

        self.logger.info("Attempting Semantic Scholar API for keywords: '%s'", keywords)
        query = urllib.parse.quote_plus(keywords)

        # Searching for papers from 2023 onwards, fetching configurable number of results
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={query}&limit={limit}&year=2023-&fields=title,abstract,year,citationCount,venue,url"

        headers = {}
        if Config.SEMANTIC_SCHOLAR_API_KEY:
            headers["x-api-key"] = Config.SEMANTIC_SCHOLAR_API_KEY

        response = requests.get(url, headers=headers, timeout=15)
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
                page.wait_for_url("https://scholar.google.com/**", timeout=300000)
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

        with self._rate_limit_lock:
            self._stats["total_calls"] += 1

        # 1. Try Primary API
        try:
            results = self._fetch_from_semantic_scholar(keywords)
            if results:
                with self._rate_limit_lock:
                    self._stats["semantic_scholar_hits"] += 1
                self.logger.info("Successfully fetched %d results from Semantic Scholar API.", len(results))
                return results
            else:
                with self._rate_limit_lock:
                    self._stats["semantic_scholar_misses"] += 1
                self.logger.warning("Semantic Scholar API returned empty results.")
        except Exception as e:
            self.logger.warning("Semantic Scholar API failed: %s", str(e))

        # 2. Try Fallback Scraper
        fallback_results = self._fetch_from_google_scholar(keywords)
        if fallback_results:
            with self._rate_limit_lock:
                self._stats["google_scholar_hits"] += 1
            self.logger.info("Successfully fetched %d results from Google Scholar fallback.", len(fallback_results))
        self.logger.info("Fetcher stats: %s", self._stats)
        return fallback_results

    def get_stats(self) -> dict:
        """Returns accumulated call statistics since this instance was created."""
        stats = dict(self._stats)
        total = stats["total_calls"]
        hits = stats["semantic_scholar_hits"]
        stats["semantic_scholar_hit_rate"] = f"{hits/total*100:.1f}%" if total > 0 else "N/A"
        return stats

    # ==========================================
    # OpenAlex enrichment
    # ==========================================

    def _enrich_with_openalex(self, papers: list) -> list:
        if not Config.OPENALEX_API_KEY:
            if not self._openalex_warned:
                self.logger.warning("OPENALEX_API_KEY is not set; skipping OpenAlex enrichment.")
                self._openalex_warned = True
            for paper in papers:
                paper["openalex"] = {}
            return papers

        for paper in papers:
            if paper.get("openalex"):
                continue
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
                response = requests.get(url, timeout=20)
                if response.status_code != 200:
                    self.logger.warning("OpenAlex returned status %d for '%s'", response.status_code, title)
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
                paper["openalex"] = {"topics": topics, "keywords": keywords, "is_oa": is_oa, "oa_url": oa_url}
            except Exception as e:
                self.logger.error("OpenAlex enrichment error for '%s': %s", title, str(e))
                paper["openalex"] = {}
            time.sleep(0.2)
        return papers

    def enrich_with_openalex(self, papers: list) -> list:
        """Public entry point for OpenAlex enrichment."""
        return self._enrich_with_openalex(papers)

    def _compute_keyword_overlap(self, paper: dict, keywords: str) -> int:
        stopwords = {"and", "or", "the", "for", "in", "with", "of", "a", "an", "to", "on", "at", "is", "are", "by", "from", "as", "be"}
        tokens = {w.lower() for w in keywords.split() if w.lower() not in stopwords and len(w) > 2}
        text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
        return sum(1 for t in tokens if t in text)

    def _fetch_from_openalex(self, keywords: str, limit: int = 8) -> list:
        if not Config.OPENALEX_API_KEY:
            self.logger.warning("OPENALEX_API_KEY is not set; skipping OpenAlex direct search.")
            return []
        try:
            params = {
                "search": keywords,
                "per_page": limit,
                "filter": "publication_year:>2021",
                "api_key": Config.OPENALEX_API_KEY,
                "select": "display_name,abstract_inverted_index,cited_by_count,publication_year,topics,keywords,open_access,primary_location,ids",
            }
            response = requests.get("https://api.openalex.org/works", params=params, timeout=10)
            if response.status_code != 200:
                self.logger.warning("OpenAlex search returned status %d for keywords '%s'", response.status_code, keywords)
                return []
            data = response.json()
            results = []
            for work in data.get("results", []):
                try:
                    title = work.get("display_name", "")
                    if not title:
                        continue
                    index = work.get("abstract_inverted_index") or {}
                    if index:
                        word_positions = sorted([(pos, word) for word, positions in index.items() for pos in positions])
                        abstract = " ".join(w for _, w in word_positions)
                    else:
                        abstract = ""
                    primary_location = work.get("primary_location") or {}
                    source = primary_location.get("source") or {}
                    open_access = work.get("open_access") or {}
                    results.append({
                        "title": title,
                        "abstract": abstract,
                        "year": str(work.get("publication_year", "")),
                        "citationCount": str(work.get("cited_by_count", "0")),
                        "venue": source.get("display_name", ""),
                        "url": primary_location.get("landing_page_url", ""),
                        "openalex": {
                            "topics": [t["display_name"] for t in work.get("topics", []) if "display_name" in t],
                            "keywords": [k["display_name"] for k in work.get("keywords", []) if "display_name" in k],
                            "is_oa": open_access.get("is_oa", False),
                            "oa_url": open_access.get("oa_url", None),
                        },
                    })
                except Exception as e:
                    self.logger.warning("OpenAlex search: failed to parse work '%s': %s", work.get("display_name", "unknown"), str(e))
            papers = [p for p in results if self._compute_keyword_overlap(p, keywords) >= 2]
            self.logger.info("OpenAlex direct search returned %d papers for '%s'.", len(papers), keywords)
            return papers
        except Exception as e:
            self.logger.error("OpenAlex search failed for '%s': %s", keywords, str(e))
            return []

    def fetch_from_openalex(self, keywords: str, limit: int = 8) -> list:
        """Public entry point for OpenAlex direct paper search."""
        return self._fetch_from_openalex(keywords, limit)

    # ==========================================
    # SerpAPI Google Scholar fallback
    # ==========================================

    def _extract_year_from_serpapi(self, item: dict) -> str:
        summary = item.get("publication_info", {}).get("summary", "")
        match = re.search(r'\b(19|20)\d{2}\b', summary)
        return match.group(0) if match else ""

    def _fetch_from_serpapi(self, keywords: str) -> list:
        if not Config.SERPAPI_API_KEY:
            self.logger.warning("SERPAPI_API_KEY is not set; skipping SerpAPI search.")
            return []
        try:
            url = "https://serpapi.com/search"
            params = {"engine": "google_scholar", "q": keywords, "api_key": Config.SERPAPI_API_KEY, "num": 10}
            response = requests.get(url, params=params, timeout=15)
            if response.status_code != 200:
                self.logger.warning("SerpAPI returned status %d for keywords '%s'", response.status_code, keywords)
                return []
            results = []
            for item in response.json().get("organic_results", []):
                title = item.get("title", "")
                if not title:
                    continue
                results.append({
                    "title": title,
                    "abstract": item.get("snippet", ""),
                    "year": self._extract_year_from_serpapi(item),
                    "citationCount": str(item.get("inline_links", {}).get("cited_by", {}).get("total", 0)),
                    "venue": item.get("publication_info", {}).get("summary", ""),
                    "url": item.get("link", ""),
                    "openalex": {},
                })
            self.logger.info("SerpAPI returned %d papers for '%s'.", len(results), keywords)
            return results
        except Exception as e:
            self.logger.error("SerpAPI search failed for '%s': %s", keywords, str(e))
            return []

    def fetch_from_serpapi(self, keywords: str) -> list:
        """Public entry point for SerpAPI Google Scholar search."""
        return self._fetch_from_serpapi(keywords)

    # ==========================================
    # scholarly last-resort fallback
    # ==========================================

    def _fetch_from_scholarly(self, keywords: str) -> list:
        if not SCHOLARLY_AVAILABLE:
            self.logger.warning("scholarly library is not installed; skipping scholarly search.")
            return []
        try:
            search_query = _scholarly_lib.search_pubs(keywords)
            results = []
            for i, pub in enumerate(search_query):
                if i >= 8:
                    break
                bib = pub.get("bib", {})
                title = bib.get("title", "")
                if not title:
                    continue
                results.append({
                    "title": title,
                    "abstract": bib.get("abstract", ""),
                    "year": str(bib.get("pub_year", "")),
                    "citationCount": str(pub.get("num_citations", 0)),
                    "venue": bib.get("venue", ""),
                    "url": pub.get("pub_url", ""),
                    "openalex": {},
                })
            self.logger.info("scholarly returned %d papers for '%s'.", len(results), keywords)
            return results
        except Exception as e:
            self.logger.error("scholarly search failed for '%s': %s", keywords, str(e))
            return []

    def fetch_from_scholarly(self, keywords: str) -> list:
        """Public entry point for scholarly Google Scholar search."""
        return self._fetch_from_scholarly(keywords)