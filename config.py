import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from the .env file
load_dotenv()

class Config:
    """
    Central configuration class for the Academic Research Multi-Agent System.
    All paths, API settings, and model configurations are defined here.
    """
    
    # ==========================================
    # 1. Base Paths & Directories
    # ==========================================
    BASE_DIR = Path(__file__).parent.absolute()
    
    OVERLEAF_DIR = BASE_DIR / "overleaf_projects"
    LIBRARY_DIR = BASE_DIR / "research_library"
    LOGS_DIR = BASE_DIR / "logs"
    
    # ==========================================
    # 2. State & Data Files 
    # ==========================================
    SYNC_REGISTRY_PATH = BASE_DIR / "sync_registry.json"
    # NOTE: `researchers_map.json` is provided for migration only
    # and should NOT be treated as the live source of truth once
    # the application has migrated data into the SQLite DB.
    RESEARCHERS_MAP_PATH = BASE_DIR / "researchers_map.json"
    SCHOLAR_STATE_PATH = BASE_DIR / "scholar_state.json"
    OVERLEAF_STATE_PATH = BASE_DIR / "overleaf_state.json"
    OVERLEAF_USER_DATA_DIR: str = str(BASE_DIR / "playwright_state" / "overleaf_profile")
    
    # ==========================================
    # 3. LLM Configuration (Groq)
    # ==========================================
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    LLM_MODEL_NAME = "openai/gpt-oss-120b"
    # Cheaper/faster Groq model used only for lightweight extraction calls (keyword
    # generation, paper relevance filtering) — synthesis calls that produce the actual
    # content a researcher reads (reviews, feedback, reports) keep LLM_MODEL_NAME.
    # Groq deleted llama-3.1-8b-instant (~2026-08-17); reusing LLM_MODEL_NAME here
    # until a dedicated lightweight replacement is confirmed still live in Groq's
    # catalog. Verify this model id is still current before relying on it.
    LLM_EXTRACTION_MODEL_NAME = os.getenv("LLM_EXTRACTION_MODEL_NAME", LLM_MODEL_NAME)
    
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    # gemini-1.5-flash is retired (confirmed 2026-08-21 by calling the live
    # models.list endpoint with a real GEMINI_API_KEY — it is absent from the current
    # catalog). gemini-2.5-flash was confirmed present in that same live models.list
    # call AND confirmed working with a live generate_content("Say OK") test call made
    # during this session. Stable (non-preview) 2.5-generation model — same
    # speed/cost tier as the old 1.5-flash it replaces.
    GEMINI_MODEL_NAME = "gemini-2.5-flash"

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL_NAME = "gpt-4o-mini"

    # Cerebras — live-verified numbers from the operator's own account dashboard
    # (2026-08-21). gpt-oss-120b is labeled Production in Cerebras's dashboard;
    # gemma-4-31b also exists there but is labeled Preview and must NOT be used for
    # production pipeline calls.
    CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY")
    CEREBRAS_MODEL_NAME = "gpt-oss-120b"
    CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
    # Live-verified: 5 requests/minute, 2,400 requests/day (operator dashboard).
    CEREBRAS_RATE_LIMIT_RPM = 5
    # Proactive client-side ceiling used by the Cerebras concurrency guard in
    # base_agent.py (_SlidingWindowLimiter) — deliberately below the live 5 rpm limit
    # to leave headroom for clock-boundary/timing-window slop rather than racing the
    # real ceiling exactly.
    # NOTE: this limiter's window is held in-process only (not persisted, unlike
    # llm_provider_cooldowns) — correct for today's one-process-per-run architecture,
    # but a future per-project-container deployment would give each container its own
    # independent window, so N containers could collectively exceed the real 5rpm
    # ceiling by up to Nx. See the "IMPORTANT" note on _SlidingWindowLimiter in
    # base_agent.py before assuming this is already cross-process-safe.
    CEREBRAS_SAFE_RPM = 4

    # NVIDIA NIM — model id and base URL confirmed live (2026-08-21) via
    # https://integrate.api.nvidia.com/v1/models (reachable without auth; returned
    # "nvidia/nemotron-3-super-120b-a12b" in the model list — note the "nvidia/"
    # prefix, which the plan's shorthand name omitted). Context window and rate
    # limits could NOT be verified live (no NVIDIA_NIM_API_KEY available in this
    # environment to call an authenticated endpoint or check the account dashboard).
    # Treated conservatively like Groq's cooldown/retry shape per the extension
    # brief's guidance, since NIM's API is also OpenAI-compatible. Operator should
    # confirm actual context window / rate limits before relying on this in
    # production.
    NVIDIA_NIM_API_KEY = os.getenv("NVIDIA_NIM_API_KEY")
    NVIDIA_NIM_MODEL_NAME = "nvidia/nemotron-3-super-120b-a12b"
    NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

    LLM_MAX_RETRIES = 3
    LLM_TIMEOUT_SECONDS = 30
    # How long a provider is skipped after returning a rate-limit error, shared across
    # all agents in the process so one 429 protects every subsequent LLM call this run.
    LLM_RATE_LIMIT_COOLDOWN_SECONDS = int(os.getenv("LLM_RATE_LIMIT_COOLDOWN_SECONDS", "90"))
    EMAIL_MAX_RETRIES = int(os.getenv("EMAIL_MAX_RETRIES", "3"))
    
    # ==========================================
    # 4. API & Network Configuration
    # ==========================================
    
    SEMANTIC_SCHOLAR_RATE_LIMIT = 100  # Requests per 5 minutes
    SEMANTIC_SCHOLAR_API_KEY: str = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    PLAYWRIGHT_TIMEOUT_MS = 30000      # 30 seconds in milliseconds
    OPENALEX_API_KEY: str = os.getenv("OPENALEX_API_KEY", "")
    SERPAPI_API_KEY: str = os.getenv("SERPAPI_API_KEY", "")
    # Playwright headless mode — set to False only for local debugging
    PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() != "false"

    # ==========================================
    # 5. Email Configuration
    # ==========================================
    # The GMAIL account that physically SENDS the email
    NOTIFICATION_SENDER_EMAIL = os.getenv("NOTIFICATION_SENDER_EMAIL")
    NOTIFICATION_SENDER_PASSWORD = os.getenv("NOTIFICATION_SENDER_PASSWORD")
    
    # The Overleaf LOGIN account (used only for scraping/automation — may differ from researcher email)
    OVERLEAF_EMAIL = os.getenv("OVERLEAF_EMAIL")
    # Overleaf account password (used for scraping / automation).
    OVERLEAF_PASSWORD = os.getenv("OVERLEAF_PASSWORD")

    # The researcher email that RECEIVES notifications (defaults to OVERLEAF_EMAIL if not set)
    RESEARCHER_EMAIL = os.getenv("RESEARCHER_EMAIL") or os.getenv("OVERLEAF_EMAIL")
    
    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 465
    
    # ==========================================
    # 6. Logging Configuration
    # ==========================================
    MAX_LOG_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB limit per log file
    LOG_BACKUP_COUNT = 3                  # Keep 3 backup log files
    
    # ==========================================
    # 7. System & Maintenance
    # ==========================================
    GARBAGE_COLLECTION_TTL_DAYS = int(os.getenv("GARBAGE_COLLECTION_TTL_DAYS", "30"))
    PROGRESS_SNAPSHOT_TTL_DAYS = 60

    # ==========================================
    # 8. Agent Tuning Parameters
    # ==========================================
    # Max chars of LaTeX delta text fed to LLM per progress-tracking call
    MAX_DELTA_CHARS: int = 8000
    # Min chars of delta before skipping LLM analysis
    MIN_DELTA_CHARS: int = 50
    # Max number of unique papers kept per literature search cycle
    MAX_LITERATURE_PAPERS: int = 15
    # Max chars of project LaTeX text used for keyword extraction
    MAX_PROJECT_TEXT_CHARS: int = 4000
    # Total char budget for the paper-abstract JSON payload sent to the LLM
    # summarization call, split adaptively across however many papers are in
    # that batch (see utils/token_budget.py). Sized to keep the whole payload
    # comfortably under Groq's 8000 TPM ceiling even at the full 15-paper cap.
    TOTAL_ABSTRACT_BUDGET_CHARS: int = 16000
    # Floor and ceiling on the per-paper abstract cap computed from the budget above
    MIN_ABSTRACT_CHARS: int = 300
    MAX_ABSTRACT_CHARS: int = 1200
    # Min chars of paper text required to run the internal peer review
    MIN_REVIEW_LENGTH: int = 3000
    # Consecutive Stanford upload failures tolerated before giving up and falling back
    # to the internal LLM review — avoids burning an LLM call on a transient rate-limit
    # or brief outage that would have succeeded on the next scheduled run.
    STANFORD_MAX_UPLOAD_RETRIES: int = int(os.getenv("STANFORD_MAX_UPLOAD_RETRIES", "2"))
    # Consecutive Stanford upload failures across ANY projects in the same run before
    # assuming Stanford itself is down and skipping further browser attempts this run.
    STANFORD_CONSECUTIVE_FAILURE_THRESHOLD: int = int(os.getenv("STANFORD_CONSECUTIVE_FAILURE_THRESHOLD", "3"))
    # A STARTED agent_runs row with no SUCCESS/FAILURE after this many hours is
    # assumed to mean the process was killed outright, not just slow.
    STALE_RUN_THRESHOLD_HOURS: float = float(os.getenv("STALE_RUN_THRESHOLD_HOURS", "2.0"))
    # ThreadPoolExecutor max_workers for progress + literature agents
    PROGRESS_MAX_WORKERS: int = int(os.getenv("PROGRESS_MAX_WORKERS", "4"))
    LITERATURE_MAX_WORKERS: int = int(os.getenv("LITERATURE_MAX_WORKERS", "4"))

    @classmethod
    def validate(cls):
        """
        Validate that required environment variables are present.
        Raises ValueError listing any missing variables.
        """
        required = [
            ("GROQ_API_KEY", cls.GROQ_API_KEY),
            ("NOTIFICATION_SENDER_EMAIL", cls.NOTIFICATION_SENDER_EMAIL),
            ("NOTIFICATION_SENDER_PASSWORD", cls.NOTIFICATION_SENDER_PASSWORD),
            ("OVERLEAF_EMAIL", cls.OVERLEAF_EMAIL),
            ("OVERLEAF_PASSWORD", cls.OVERLEAF_PASSWORD),
            ("RESEARCHER_EMAIL (or OVERLEAF_EMAIL as fallback)", cls.RESEARCHER_EMAIL),
        ]

        missing = [name for name, val in required if not val]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        if cls.NOTIFICATION_SENDER_EMAIL and "@" not in cls.NOTIFICATION_SENDER_EMAIL:
            raise ValueError("NOTIFICATION_SENDER_EMAIL does not appear to be a valid email address.")
        if cls.OVERLEAF_EMAIL and "@" not in cls.OVERLEAF_EMAIL:
            raise ValueError("OVERLEAF_EMAIL does not appear to be a valid email address.")
        if cls.GROQ_API_KEY and not cls.GROQ_API_KEY.startswith("gsk_"):
            raise ValueError("GROQ_API_KEY does not appear to be a valid Groq key (expected prefix: gsk_).")