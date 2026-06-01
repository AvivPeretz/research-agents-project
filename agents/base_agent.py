import os
import time
import random
import logging
import logging.handlers
from abc import ABC, abstractmethod
from groq import Groq
from google import genai
from openai import OpenAI

# Import the centralized configuration
from config import Config

class BaseAgent(ABC):
    """
    Abstract base class for all agents in the project.
    Provides common functionality like logging and LLM integration.
    Features a Multi-LLM Waterfall strategy (Groq -> Gemini -> OpenAI) with built-in retries.
    """
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.logger = self._setup_logger()
        self._setup_llm()
        self.logger.info("Agent '%s' initialized.", self.agent_name)

    def _setup_logger(self):
        logger = logging.getLogger(self.agent_name)
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            ch = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            ch.setFormatter(formatter)
            logger.addHandler(ch)
            
            log_dir = Config.LOGS_DIR
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
                
            log_file = os.path.join(log_dir, f"{self.agent_name}.log")
            
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
        Initialize connections to LLM providers.
        Sets up Groq as Primary, Gemini as Fallback 1, and OpenAI as Fallback 2.
        """
        # 1. Setup Groq (Primary - MUST EXIST)
        groq_key = Config.GROQ_API_KEY
        if not groq_key:
            self.logger.error("GROQ_API_KEY not found. Primary LLM cannot start.")
            raise ValueError("API Key is missing. Please check your .env file.")
        self.groq_client = Groq(api_key=groq_key)
        self.logger.info("Primary LLM (Groq) configured successfully.")

        # 2. Setup Gemini (Fallback 1)
        self.gemini_available = False
        gemini_key = getattr(Config, 'GEMINI_API_KEY', None)
        if gemini_key:
            try:
                self.gemini_client = genai.Client(api_key=gemini_key)
                self.gemini_model_name = getattr(Config, 'GEMINI_MODEL_NAME', 'gemini-1.5-flash')
                self.gemini_available = True
                self.logger.info("Fallback 1 (Gemini) configured successfully.")
            except Exception as e:
                self.logger.warning("Failed to configure Gemini Fallback: %s", str(e))

        # 3. Setup OpenAI (Fallback 2)
        self.openai_available = False
        openai_key = getattr(Config, 'OPENAI_API_KEY', None)
        if openai_key:
            try:
                self.openai_client = OpenAI(api_key=openai_key)
                self.openai_available = True
                self.logger.info("Fallback 2 (OpenAI) configured successfully.")
            except Exception as e:
                self.logger.warning("Failed to configure OpenAI Fallback: %s", str(e))

        # Cache the provider waterfall once so ask_llm() doesn't rebuild it every call
        self._providers_waterfall = ["groq"]
        if self.gemini_available:
            self._providers_waterfall.append("gemini")
        if self.openai_available:
            self._providers_waterfall.append("openai")

    def _ask_provider(self, provider_name: str, prompt: str) -> str:
        """
        Adapter method to format and send the request according to the specific provider's SDK.
        """
        if provider_name == "groq":
            chat_completion = self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=Config.LLM_MODEL_NAME,
                timeout=Config.LLM_TIMEOUT_SECONDS  
            )
            return chat_completion.choices[0].message.content
            
        elif provider_name == "gemini":
            if not getattr(self, 'gemini_available', False):
                raise ValueError("Gemini is not configured.")
            response = self.gemini_client.models.generate_content(
                model=self.gemini_model_name,
                contents=prompt,
            )
            return response.text
            
        elif provider_name == "openai":
            if not getattr(self, 'openai_available', False):
                raise ValueError("OpenAI is not configured.")
            response = self.openai_client.chat.completions.create(
                model=getattr(Config, 'OPENAI_MODEL_NAME', 'gpt-4o-mini'),
                messages=[{"role": "user", "content": prompt}],
                timeout=Config.LLM_TIMEOUT_SECONDS
            )
            return response.choices[0].message.content
            
        else:
            raise ValueError(f"Unknown LLM provider requested: {provider_name}")

    def ask_llm(self, prompt: str) -> str:
        """
        Sends a prompt using a Multi-LLM Waterfall strategy.
        Attempts Primary (Groq). Fails over to Fallbacks (Gemini -> OpenAI) if available.
        """
        max_retries = Config.LLM_MAX_RETRIES

        for provider in self._providers_waterfall:
            self.logger.info("Routing request to LLM provider: [%s]", provider.upper())
            
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        self.logger.info("Retrying [%s] (Attempt %d/%d)...", provider.upper(), attempt + 1, max_retries)
                    
                    content = self._ask_provider(provider, prompt)
                    
                    # --- Defensive Checks ---
                    if content is None or not content.strip():
                        raise ValueError(f"{provider.upper()} returned an empty or None response.")
                    if content.strip().startswith("Error:"):
                        raise ValueError(f"{provider.upper()} hallucinated an error string: {content}")
                    
                    return content
                    
                except Exception as e:
                    self.logger.warning("[%s] API call failed on attempt %d: %s", provider.upper(), attempt + 1, str(e))
                    # Permanent errors — retrying won't help, move to next provider immediately
                    err_str = str(e)
                    is_auth_error = "401" in err_str or "403" in err_str or "invalid_api_key" in err_str or "Incorrect API key" in err_str
                    is_size_error = "413" in err_str or "context_length_exceeded" in err_str or ("tokens" in err_str and "reduce" in err_str)
                    if is_auth_error or is_size_error:
                        self.logger.error("[%s] Permanent error (auth/size) — skipping retries for this provider.", provider.upper())
                        break
                    if attempt < max_retries - 1:
                        sleep_time = 2 ** (attempt + 1) + random.uniform(0, 1)
                        time.sleep(sleep_time)
                    else:
                        self.logger.error("Max retries reached for provider [%s]. Exhausted.", provider.upper())
                        break
                        
            if provider != self._providers_waterfall[-1]:
                self.logger.warning("Initiating LLM Fallback: Switching from [%s] to next provider...", provider.upper())
            else:
                self.logger.error("All available LLM providers have been exhausted.")

        raise RuntimeError(f"CRITICAL: All LLM providers exhausted. Tried: {', '.join(self._providers_waterfall)}. Check API keys and rate limits.")

    def ask_llm_json(self, prompt: str) -> dict:
        """
        Calls ask_llm and returns a parsed dict. Strips markdown code fences
        (```json ... ```) before parsing. Retries the LLM once on JSONDecodeError.
        Raises ValueError if JSON cannot be parsed after retry.
        """
        import json
        import re

        def _extract_and_parse(text: str) -> dict:
            # Strip markdown code fences
            clean = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
            clean = re.sub(r'\s*```$', '', clean.strip(), flags=re.MULTILINE)
            # Find outermost JSON object or array
            for start_char, end_char in [('{', '}'), ('[', ']')]:
                start = clean.find(start_char)
                end = clean.rfind(end_char) + 1
                if start != -1 and end > start:
                    return json.loads(clean[start:end])
            raise json.JSONDecodeError("No JSON object found", clean, 0)

        response = self.ask_llm(prompt)
        try:
            return _extract_and_parse(response)
        except json.JSONDecodeError as e:
            self.logger.warning("JSON parse failed on first attempt (%s). Retrying with cleaner prompt.", str(e))
            retry_prompt = prompt + "\n\nIMPORTANT: Return ONLY valid JSON. No markdown. No explanations. Start with { or [."
            response2 = self.ask_llm(retry_prompt)
            try:
                return _extract_and_parse(response2)
            except json.JSONDecodeError as e2:
                self.logger.error("JSON parse failed after retry: %s", str(e2))
                raise ValueError(f"LLM did not return valid JSON after retry: {str(e2)}") from e2

    @abstractmethod
    def run(self):
        pass