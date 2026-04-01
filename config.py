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
    RESEARCHERS_MAP_PATH = BASE_DIR / "researchers_map.json"
    SCHOLAR_STATE_PATH = BASE_DIR / "scholar_state.json"
    OVERLEAF_STATE_PATH = BASE_DIR / "overleaf_state.json"
    
    # ==========================================
    # 3. LLM Configuration (Groq)
    # ==========================================
    GROQ_API_KEY = os.getenv("GROQ_API_KEY")
    LLM_MODEL_NAME = "llama-3.3-70b-versatile"
    LLM_MAX_RETRIES = 3
    LLM_TIMEOUT_SECONDS = 30
    
    # ==========================================
    # 4. API & Network Configuration
    # ==========================================
    SEMANTIC_SCHOLAR_RATE_LIMIT = 100  # Requests per 5 minutes
    PLAYWRIGHT_TIMEOUT_MS = 30000      # 30 seconds in milliseconds
    
    # ==========================================
    # 5. Email Configuration
    # ==========================================
    # The GMAIL account that physically SENDS the email
    NOTIFICATION_SENDER_EMAIL = os.getenv("NOTIFICATION_SENDER_EMAIL")
    NOTIFICATION_SENDER_PASSWORD = os.getenv("NOTIFICATION_SENDER_PASSWORD")
    
    # The UNIVERSITY account that RECEIVES the email (default fallback)
    OVERLEAF_EMAIL = os.getenv("OVERLEAF_EMAIL")
    
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