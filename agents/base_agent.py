import os
import time
import logging
import logging.handlers
from abc import ABC, abstractmethod
from groq import Groq

# Import the centralized configuration
from config import Config

class BaseAgent(ABC):
    """
    Abstract base class for all agents in the project.
    Provides common functionality like logging and LLM integration with built-in retries.
    """
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.logger = self._setup_logger()
        self._setup_llm()
        self.logger.info("Agent '%s' initialized.", self.agent_name)

    def _setup_logger(self):
        """
        Configure internal logging to track agent activities.
        Outputs to BOTH the console and a dedicated rotating log file, using Config settings.
        """
        logger = logging.getLogger(self.agent_name)
        logger.setLevel(logging.INFO)
        
        # Prevent adding handlers multiple times if instantiated again
        if not logger.handlers:
            # 1. Console Handler
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            logger.addHandler(ch)
            
            # 2. File Handler (Rotating)
            # Ensure the logs directory exists based on Config
            log_dir = Config.LOGS_DIR
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
                
            # Define the log file path based on the specific agent's name
            log_file = os.path.join(log_dir, f"{self.agent_name}.log")
            
            # Create a RotatingFileHandler using values from Config
            fh = logging.handlers.RotatingFileHandler(
                log_file, 
                maxBytes=Config.MAX_LOG_SIZE_BYTES, 
                backupCount=Config.LOG_BACKUP_COUNT,
                encoding='utf-8'
            )
            fh.setFormatter(formatter)
            logger.addHandler(fh)
            
        return logger

    def _setup_llm(self):
        """
        Initialize the connection to the Groq API using Config variables.
        """
        api_key = Config.GROQ_API_KEY
        if not api_key:
            self.logger.error("GROQ_API_KEY not found in configuration.")
            raise ValueError("API Key is missing. Please check your .env file.")
        
        # Initialize the Groq client
        self.client = Groq(api_key=api_key)
        self.logger.info("LLM connection established successfully using Groq.")

    def ask_llm(self, prompt: str) -> str:
        """
        Sends a prompt to the LLM with defensive programming features:
        - Max retries (to prevent infinite loops)
        - Exponential backoff (to respect rate limits)
        - Strict validation (checking for None or empty strings)
        - Raises actual Exceptions instead of returning error strings.
        """
        max_retries = Config.LLM_MAX_RETRIES
        
        for attempt in range(max_retries):
            try:
                self.logger.info("Sending prompt to LLM (Attempt %d/%d)...", attempt + 1, max_retries)
                
                chat_completion = self.client.chat.completions.create(
                    messages=[
                        {
                            "role": "user",
                            "content": prompt,
                        }
                    ],
                    model=Config.LLM_MODEL_NAME,  
                )
                
                content = chat_completion.choices[0].message.content
                
                # --- Defensive Checks ---
                if content is None or content.strip() == "":
                    raise ValueError("LLM returned an empty or None response.")
                
                if content.strip().startswith("Error:"):
                    raise ValueError(f"LLM hallucinated an error string: {content}")
                
                return content
                
            except Exception as e:
                self.logger.warning("LLM communication failed on attempt %d: %s", attempt + 1, str(e))
                
                if attempt < max_retries - 1:
                    # Exponential backoff: sleep for 1, 2, 4... seconds
                    sleep_time = 2 ** attempt
                    self.logger.info("Retrying in %d seconds...", sleep_time)
                    time.sleep(sleep_time)
                else:
                    self.logger.error("Max retries reached. LLM request failed completely.")
                    # Raise a real exception, forcing the calling agent to handle it
                    raise RuntimeError(f"Failed to communicate with LLM after {max_retries} attempts: {str(e)}") from e

    @abstractmethod
    def run(self):
        """
        The main execution flow of the agent.
        Must be implemented by all subclasses.
        """
        pass