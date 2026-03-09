import os
import time
import logging
from abc import ABC, abstractmethod
from dotenv import load_dotenv
from groq import Groq  # The new Groq SDK

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
                model="llama-3.3-70b-versatile",  # You can also try 'mixtral-8x7b-32768'
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