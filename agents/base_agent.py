import os
import time
import logging
import logging.handlers # <--- NEW: For RotatingFileHandler
from abc import ABC, abstractmethod
from dotenv import load_dotenv
from groq import Groq

# Load environment variables
load_dotenv()

class BaseAgent(ABC):
    """
    Abstract base class for all agents in the project.
    Provides common functionality like logging and LLM integration.
    """
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.logger = self._setup_logger()
        self._setup_llm()
        self.logger.info("Agent '%s' initialized.", self.agent_name)

    def _setup_logger(self):
        """
        Configure internal logging to track agent activities.
        Outputs to BOTH the console and a dedicated rotating log file.
        """
        logger = logging.getLogger(self.agent_name)
        logger.setLevel(logging.INFO)
        
        # Prevent adding handlers multiple times if instantiated again
        if not logger.handlers:
            # 1. Console Handler (Keeps terminal output working)
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            logger.addHandler(ch)
            
            # --- NEW: 2. File Handler (Rotating) ---
            # Ensure the logs directory exists
            log_dir = os.path.join(os.getcwd(), "logs")
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
                
            # Define the log file path based on the specific agent's name
            log_file = os.path.join(log_dir, f"{self.agent_name}.log")
            
            # Create a RotatingFileHandler: 
            # maxBytes=5MB, backupCount=3 (keeps the last 3 files, deletes older ones)
            fh = logging.handlers.RotatingFileHandler(
                log_file, 
                maxBytes=5 * 1024 * 1024, # 5 MB per file
                backupCount=3,            # Keep 3 backups (e.g., agent.log.1, agent.log.2)
                encoding='utf-8'          # Crucial for preventing crashes with special chars
            )
            fh.setFormatter(formatter)
            logger.addHandler(fh)
            
        return logger

    def _setup_llm(self):
        """
        Initialize the connection to the Groq API.
        """
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            self.logger.error("GROQ_API_KEY not found in environment variables.")
            raise ValueError("API Key is missing. Please check your .env file.")
        
        # Initialize the Groq client
        self.client = Groq(api_key=api_key)
        self.logger.info("LLM connection established successfully using Groq.")

    def ask_llm(self, prompt: str) -> str:
        """
        A helper method to send a prompt to the LLM and return its response.
        """
        try:
            self.logger.info("Sending prompt to LLM (Groq)...")
            
            # Groq is very fast and has high limits, but a small 1-second sleep is good practice
            time.sleep(1) 
            
            # Groq uses the standard OpenAI-like chat completion structure
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                model="llama-3.3-70b-versatile",  
            )
            
            return chat_completion.choices[0].message.content
            
        except Exception as e:
            self.logger.error("Error communicating with LLM: %s", str(e))
            return f"Error: {str(e)}"

    @abstractmethod
    def run(self):
        """
        The main execution flow of the agent.
        Must be implemented by all subclasses.
        """
        pass