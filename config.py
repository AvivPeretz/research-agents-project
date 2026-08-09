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
    OVERLEAF_STATE_PATH = BASE_DIR / "scholar_state.json"
    OVERLEAF_USER_DATA_DIR: str = str(BASE_DIR / "playwright_state" / "overleaf_profile")
    
    # ==========================================
    # 3. LLM Configuration (Groq)
    # ==========================================
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    LLM_MODEL_NAME = "openai/gpt-oss-120b"
    
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL_NAME = "gemini-1.5-flash"
    
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL_NAME = "gpt-4o-mini"
    
    LLM_MAX_RETRIES = 3
    LLM_TIMEOUT_SECONDS = 30
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
    IMAP_SERVER = "imap.gmail.com"
    IMAP_PORT = 993
    
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