import os
import time
import random
import threading
import logging
import logging.handlers
from abc import ABC, abstractmethod
from groq import Groq
from google import genai
from openai import OpenAI

# Import the centralized configuration
from config import Config
from utils.database_manager import DatabaseManager


class _CerebrasProactiveRateLimitError(Exception):
    """Raised when the client-side _SlidingWindowLimiter proactively rejects a
    Cerebras call BEFORE any real network request is made. Deliberately a distinct
    type from a real provider-returned 429/rate-limit error: this event carries no
    information about Cerebras's actual server-side state (Cerebras never saw the
    request), so it must not be treated the same as a genuine rate-limit response
    from ask_llm()'s classification logic. See the cerebras branch of _ask_provider
    and the isinstance check near the top of the except block in ask_llm()."""
    pass


class _SlidingWindowLimiter:
    """Thread-safe proactive rate limiter — narrowly scoped to a single provider's
    live-verified request-per-minute ceiling. This is NOT the system-wide token-bucket
    rate limiter the original stability-hardening plan considered and rejected as
    over-engineered; it exists only because LiteratureResearchAgent runs concurrent
    per-project LLM calls via ThreadPoolExecutor, and a burst of even a handful of
    simultaneous fallbacks to a 5-requests/minute provider would blow through that
    ceiling on the very first burst under real concurrent load, not as a rare edge
    case. See Cerebras usage in BaseAgent for the specific justification.

    IMPORTANT — PROCESS-SCOPED ONLY, NOT CROSS-PROCESS: this window lives entirely
    in this process's memory (a plain list guarded by a threading.Lock), the same as
    _provider_cooldowns was before this task added SQLite-backed persistence for it.
    Unlike _provider_cooldowns, this limiter's state is deliberately NOT persisted —
    it protects a fast-moving per-minute window, not a slow cooldown, so cross-process
    sharing would need a shared, low-latency, atomically-updated store (e.g. a DB row
    updated per request), which is a meaningfully bigger piece of work than the
    cooldown table. Today, with one long-lived process handling every project in a
    run, one shared in-memory window is correct and sufficient. If/when this migrates
    to per-project-per-agent containers (the same future architecture the cooldown-
    persistence work in this task was built to survive), each container would get its
    OWN independent window — N concurrent containers could then send up to
    N * max_requests per minute against Cerebras's real ceiling, silently defeating
    this guard. That migration must not assume this limiter already handles it; it
    will need to move to a shared store (the llm_provider_cooldowns table's pattern is
    a reasonable model) before per-container deployment goes live with Cerebras in the
    waterfall."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps = []
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """Non-blocking: returns True and reserves a slot if under the limit, False
        otherwise. Never blocks the caller — a rejection is meant to be surfaced as a
        rate-limit-shaped error so the normal waterfall/cooldown logic handles it,
        rather than stalling a worker thread."""
        now = time.time()
        with self._lock:
            cutoff = now - self.window_seconds
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) >= self.max_requests:
                return False
            self._timestamps.append(now)
            return True

    def seconds_until_slot_free(self) -> float:
        """How long until the oldest request in the current window ages out and a
        slot frees up (0.0 if a slot is already free). Used to size a short,
        proportionate local cooldown for a proactive client-side rejection — unlike
        a real provider 429, there is no external signal to wait out; the window
        itself IS the wait."""
        now = time.time()
        with self._lock:
            cutoff = now - self.window_seconds
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            if len(self._timestamps) < self.max_requests:
                return 0.0
            oldest = min(self._timestamps)
            return max(0.0, oldest + self.window_seconds - now)


class BaseAgent(ABC):
    """
    Abstract base class for all agents in the project.
    Provides common functionality like logging and LLM integration.
    Features a Multi-LLM Waterfall strategy
    (Groq -> Gemini -> Cerebras -> NVIDIA NIM -> OpenAI) with built-in retries.
    """

    # Class-level (shared across every agent instance/subclass in this process) so that
    # once one agent discovers a provider is rate-limited, every other agent in the same
    # pipeline run skips it too instead of independently re-discovering the same 429.
    # {provider_name: unix_timestamp_when_cooldown_ends}
    _provider_cooldowns: dict = {}
    _cooldown_lock = threading.Lock()

    # Lazily-created, class-level (shared for the life of the process, same rationale as
    # _provider_cooldowns above) DatabaseManager used to persist cooldowns across cold
    # starts. Tests reset this to None between runs (see tests/conftest.py) so they never
    # touch a real on-disk database.
    _shared_db_manager = None
    _db_manager_lock = threading.Lock()

    # Cerebras-specific proactive concurrency guard — see _SlidingWindowLimiter and the
    # Cerebras branch of _ask_provider for the full reasoning. Scoped ONLY to Cerebras;
    # no other provider has a live-verified limit this low.
    # PER-PROCESS ONLY — see the "IMPORTANT" note on _SlidingWindowLimiter above. This
    # single shared instance is correct for today's one-process-per-run architecture;
    # it does NOT hold under a future per-project-container deployment (each container
    # would get its own independent window, so N containers could collectively exceed
    # Cerebras's real 5rpm ceiling by up to Nx). Not persisted to SQLite like
    # _provider_cooldowns — this window would need a shared, low-latency store to be
    # made cross-process-safe, which is out of scope for this task.
    _cerebras_rate_limiter = _SlidingWindowLimiter(
        max_requests=Config.CEREBRAS_SAFE_RPM, window_seconds=60.0
    )

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.logger = self._setup_logger()
        self._setup_llm()
        self._load_persisted_cooldowns()
        # Dedup guard for _alert_waterfall_exhausted: a full LLM waterfall exhaustion
        # is alerted at most once per project per run (not once per call site/paper) so
        # a project that hits the exhausted waterfall on every LLM call in a run
        # doesn't spam the admin inbox with a dozen identical alerts. Instance-level
        # (not class-level, unlike _provider_cooldowns) — each agent instance tracks
        # its own run independently, which is what "once per project per run" means
        # for a specific agent's specific degrade-but-continue path.
        self._waterfall_exhausted_alerted: set = set()
        self.logger.info("Agent '%s' initialized.", self.agent_name)

    def _alert_waterfall_exhausted(self, context: str, project_name: str):
        """Sends exactly one deduplicated admin alert per run per project when the full
        LLM waterfall is exhausted for that project. Closes the silent-degradation gap:
        previously a total waterfall failure fell back to degraded output (whatever this
        call site's caller does on RuntimeError — e.g. keyword-only search, a system-note
        placeholder, or simply giving up on this project for the run) with no signal
        anyone would see. This does not change that degrade-but-continue behavior — a
        full crash is disproportionate to one LLM call's failure — it only makes sure
        someone is told.

        Promoted here from LiteratureResearchAgent (which had this same helper as a
        private, non-shared implementation) once a second and third agent needed the
        identical pattern — see the stability-hardening backlog note this closes.
        Dedup key is project_name alone (not (context, project_name)): a project is
        alerted at most once per run regardless of which LLM call within it exhausted
        the waterfall first, matching the original LiteratureResearchAgent behavior.
        """
        if project_name in self._waterfall_exhausted_alerted:
            return
        self._waterfall_exhausted_alerted.add(project_name)
        notifier = getattr(self, "notifier", None)
        if not notifier:
            return
        try:
            notifier.send_admin_alert(
                subject=f"{self.agent_name} — LLM waterfall exhausted: {project_name}",
                message=(
                    f"The full LLM provider waterfall was exhausted while processing "
                    f"project '{project_name}' ({context}). The agent is continuing "
                    f"with degraded output for this project rather than crashing, but "
                    f"this project's results for this run may be incomplete or "
                    f"degraded.\n\n"
                    f"See {self.agent_name}.log for the full traceback."
                )
            )
        except Exception:
            pass  # do not let alert failure mask the original degraded-output path

    @classmethod
    def _get_db_manager(cls) -> DatabaseManager:
        """Lazily creates (once per process) the shared DatabaseManager used for
        cross-run cooldown persistence.

        Deliberately assigns to BaseAgent explicitly rather than `cls`: a classmethod
        assignment of `cls._shared_db_manager = ...` would, when invoked through a
        subclass (e.g. LiteratureResearchAgent), create a NEW class attribute shadowing
        BaseAgent's on that subclass alone — defeating the "one shared instance across
        every agent in the process" design (the same reason _provider_cooldowns is
        mutated in place via __setitem__ rather than ever reassigned)."""
        with BaseAgent._db_manager_lock:
            if BaseAgent._shared_db_manager is None:
                BaseAgent._shared_db_manager = DatabaseManager()
            return BaseAgent._shared_db_manager

    def _load_persisted_cooldowns(self):
        """Reads any cooldowns persisted by a previous (possibly now-dead) process and
        merges them into the in-process _provider_cooldowns dict. This is what makes the
        circuit breaker survive a cold restart — without it, a fresh container/process
        would silently forget an active rate limit and immediately re-trigger it.
        Best-effort: a DB read failure must not prevent the agent from starting."""
        try:
            persisted = self._get_db_manager().get_all_cooldowns()
        except Exception as e:
            self.logger.warning(
                "Could not load persisted LLM provider cooldowns (cross-run protection "
                "unavailable this run): %s", str(e)
            )
            return
        with self.__class__._cooldown_lock:
            for provider, until in persisted.items():
                if until is None:
                    continue
                current = self.__class__._provider_cooldowns.get(provider, 0.0)
                if until > current:
                    self.__class__._provider_cooldowns[provider] = until

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
        Sets up Groq as Primary, Gemini as Fallback 1, Cerebras as Fallback 2,
        NVIDIA NIM as Fallback 3, and OpenAI as Fallback 4 (last resort).
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

        # 3. Setup Cerebras (Fallback 2) — OpenAI-compatible API, reuses the OpenAI SDK
        # client pointed at Cerebras's base URL rather than a separate SDK dependency.
        self.cerebras_available = False
        cerebras_key = getattr(Config, 'CEREBRAS_API_KEY', None)
        if cerebras_key:
            try:
                self.cerebras_client = OpenAI(api_key=cerebras_key, base_url=Config.CEREBRAS_BASE_URL)
                self.cerebras_available = True
                self.logger.info("Fallback 2 (Cerebras) configured successfully.")
            except Exception as e:
                self.logger.warning("Failed to configure Cerebras Fallback: %s", str(e))

        # 4. Setup NVIDIA NIM (Fallback 3) — also OpenAI-compatible.
        self.nvidia_nim_available = False
        nvidia_nim_key = getattr(Config, 'NVIDIA_NIM_API_KEY', None)
        if nvidia_nim_key:
            try:
                self.nvidia_nim_client = OpenAI(api_key=nvidia_nim_key, base_url=Config.NVIDIA_NIM_BASE_URL)
                self.nvidia_nim_available = True
                self.logger.info("Fallback 3 (NVIDIA NIM) configured successfully.")
            except Exception as e:
                self.logger.warning("Failed to configure NVIDIA NIM Fallback: %s", str(e))

        # 5. Setup OpenAI (Fallback 4 — last resort)
        self.openai_available = False
        openai_key = getattr(Config, 'OPENAI_API_KEY', None)
        if openai_key:
            try:
                self.openai_client = OpenAI(api_key=openai_key)
                self.openai_available = True
                self.logger.info("Fallback 4 (OpenAI) configured successfully.")
            except Exception as e:
                self.logger.warning("Failed to configure OpenAI Fallback: %s", str(e))

        # Cache the provider waterfall once so ask_llm() doesn't rebuild it every call.
        # Order: Groq (primary) -> Gemini -> Cerebras -> NVIDIA NIM -> OpenAI (last
        # resort, kept intact despite currently failing with insufficient_quota — an
        # external billing issue, not a code defect). NVIDIA NIM's context window
        # could not be live-verified (no API key available this session), so there is
        # no confirmed basis to place it ahead of Cerebras for long-manuscript calls;
        # it stays after Cerebras until that number is confirmed.
        self._providers_waterfall = ["groq"]
        if self.gemini_available:
            self._providers_waterfall.append("gemini")
        if self.cerebras_available:
            self._providers_waterfall.append("cerebras")
        if self.nvidia_nim_available:
            self._providers_waterfall.append("nvidia_nim")
        if self.openai_available:
            self._providers_waterfall.append("openai")

    def _ask_provider(self, provider_name: str, prompt: str, model_override: str = None) -> str:
        """
        Adapter method to format and send the request according to the specific provider's SDK.
        model_override, when given, is only honored for Groq (the primary provider) — it
        exists so lightweight extraction calls (keyword generation, relevance filtering)
        can use a cheaper/faster model while synthesis calls keep the configured default.
        Gemini, Cerebras, NVIDIA NIM, and OpenAI are all fallbacks, not the primary
        cost driver, so each of them always uses its own single configured model
        (Config.GEMINI_MODEL_NAME / CEREBRAS_MODEL_NAME / NVIDIA_NIM_MODEL_NAME /
        OPENAI_MODEL_NAME) regardless of model_override — the parameter is silently
        ignored for all four, exactly as it always was for Gemini/OpenAI before
        Cerebras and NVIDIA NIM were added to the waterfall.
        """
        if provider_name == "groq":
            chat_completion = self.groq_client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model=model_override or Config.LLM_MODEL_NAME,
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
            
        elif provider_name == "cerebras":
            if not getattr(self, 'cerebras_available', False):
                raise ValueError("Cerebras is not configured.")
            # Proactive, narrowly-scoped guard against Cerebras's live-verified 5
            # requests/minute ceiling (see _SlidingWindowLimiter). A rejection here
            # raises a distinct _CerebrasProactiveRateLimitError (NOT a generic
            # 429-shaped Exception) so ask_llm() can route it through a short,
            # non-persisted local cooldown instead of the full persisted
            # LLM_RATE_LIMIT_COOLDOWN_SECONDS used for real provider 429s — see the
            # isinstance check near the top of the except block in ask_llm().
            if not self._cerebras_rate_limiter.try_acquire():
                raise _CerebrasProactiveRateLimitError(
                    "429 - Cerebras client-side rate limit exceeded "
                    f"(proactive guard, {Config.CEREBRAS_SAFE_RPM} rpm safety ceiling "
                    f"below the live {Config.CEREBRAS_RATE_LIMIT_RPM} rpm limit)"
                )
            response = self.cerebras_client.chat.completions.create(
                model=Config.CEREBRAS_MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                timeout=Config.LLM_TIMEOUT_SECONDS
            )
            return response.choices[0].message.content

        elif provider_name == "nvidia_nim":
            if not getattr(self, 'nvidia_nim_available', False):
                raise ValueError("NVIDIA NIM is not configured.")
            response = self.nvidia_nim_client.chat.completions.create(
                model=Config.NVIDIA_NIM_MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                timeout=Config.LLM_TIMEOUT_SECONDS
            )
            return response.choices[0].message.content

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

    @staticmethod
    def _is_rate_limit_error(err_str: str) -> bool:
        """Recognizes rate-limit signals across all five waterfall providers'
        differently-shaped error strings (HTTP 429, provider-specific quota wording).
        Groq, Gemini, and OpenAI have their own distinct wordings; Cerebras and
        NVIDIA NIM go through the OpenAI SDK (OpenAI-compatible APIs) so their errors
        are typically OpenAI-shaped, but the marker list below is deliberately
        provider-agnostic rather than enumerating each SDK's exact format."""
        lowered = err_str.lower()
        return any(marker in lowered for marker in (
            "429", "rate limit", "rate_limit", "quota", "resource_exhausted", "too many requests"
        ))

    @classmethod
    def _provider_cooldown_remaining(cls, provider: str) -> float:
        """Seconds left before *provider* is worth trying again (0 if available now)."""
        with cls._cooldown_lock:
            until = cls._provider_cooldowns.get(provider, 0.0)
        return max(0.0, until - time.time())

    @classmethod
    def _start_provider_cooldown(cls, provider: str, seconds: float = None, persist: bool = True) -> float:
        """Marks *provider* as rate-limited for `seconds` (default: Config value).
        Shared across all agents in this process, so a 429 discovered by one agent
        immediately protects every other agent's waterfall for the rest of the run.

        persist=False skips the cross-run SQLite write (see the call below): this is
        for cooldowns that carry no information about the provider's actual
        server-side state — e.g. a Cerebras proactive client-side rejection, where no
        real request ever reached Cerebras — and so must NOT be inherited by a fresh
        cold-started process. Real 429/413-rate-shaped responses from an actual
        provider call must keep persist=True (the default) so cold starts still
        benefit from cross-run protection, unchanged from before this parameter
        existed."""
        seconds = Config.LLM_RATE_LIMIT_COOLDOWN_SECONDS if seconds is None else seconds
        until = time.time() + seconds
        with cls._cooldown_lock:
            cls._provider_cooldowns[provider] = until
        if persist:
            # Persist so a cold-started process (e.g. a future per-project container)
            # doesn't rediscover the same rate limit from scratch. Best-effort: a DB
            # write failure must not break the in-process cooldown that already
            # protects this run.
            try:
                cls._get_db_manager().set_cooldown(provider, until)
            except Exception:
                pass
        return seconds

    def ask_llm(self, prompt: str, model_override: str = None) -> str:
        """
        Sends a prompt using a Multi-LLM Waterfall strategy.
        Attempts Primary (Groq). Fails over to Fallbacks (Gemini -> Cerebras ->
        NVIDIA NIM -> OpenAI) if available.
        A provider that returns a rate-limit error is put into a shared cooldown and
        skipped (by this agent and every other agent in the process) until it expires,
        instead of being retried with generic backoff or re-discovered independently by
        every subsequent LLM call.

        model_override lets a caller use a cheaper/faster Groq model for lightweight
        extraction work (see Config.LLM_EXTRACTION_MODEL_NAME) while synthesis calls
        keep the default. Ignored for the Gemini, Cerebras, NVIDIA NIM, and OpenAI
        fallbacks — each of those always uses its own single configured model.
        """
        max_retries = Config.LLM_MAX_RETRIES
        skipped_on_cooldown = []

        for provider in self._providers_waterfall:
            cooldown_remaining = self._provider_cooldown_remaining(provider)
            if cooldown_remaining > 0:
                self.logger.warning(
                    "[%s] Skipping — rate-limit cooldown active for %.0fs more.",
                    provider.upper(), cooldown_remaining
                )
                skipped_on_cooldown.append(provider)
                continue

            self.logger.info("Routing request to LLM provider: [%s]", provider.upper())

            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        self.logger.info("Retrying [%s] (Attempt %d/%d)...", provider.upper(), attempt + 1, max_retries)

                    content = self._ask_provider(provider, prompt, model_override=model_override)

                    # --- Defensive Checks ---
                    if content is None or not content.strip():
                        raise ValueError(f"{provider.upper()} returned an empty or None response.")
                    if content.strip().startswith("Error:"):
                        raise ValueError(f"{provider.upper()} hallucinated an error string: {content}")

                    return content

                except Exception as e:
                    self.logger.warning("[%s] API call failed on attempt %d: %s", provider.upper(), attempt + 1, str(e))
                    err_str = str(e)

                    if isinstance(e, _CerebrasProactiveRateLimitError):
                        # Client-side-only rejection — no real request reached
                        # Cerebras, so the full persisted LLM_RATE_LIMIT_COOLDOWN_SECONDS
                        # would over-penalize (e.g. cutting Cerebras's effective
                        # throughput from ~4rpm to ~2.7rpm) and would wrongly persist a
                        # "provider is rate-limited" fact that a fresh cold-started
                        # process should NOT inherit, since nothing actually happened on
                        # Cerebras's side. Instead: cooldown only for the remainder of
                        # the current sliding window (capped at 60s) and never persisted
                        # to SQLite — this is purely local in-process pacing.
                        short_cooldown = min(self._cerebras_rate_limiter.seconds_until_slot_free(), 60.0)
                        cooldown = self._start_provider_cooldown(provider, seconds=short_cooldown, persist=False)
                        self.logger.warning(
                            "[%s] Proactive client-side rate limit guard triggered (no real "
                            "request was sent) — entering short local-only %.0fs cooldown "
                            "(not persisted), skipping remaining retries for this provider.",
                            provider.upper(), cooldown
                        )
                        break

                    if self._is_rate_limit_error(err_str):
                        cooldown = self._start_provider_cooldown(provider)
                        self.logger.error(
                            "[%s] Rate limit detected — entering %.0fs cooldown, skipping remaining retries for this provider.",
                            provider.upper(), cooldown
                        )
                        break

                    # Permanent errors — retrying won't help, move to next provider immediately.
                    # A 413 is NOT always permanent: Groq's TPM (tokens-per-minute) ceiling
                    # also surfaces as 413, and TPM limits are transient (they clear on
                    # their own) — treating every 413 as permanent meant the very next call
                    # in the same run hit the identical TPM wall and repeated the wasted
                    # attempt. Only a genuinely oversized single request (chunking is the
                    # real fix, not retry) stays classified as permanent; a 413 whose
                    # message names a per-minute/rate ceiling is rate-limit-shaped and
                    # routed through the same cooldown path as a real 429.
                    lowered_err = err_str.lower()
                    is_auth_error = "401" in err_str or "403" in err_str or "invalid_api_key" in err_str or "Incorrect API key" in err_str
                    has_413 = "413" in err_str
                    is_context_length_error = "context_length_exceeded" in lowered_err or "maximum context length" in lowered_err
                    # NOTE: markers are deliberately specific multi-word phrases, not a
                    # bare "rate" substring — "rate" alone false-positives on ordinary
                    # English words like "generate", "accurate", "separate", "moderate"
                    # that show up in genuinely permanent oversized-request messages
                    # (e.g. "request too large to generate a response"). A false match
                    # here would put a healthy provider into a bogus cooldown that then
                    # gets persisted to SQLite, so a cold-started process would honor a
                    # cooldown that was never real.
                    is_rate_shaped_413 = has_413 and not is_context_length_error and any(
                        marker in lowered_err for marker in (
                            "tokens per minute", "tpm", "rate limit", "per minute", "requests per"
                        )
                    )

                    if is_rate_shaped_413:
                        cooldown = self._start_provider_cooldown(provider)
                        self.logger.error(
                            "[%s] 413 is rate-limit-shaped (TPM/rate ceiling) — entering "
                            "%.0fs cooldown, skipping remaining retries for this provider.",
                            provider.upper(), cooldown
                        )
                        break

                    is_permanent_size_error = (
                        is_context_length_error
                        or (has_413 and not is_rate_shaped_413)
                        or ("tokens" in lowered_err and "reduce" in lowered_err)
                    )
                    if is_auth_error or is_permanent_size_error:
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

        if skipped_on_cooldown and len(skipped_on_cooldown) == len(self._providers_waterfall):
            raise RuntimeError(
                f"CRITICAL: All LLM providers are in rate-limit cooldown. "
                f"Tried none live: {', '.join(skipped_on_cooldown)}. Will retry once cooldowns expire."
            )
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