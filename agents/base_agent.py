import logging
from abc import ABC, abstractmethod

class BaseAgent(ABC):
    """
    An abstract parent class for all agents. Each agent inherit from
    it and must implement its methods.
    """
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.logger = self._setup_logger()
        self.logger.info(f"Agent '{self.agent_name}' initialized.")

    def _setup_logger(self):
        """
        Setting up an internal logging system so we can track the agent's actions.
        """
        logger = logging.getLogger(self.agent_name)
        logger.setLevel(logging.INFO)
        
        #Prevents print duplications if multiple agents are created.
        if not logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            logger.addHandler(ch)
            
        return logger

    @abstractmethod
    def run(self):
        """
        The central function that runs the agent.
        Every agent that inherits from the parent class *must* 
        implement this function itself.
        """
        pass