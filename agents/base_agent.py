import os
import logging
from abc import ABC, abstractmethod
from dotenv import load_dotenv
import google.generativeai as genai

# Load environment variables from the .env file
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
        """
        logger = logging.getLogger(self.agent_name)
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            logger.addHandler(ch)
            
        return logger

    def _setup_llm(self):
        """
        Initialize the connection to the Gemini API using the key from .env.
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            self.logger.error("GEMINI_API_KEY not found in environment variables.")
            raise ValueError("API Key is missing. Please check your .env file.")
        
        # Configure the Google Generative AI library with our secure key
        genai.configure(api_key=api_key)
        
        # Initialize the model (using gemini-1.5-flash for fast and cost-effective text generation)
        self.llm = genai.GenerativeModel('gemini-1.5-flash')
        self.logger.info("LLM connection established successfully.")

    def ask_llm(self, prompt: str) -> str:
        """
        A helper method to send a prompt to the LLM and return its response.
        Child classes will use this to generate summaries, feedback, etc.
        """
        try:
            self.logger.info("Sending prompt to LLM...")
            response = self.llm.generate_content(prompt)
            return response.text
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