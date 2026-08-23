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
    # Run-lock file (fcntl.flock) preventing two concurrent main.py invocations
    # (e.g. a scheduled run overlapping a still-running one) from racing.
    RUN_LOCK_PATH = BASE_DIR / "run.lock"
    
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
    GEMINI_MODEL_NAME = "gemini-2.5-flash"
    
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL_NAME = "gpt-4o-mini"

    # NVIDIA NIM — live-verified end-to-end (2026-08-21) with a real authenticated
    # chat.completions call through the production BaseAgent._ask_provider('nvidia_nim',
    # ...) path (see tests/live/test_llm_providers_live.py). Model id and base URL
    # below are exactly what was called and returned a real 200 response ("PONG"),
    # so both are confirmed correct as-is — no change needed.
    #
    # Context window: CONFIRMED — 1,000,000 tokens (native), per NVIDIA's own
    # developer blog: "native 1M-token context window that gives agents long-term
    # memory for aligned, high-accuracy reasoning" —
    # https://developer.nvidia.com/blog/introducing-nemotron-3-super-an-open-hybrid-mamba-transformer-moe-for-agentic-reasoning/
    # (checked live 2026-08-21; note some third-party hosts cap the *usable* window
    # lower for their own VRAM/deployment reasons — this 1M figure is NVIDIA's own
    # stated native limit for the model, which is what applies on NVIDIA's own
    # build.nvidia.com-hosted NIM endpoint used here).
    #
    # Rate limits: NOT CONFIRMED — the live response above returned no
    # x-ratelimit-*/rate-limit-shaped headers at all (inspected the raw HTTP
    # response via the OpenAI SDK's `.with_raw_response`, not just the parsed
    # object: only access-control-expose-headers, connection, content-type, date,
    # nvcf-reqid, nvcf-status, transfer-encoding, vary were present — no quota/
    # remaining/reset headers). NVIDIA has no official published numeric free-tier
    # RPM limit for build.nvidia.com; an NVIDIA forum moderator (not NVIDIA's own
    # documentation) states only that the limit "is dependent on model, use-case
    # and the amount of current overall traffic" with no fixed number
    # (https://forums.developer.nvidia.com/t/clarity-on-nim-api-free-tier-rate-limit-increases/369624,
    # checked live 2026-08-21). A commonly-cited unofficial community baseline is
    # ~40 requests/minute per API key across all models, but this is NOT an
    # NVIDIA-documented SLA and was not observed in this session's response
    # headers — do not hardcode a numeric NVIDIA_NIM_RATE_LIMIT_RPM from this until
    # an authoritative number is obtained (e.g. from the account dashboard at
    # build.nvidia.com, which was not checked this session).
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