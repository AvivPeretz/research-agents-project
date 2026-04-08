# Academic Research Multi-Agent System 🔬🤖

A Python-based, multi-agent artificial intelligence system designed to automate and enhance
academic research workflows. This project utilizes LLMs (Groq, Gemini, OpenAI), browser
automation (Playwright), strict Object-Oriented Programming (OOP) principles, Defensive
Programming, and Centralized SQLite state management to create an autonomous, decoupled
pipeline that fetches real-time data, conducts targeted literature reviews, tracks writing
progress, enhances manuscripts via Stanford's paperreview.ai, and delivers beautifully
formatted email reports.

---

## 🌟 Features & Architecture

The system is built on a modular, scalable architecture separating data ingestion from LLM
processing. It operates a preliminary web-scraping agent followed by four specialized AI
agents, all utilizing a shared `BaseAgent` class for robust API connectivity and centralized
logging.

A core feature of the system is its **Dual-Email Architecture**: It utilizes an official
University Microsoft 365 account for academic credibility (e.g., retrieving source files and
external submissions) while employing a decoupled Gmail relay account to safely dispatch
internal notifications, successfully bypassing strict institutional SMTP blocks.

---

### Phase 0: Data Ingestion (Delta Sync)
**Data Ingestion Agent:**
* Operates completely autonomously using Microsoft's **Playwright** framework.
* Incorporates **Session Persistence** to securely bypass login screens and CAPTCHA after
  an initial manual authentication.
* Implements a **Delta Sync** mechanism: Scans the researcher's Overleaf dashboard and
  downloads *only* new or recently modified projects (fetching both ZIP source codes and
  compiled PDFs), saving bandwidth and processing time.
* Uses direct endpoint navigation to reliably download archives, immune to front-end UI
  changes.

---

### Phase 1: Global Research & Data Extraction
**Literature Research Agent:**
* Autonomously reads the actual `.tex` manuscript text to dynamically extract highly
  targeted research keywords.
* **Hybrid Search Engine Approach:** Uses the **Semantic Scholar API** as the primary
  source for fast, reliable data (full abstracts, exact citation counts, publication venues).
  If the API fails, it falls back to navigating **Google Scholar** using Playwright.
* **Strict LLM Contracts (Pydantic):** Enforces a rigid JSON schema for all LLM outputs,
  guaranteeing data integrity and completely eliminating hallucinations.
* Automatically populates a **14-Column Rolling CSV Comparison Table** per project and
  generates comprehensive Markdown summaries featuring direct, clickable URLs to the
  discovered papers.
* The agent is context-aware and can accurately identify and categorize
  theoretical-mathematical papers without inventing empirical metrics.

---

### Phase 2: Manuscript Analysis
**Progress Tracking Agent** *(Triggered only on new data):*
* Analyzes the newly ingested plain text extracted from LaTeX files.
* Utilizes a **SQLite Database** to store historical project states and isolate only the
  *delta* (newly added or modified sentences) since the last run, saving LLM tokens.
* Acts as an academic reviewer to provide highly targeted, surgical feedback on the tone,
  structure, and clarity of the day's specific writing additions.

---

### Phase 3: Peer-Review & Innovation
**Research Enhancement Agent** *(Triggered autonomously):*
* Automates a rigorous external peer-review submission process by interfacing with
  Stanford's **paperreview.ai**.
* **Phase 1 (Upload):** Uses Playwright to autonomously upload the PDF manuscript and
  submit the university email address.
* **Phase 2 (Fetch & Analyze):** Operates an IMAP connector to read incoming automated
  emails, utilizes Regex to extract the unique Stanford access token, and uses Playwright
  to scrape the generated peer-review from the web portal.
* Uses the LLM to translate harsh academic critiques into an actionable, supportive To-Do
  list with estimated effort hours and strict deadlines for the research team.

---

### Phase 4: Communication & Alerting
**Notification Agent** *(Event-Driven & Injected):*
* Acts as the system's external communication hub. Completely decoupled from the main
  execution flow, triggered via an **Event-Driven Architecture**.
* Implements **Dependency Injection (DI)**: The Orchestrator (`main.py`) instantiates a
  single shared notification service and injects it into all operational agents, preventing
  tight coupling and redundant connections.
* Uses the central **SQLite Database** to dynamically route specific project feedback to
  the appropriate researcher's inbox.
* Translates raw Markdown reports into beautifully styled, professional HTML layouts
  dynamically branded per agent (e.g., green for literature, purple for project
  management).
* Automatically sends executive summaries via a secure SMTP relay connection, with full
  detailed reports embedded directly in the email body.

---

## 🛠️ Utility Modules & Infrastructure

* **Multi-LLM Waterfall Strategy:** The `BaseAgent` incorporates a dynamic fallback
  mechanism (`Groq → Gemini → OpenAI`). If the primary model encounters errors or rate
  limits, the system automatically routes the request to the next available provider,
  ensuring maximum uptime.

* **Centralized Configuration (`config.py`):** Acts as the Single Source of Truth for the
  entire system. Eliminates "magic numbers" and hardcoded paths by centrally managing all
  environment variables, API limits, UI timeouts, models, and directory structures.
  Includes a `validate()` function operating on a **Fail-Fast** principle — checking all
  required environment variables before execution begins.

* **Single Source of Truth (Database):** Uses `DatabaseManager` backed by SQLite to safely
  manage project routing emails and synchronization states, replacing fragile JSON files.
  Includes built-in, idempotent JSON-to-SQLite migration for legacy `researchers_map.json`
  data.

* **Defensive Programming & Resilience:** Built into the `BaseAgent`, featuring strict
  validation for all LLM inputs and outputs. Implements **Exponential Backoff** for API
  rate limits, secure JSON parsing with graceful fallbacks, and real exception bubbling
  (`RuntimeError`) to prevent silent systemic crashes. The orchestration layer uses
  `run_agent_safely()` to isolate individual agent failures from crashing the entire
  pipeline.

* **Library Manager:** Automates the creation of an organized directory structure and
  handles all file I/O operations to maintain a pristine `research_library` ecosystem,
  including Markdown reports and rolling CSV tables.

* **Overleaf Connector:** Extracts downloaded ZIP files and uses highly optimized Regular
  Expressions (RegEx) to strip heavy LaTeX formatting, delivering clean plain text to the
  LLM agents.

* **Garbage Collector:** Implements a strict Data Retention Policy (TTL). Safely scans
  specific directories to automatically purge outdated Markdown reports older than 30 days,
  preventing disk space exhaustion while preserving critical rolling CSVs and system state
  files.

* **Production Logging System:** Uses Python's `RotatingFileHandler` implemented within the
  `BaseAgent`. Automatically generates distinct `.log` files for each running agent,
  backing them up once they reach 5MB. This ensures infinite system uptime and safe
  monitoring without bloating server storage.

---

## 🚀 Usage & Command Line Interface (CLI)

The system includes a powerful, built-in CLI using `argparse` to allow fine-grained control
over which agents and projects are executed. This is highly useful for development,
debugging, and targeted runs.

### 1. Basic Execution (Run Everything)
By default, running the script will execute **all agents** across **all active projects**
found in the `overleaf_projects/` directory.
```bash
python main.py
```

### 2. Run a Specific Agent *(Great for Debugging)*
To isolate a specific task and save time, use the `--agent` flag.

Available options: `all`, `ingestion`, `literature`, `progress`, `enhancement`, `gc`.

```bash
# Example: Run only the Literature Research Agent
python main.py --agent literature
```

### 3. Target a Specific Project
To execute the entire pipeline for a single project (ignoring all others in the workspace),
use the `--project` flag. Enclose the project name in quotes if it contains spaces.

```bash
python main.py --project "Physics_Lab_1"
```

### 4. The Sweet Spot — Combined Execution
Combine flags to run a specific agent on a specific project. This is the optimal workflow
for targeted updates or testing a specific component.

```bash
# Example: Run only the Progress Tracking Agent for "My Thesis"
python main.py --agent progress --project "My Thesis"
```

### 5. The Cheat Sheet (Help Menu)
```bash
python main.py --help
```

---

## 📁 Project Structure
├── agents/
│   ├── base_agent.py               # Abstract base with Multi-LLM Waterfall & logging
│   ├── literature_research_agent.py
│   ├── progress_tracking_agent.py
│   ├── research_enhancement_agent.py
│   └── notification_agent.py
├── ingestion/
│   └── data_ingestion_agent.py     # Playwright Delta Sync from Overleaf
├── utils/
│   ├── overleaf_connector.py       # LaTeX parser & text cleaner
│   ├── literature_fetcher.py       # Semantic Scholar API + Google Scholar fallback
│   ├── library_manager.py          # File I/O & directory structure
│   ├── database_manager.py         # SQLite Single Source of Truth
│   └── garbage_collector.py        # TTL-based Markdown cleanup
├── domain/
│   └── schemas.py                  # Pydantic contracts for LLM output validation
├── config.py                       # Centralized configuration & env management
├── main.py                         # Orchestrator + CLI (argparse)
├── requirements.txt
├── .env                            # (Not committed) credentials & API keys
└── .gitignore
