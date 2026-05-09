import re
import time
import urllib.parse
import requests
import logging
from tenacity import retry, stop_after_attempt, wait_exponential

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
    Handles literature retrieval using:
      1. Semantic Scholar API (primary)
      2. SerpAPI Google Scholar (fallback)
      3. scholarly library (last resort)
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

        headers = {}
        if Config.SEMANTIC_SCHOLAR_API_KEY:
            headers["x-api-key"] = Config.SEMANTIC_SCHOLAR_API_KEY
            self.logger.debug(
                "Semantic Scholar request includes x-api-key header."
            )

        response = requests.get(url, headers=headers, timeout=15)
        self._last_semantic_scholar_call = time.time()

        if response.status_code != 200:
            self.logger.warning(
                "Semantic Scholar returned HTTP %d for query: '%s'",
                response.status_code, keywords
            )

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

    def search(self, keywords: str) -> list:
        """
        Main entry point for Semantic Scholar search.
        Returns results from the Semantic Scholar API, or empty list on failure.
        SerpAPI and scholarly fallbacks are handled at the agent level.
        Always returns a list of dictionaries.
        """
        if not keywords or keywords.strip() == "":
            self.logger.warning("Empty keywords provided. Returning empty list.")
            return []

        # Try Primary API
        try:
            results = self._fetch_from_semantic_scholar(keywords)
            if results:
                self.logger.info("Successfully fetched %d results from Semantic Scholar API.", len(results))
                return results
            else:
                self.logger.warning("Semantic Scholar API returned empty results.")
        except Exception as e:
            self.logger.warning("Semantic Scholar API failed: %s", str(e))

        return []

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
            if paper.get("openalex"):
                continue  # already enriched by OpenAlex search

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

    def _fetch_from_openalex(self, keywords: str, limit: int = 8) -> list:
        """
        Searches OpenAlex directly for papers matching the given keywords.
        Returns papers in the same dict format as _fetch_from_semantic_scholar,
        with an 'openalex' enrichment dict already attached.
        Returns empty list if OPENALEX_API_KEY is not set or on any error.
        """
        if not Config.OPENALEX_API_KEY:
            self.logger.warning(
                "OPENALEX_API_KEY is not set; skipping OpenAlex direct search."
            )
            return []

        try:
            params = {
                "search": keywords,
                "per_page": limit,
                "filter": "publication_year:>2021",
                "api_key": Config.OPENALEX_API_KEY,
                "select": (
                    "display_name,abstract_inverted_index,cited_by_count,"
                    "publication_year,topics,keywords,open_access,"
                    "primary_location,ids"
                ),
            }
            response = requests.get(
                "https://api.openalex.org/works",
                params=params,
                timeout=10
            )
            if response.status_code != 200:
                self.logger.warning(
                    "OpenAlex search returned status %d for keywords '%s'",
                    response.status_code, keywords,
                )
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
                        word_positions = []
                        for word, positions in index.items():
                            for pos in positions:
                                word_positions.append((pos, word))
                        word_positions.sort(key=lambda x: x[0])
                        abstract = " ".join(w for _, w in word_positions)
                    else:
                        abstract = ""

                    primary_location = work.get("primary_location") or {}
                    source = primary_location.get("source") or {}
                    venue = source.get("display_name", "")
                    paper_url = primary_location.get("landing_page_url", "")
                    open_access = work.get("open_access") or {}

                    results.append({
                        "title": title,
                        "abstract": abstract,
                        "year": str(work.get("publication_year", "")),
                        "citationCount": str(work.get("cited_by_count", "0")),
                        "venue": venue,
                        "url": paper_url,
                        "openalex": {
                            "topics": [
                                t["display_name"] for t in work.get("topics", [])
                                if "display_name" in t
                            ],
                            "keywords": [
                                k["display_name"] for k in work.get("keywords", [])
                                if "display_name" in k
                            ],
                            "is_oa": open_access.get("is_oa", False),
                            "oa_url": open_access.get("oa_url", None),
                        },
                    })
                except Exception as e:
                    self.logger.warning(
                        "OpenAlex search: failed to parse work '%s': %s",
                        work.get("display_name", "unknown"), str(e),
                    )
                    continue

            self.logger.info(
                "OpenAlex direct search returned %d papers for '%s'.",
                len(results), keywords,
            )

            papers = [p for p in results if
                      self._compute_keyword_overlap(p, keywords) >= 2]

            if not papers:
                self.logger.info(
                    "OpenAlex: all results filtered out by relevance check. "
                    "Returning empty list."
                )
            else:
                self.logger.info(
                    "OpenAlex: %d papers passed relevance filter.", len(papers)
                )

            return papers

        except Exception as e:
            self.logger.error("OpenAlex search failed for '%s': %s", keywords, str(e))
            return []

    def _compute_keyword_overlap(self, paper: dict, keywords: str) -> int:
        """Returns count of keyword tokens found in paper title+abstract."""
        stopwords = {
            "and", "or", "the", "for", "in", "with", "of", "a", "an",
            "to", "on", "at", "is", "are", "by", "from", "as", "be"
        }
        tokens = {
            w.lower() for w in keywords.split()
            if w.lower() not in stopwords and len(w) > 2
        }
        text = (paper.get("title", "") + " " + paper.get("abstract", "")).lower()
        return sum(1 for t in tokens if t in text)

    def fetch_from_openalex(self, keywords: str, limit: int = 8) -> list:
        """Public entry point for OpenAlex direct paper search."""
        return self._fetch_from_openalex(keywords, limit)

    # ==========================================
    # SerpAPI Google Scholar fallback
    # ==========================================

    def _extract_year_from_serpapi(self, item: dict) -> str:
        """Extracts a 4-digit year from a SerpAPI publication_info summary string."""
        summary = item.get("publication_info", {}).get("summary", "")
        match = re.search(r'\b(19|20)\d{2}\b', summary)
        return match.group(0) if match else ""

    def _fetch_from_serpapi(self, keywords: str) -> list:
        """
        Searches Google Scholar via SerpAPI when Semantic Scholar is unavailable.
        Returns papers in the same dict format as _fetch_from_semantic_scholar.
        Returns empty list if SERPAPI_API_KEY is not configured or on any error.
        """
        if not Config.SERPAPI_API_KEY:
            self.logger.warning("SERPAPI_API_KEY is not set; skipping SerpAPI search.")
            return []

        try:
            url = "https://serpapi.com/search"
            params = {
                "engine": "google_scholar",
                "q": keywords,
                "api_key": Config.SERPAPI_API_KEY,
                "num": 10,
            }
            response = requests.get(url, params=params, timeout=15)
            if response.status_code != 200:
                self.logger.warning(
                    "SerpAPI returned status %d for keywords '%s'",
                    response.status_code, keywords,
                )
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
                    "citationCount": str(
                        item.get("inline_links", {}).get("cited_by", {}).get("total", 0)
                    ),
                    "venue": item.get("publication_info", {}).get("summary", ""),
                    "url": item.get("link", ""),
                    "openalex": {},
                })

            self.logger.info(
                "SerpAPI returned %d papers for '%s'.", len(results), keywords
            )
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
        """
        Last-resort Google Scholar search using the scholarly Python library.
        Does not require login, session files, or browser automation.
        Returns papers in the same dict format as other fetch methods.
        Only called when both Semantic Scholar and SerpAPI have failed.
        """
        if not SCHOLARLY_AVAILABLE:
            self.logger.warning(
                "scholarly library is not installed; skipping scholarly search."
            )
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

            self.logger.info(
                "scholarly returned %d papers for '%s'.", len(results), keywords
            )
            return results

        except Exception as e:
            self.logger.error("scholarly search failed for '%s': %s", keywords, str(e))
            return []

    def fetch_from_scholarly(self, keywords: str) -> list:
        """Public entry point for scholarly Google Scholar search."""
        return self._fetch_from_scholarly(keywords)
