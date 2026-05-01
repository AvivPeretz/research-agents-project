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
    
    # ==========================================
    # 3. LLM Configuration (Groq)
    # ==========================================
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    LLM_MODEL_NAME = "llama-3.3-70b-versatile"
    
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    GEMINI_MODEL_NAME = "gemini-1.5-flash"
    
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_MODEL_NAME = "gpt-4o-mini"
    
    LLM_MODEL_NAME = "llama-3.3-70b-versatile"
    LLM_MAX_RETRIES = 3
    LLM_TIMEOUT_SECONDS = 30
    
    # ==========================================
    # 4. API & Network Configuration
    # ==========================================
    
    SEMANTIC_SCHOLAR_RATE_LIMIT = 100  # Requests per 5 minutes
    PLAYWRIGHT_TIMEOUT_MS = 30000      # 30 seconds in milliseconds
    OPENALEX_API_KEY: str = os.getenv("OPENALEX_API_KEY", "")
    # Playwright headless mode — set to False only for local debugging
    PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() != "false"

    # ==========================================
    # 5. Email Configuration
    # ==========================================
    # The GMAIL account that physically SENDS the email
    NOTIFICATION_SENDER_EMAIL = os.getenv("NOTIFICATION_SENDER_EMAIL")
    NOTIFICATION_SENDER_PASSWORD = os.getenv("NOTIFICATION_SENDER_PASSWORD")
    
    # The UNIVERSITY account that RECEIVES the email (default fallback)
    OVERLEAF_EMAIL = os.getenv("OVERLEAF_EMAIL")
    # Overleaf account password (used for scraping / automation). May be None in some deployments.
    OVERLEAF_PASSWORD = os.getenv("OVERLEAF_PASSWORD")
    
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
    GARBAGE_COLLECTION_TTL_DAYS = 30

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