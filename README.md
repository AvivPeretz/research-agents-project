# Academic Research Multi-Agent System 🔬🤖

A Python-based, multi-agent artificial intelligence system designed to automate and enhance
academic research workflows. This project utilizes LLMs (Groq, Gemini, OpenAI), browser
automation (Playwright), strict Object-Oriented Programming (OOP) principles, Defensive
Programming, and Centralized SQLite state management to create an autonomous, decoupled
pipeline that fetches real-time data, conducts targeted literature reviews, tracks writing
progress, enhances manuscripts via Stanford's paperreview.ai, and delivers beautifully
formatted email reports.

---

## 📋 Table of Contents

- [Features & Architecture](#-features--architecture)
- [Project Structure](#-project-structure)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
  - [Option A — Automated Setup](#-option-a--automated-setup-recommended)
  - [Option B — Manual Setup](#-option-b--manual-setup)
- [Configuration](#-configuration)
- [Usage & CLI](#-usage--command-line-interface-cli)
- [Agent Reference](#-agent-reference)
  - [First-Time Overleaf Login](#first-time-setup-overleaf-login)
  - [Stanford Email Forwarding Rule](#setting-up-the-stanford-review-email-forwarding-rule)
  - [Registering Projects](#registering-a-project-and-researcher-email)
- [Troubleshooting](#-troubleshooting)
- [Roadmap](#-roadmap)

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

## 📁 Project Structure

```
research-agents/
├── agents/
│   ├── __init__.py
│   ├── base_agent.py               # Abstract base class: logging, Multi-LLM waterfall
│   ├── literature_research_agent.py
│   ├── progress_tracking_agent.py
│   ├── research_enhancement_agent.py
│   ├── supervisor_status_agent.py
│   └── notification_agent.py
├── ingestion/
│   └── data_ingestion_agent.py     # Overleaf Delta Sync via Playwright
├── domain/
│   └── schemas.py                  # Pydantic contracts for all LLM outputs
├── utils/
│   ├── __init__.py
│   ├── database_manager.py         # SQLite single source of truth
│   ├── library_manager.py          # File I/O: Markdown, CSV, directories
│   ├── literature_fetcher.py       # Semantic Scholar API + Google Scholar fallback
│   ├── overleaf_connector.py       # LaTeX → plain text via RegEx
│   └── garbage_collector.py        # TTL-based .md file cleanup
├── research_library/               # Generated output (gitignored)
│   ├── literature_reviews/
│   ├── project_tracking/
│   ├── project_enhancement/
│   ├── comparison_tables/
│   └── system.db                   # SQLite database
├── overleaf_projects/              # Downloaded .tex + PDF files (gitignored)
├── logs/                           # Rotating log files (gitignored)
├── config.py                       # Centralized configuration class
├── main.py                         # Orchestrator + argparse CLI
├── requirements.txt
├── .env.example                    # Template — copy to .env and fill in
└── .gitignore
```

---

## ✅ Prerequisites

Before installing, make sure the following are available on your machine:

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | [python.org](https://www.python.org/downloads/) |
| pip | Latest | Bundled with Python |
| Git | Any | For cloning the repository |
| Google Chrome | Latest | Used by Playwright for browser automation |
| A Groq API Key | Free | [console.groq.com](https://console.groq.com) — **required** |
| A Gmail Account | Any | Used as the SMTP relay for sending emails |
| Gmail App Password | — | See [Configuration](#-configuration) section below |
| An Overleaf Account | Free/Pro | Your university Overleaf account |

> **Note:** Gemini and OpenAI API keys are **optional**. They are used as automatic LLM
> fallbacks if Groq is unavailable. The system will run correctly with only Groq configured.

---

## 🚀 Installation

Start by cloning the repository — this is required for both installation methods below.

```bash
git clone https://github.com/<your-username>/research-agents.git
cd research-agents
```

Choose one of the two installation methods:

---

### ⚡ Option A — Automated Setup (Recommended)

A single script handles everything: virtual environment creation, dependency installation,
Playwright browser download, and `.env` file creation.

```bash
bash setup.sh
```

The script will print clear progress for each step and tell you exactly what to do next.
Once it finishes, come back here and continue with the two remaining steps below.

> **Windows users:** Run the script inside **Git Bash** (included with Git for Windows),
> not PowerShell or Command Prompt.

#### Step A1 — Fill In Your Credentials

Open the `.env` file that was just created and fill in your values.
See the [Configuration](#-configuration) section below for a full explanation of each field.

```bash
nano .env   # or open in any text editor
```

#### Step A2 — Register Your Project and Email

Create a file named `researchers_map.json` in the project root. This tells the system
which Overleaf project belongs to which researcher, so email notifications are routed
correctly.

```json
{
  "Your_Overleaf_Project_Name": "your.name@university.edu"
}
```

> The project name must match **exactly** the name shown on your Overleaf dashboard
> (case-sensitive, spaces included). On the first run, this file is automatically
> imported into the database. It is only needed once.

---

### 🔧 Option B — Manual Setup

Follow these steps if you prefer full control over the installation process.

#### Step 1 — Create a Virtual Environment

```bash
# Create the environment
python -m venv venv

# Activate it — macOS / Linux
source venv/bin/activate

# Activate it — Windows (PowerShell)
.\venv\Scripts\Activate.ps1
```

> **Important:** Always activate the virtual environment before running any project command.
> You will need to re-activate it every time you open a new terminal session.

#### Step 2 — Install Python Dependencies

```bash
pip install -r requirements.txt
```

#### Step 3 — Install Playwright Browser

Playwright requires a one-time download of the Chromium browser engine used for
Overleaf automation and web scraping.

```bash
playwright install chromium
```

> If you encounter permission errors on Linux, run:
> `playwright install --with-deps chromium`

#### Step 4 — Create Your `.env` File

```bash
cp .env.example .env
```

Then open `.env` in any text editor and fill in your credentials.
See the [Configuration](#-configuration) section below for a full explanation of each field.

#### Step 5 — Register Your Project and Email

Create a file named `researchers_map.json` in the project root. This tells the system
which Overleaf project belongs to which researcher, so email notifications are routed
correctly.

```json
{
  "Your_Overleaf_Project_Name": "your.name@university.edu"
}
```

> The project name must match **exactly** the name shown on your Overleaf dashboard
> (case-sensitive, spaces included). On the first run, this file is automatically
> imported into the database. It is only needed once.

#### Step 6 — Verify the Installation

```bash
python main.py --agent literature --project "Your_Overleaf_Project_Name"
```

If you see log output and no `ValueError` on startup, the installation is successful.

---

## ⚙️ Configuration

All credentials and settings are managed through a `.env` file in the project root.
**Never commit this file to Git.** It is already listed in `.gitignore`.

### Creating Your `.env` File

```bash
cp .env.example .env
```

### Variables Reference

```dotenv
# ============================================================
# LLM PROVIDERS
# ============================================================

# [REQUIRED] Primary LLM — Free tier at console.groq.com
GROQ_API_KEY=gsk_...

# [OPTIONAL] Fallback LLM #1 — Free tier at aistudio.google.com
GEMINI_API_KEY=AIza...

# [OPTIONAL] Fallback LLM #2 — Paid, at platform.openai.com
OPENAI_API_KEY=sk-...


# ============================================================
# EMAIL — GMAIL RELAY (Sender Account)
# ============================================================
# This is a Gmail account used ONLY for sending notification emails.
# It does NOT need to be your university email.
# Recommendation: create a dedicated dummy Gmail account for this.

NOTIFICATION_SENDER_EMAIL=your-relay@gmail.com
NOTIFICATION_SENDER_PASSWORD=xxxx xxxx xxxx xxxx
# ↑ This is a Gmail App Password (16 chars with spaces), NOT your Gmail login password.
# To generate one: Google Account → Security → 2-Step Verification → App Passwords


# ============================================================
# OVERLEAF / UNIVERSITY ACCOUNT (Receiver Account)
# ============================================================
# This is your university email connected to Overleaf.
# Used for: logging into Overleaf and receiving Stanford review tokens.

OVERLEAF_EMAIL=your.name@university.edu
OVERLEAF_PASSWORD=your-overleaf-password
```

### How to Generate a Gmail App Password

1. Go to your [Google Account](https://myaccount.google.com)
2. Navigate to **Security** → **2-Step Verification** (must be enabled)
3. Scroll down to **App Passwords**
4. Select app: **Mail**, device: **Other** → type "ResearchAgents"
5. Copy the 16-character password into `NOTIFICATION_SENDER_PASSWORD`

> **Important:** Use the App Password exactly as shown, including spaces.

---

## 🖥️ Usage & Command Line Interface (CLI)

The system includes a powerful built-in CLI using `argparse` for fine-grained control
over which agents and projects are executed.

### 1. Basic Execution — Run Everything

Runs all agents across all active projects found in the `overleaf_projects/` directory.

```bash
python main.py
```

> On the **first run**, the Data Ingestion Agent will open a visible browser window and
> ask you to log into Overleaf manually (to handle reCAPTCHA). After a successful login,
> the session is saved and all subsequent runs are fully automated.

---

### 2. Run a Specific Agent

Use the `--agent` flag to isolate a single phase. Useful during development and testing.

| Value | Description |
|---|---|
| `all` | Run the full pipeline *(default)* |
| `ingestion` | Phase 0 — Download new/modified Overleaf projects |
| `literature` | Phase 1 — Fetch and summarize related papers |
| `progress` | Phase 2 — Analyze manuscript delta and provide feedback |
| `enhancement` | Phase 3 — Upload to Stanford peer-review and collect results |
| `gc` | Garbage collector — delete Markdown files older than 30 days |

```bash
# Run only the Literature Research Agent
python main.py --agent literature

# Run only the Garbage Collector
python main.py --agent gc
```

---

### 3. Target a Specific Project

Use `--project` to process a single project, ignoring all others.
Enclose the project name in quotes if it contains spaces.

```bash
python main.py --project "My_Thesis"
```

---

### 4. Combined Execution — The Recommended Workflow

Combine `--agent` and `--project` for precise, targeted runs.
This is the optimal pattern during development and debugging.

```bash
# Run only the Progress Tracking Agent for a specific project
python main.py --agent progress --project "My_Thesis"

# Run only the Literature Agent for a specific project
python main.py --agent literature --project "Physics_Lab_1"

# Run the full pipeline on one project only
python main.py --project "Physics_Lab_1"
```

---

### 5. Help Menu

```bash
python main.py --help
```

---

## 🤖 Agent Reference

### First-Time Setup: Overleaf Login

The `DataIngestionAgent` requires a one-time manual login to Overleaf to create a persistent
browser session. This only needs to be done once (or when the session expires):

```bash
python main.py --agent ingestion
```

A browser window will open automatically. Log in with your Overleaf credentials and solve
the reCAPTCHA if prompted. Once you reach the Overleaf dashboard, the session is saved
to `overleaf_state.json` (gitignored) and the browser will close. All future runs will
use the saved session silently.

---

### Setting Up the Stanford Review Email Forwarding Rule

The Research Enhancement Agent (`--agent enhancement`) relies on a **two-account email
architecture** to retrieve peer-review results from Stanford's `paperreview.ai`:

1. The system submits the manuscript PDF to Stanford along with your **university email address**.
2. Stanford sends the access token **only to that university email** (institutional emails only).
3. The agent polls your **Gmail account** via IMAP to extract the token and fetch the review.

For step 3 to work, you must configure an **automatic forwarding rule** on your university
inbox that forwards Stanford's emails to your Gmail relay account. This is a one-time manual
setup.

#### Instructions for Microsoft 365 (Outlook Web)

1. Log in to your university email at [outlook.office.com](https://outlook.office.com)
2. Click the **Settings** gear icon (top-right) → **View all Outlook settings**
3. Navigate to **Mail** → **Rules**
4. Click **Add new rule** and configure it as follows:

| Field | Value |
|---|---|
| Rule name | `Forward Stanford Review to Gmail` |
| Condition | **From** contains `paperreview.ai` |
| Action | **Forward to** → `your-relay@gmail.com` |

5. Click **Save**. The rule is now active and requires no further maintenance.

> **Why is this necessary?** Stanford's `paperreview.ai` only sends review access tokens
> to institutional (non-Gmail) email addresses. The university's Microsoft 365 environment
> blocks automated IMAP access from external scripts. The forwarding rule bridges both
> constraints: Stanford's email lands in the university inbox and is immediately forwarded
> to the Gmail account where the agent can read it programmatically.

> **Security note:** The Gmail account receiving these forwarded emails should be the
> same account configured in `NOTIFICATION_SENDER_EMAIL` in your `.env` file.

---

### Registering a Project and Researcher Email

For the system to correctly route email notifications to the right researcher, each
project must be associated with an email address in the SQLite database.

Create or edit `researchers_map.json` in the project root using this format:

```json
{
  "My_Thesis": "student.name@university.edu",
  "Physics_Lab_1": "another.student@university.edu"
}
```

On the next run, `main.py` will automatically migrate this data into the database.
This step only needs to be done once per project. After migration, you can delete the
JSON file — the database is the live source of truth.

---

### Checking Logs

Each agent writes to its own rotating log file inside the `logs/` directory:

```
logs/
├── LiteratureResearchAgent.log
├── ProgressTrackingAgent.log
├── ResearchEnhancementAgent.log
├── NotificationAgent.log
└── DatabaseManager.log
```

Log files are automatically rotated at 5MB and up to 3 backups are kept per agent.

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

## 🔧 Troubleshooting

### `ValueError: Missing required environment variables`
Your `.env` file is missing one or more required keys. Open `.env` and verify that
`GROQ_API_KEY`, `NOTIFICATION_SENDER_EMAIL`, `NOTIFICATION_SENDER_PASSWORD`,
`OVERLEAF_EMAIL`, and `OVERLEAF_PASSWORD` are all filled in.

### `playwright._impl._errors.Error: Executable doesn't exist`
You skipped the Playwright browser installation step. Run:
```bash
playwright install chromium
```

### `SMTPAuthenticationError` when sending emails
Your Gmail App Password is incorrect or 2-Step Verification is not enabled on the
sending Gmail account. Regenerate the App Password from your Google Account settings.

### The browser opens but the login times out
You have 90 seconds to complete the Overleaf login manually. If the reCAPTCHA takes
longer, re-run `python main.py --agent ingestion` to get a fresh 90-second window.

### `No valid projects found matching '...'`
The project name you passed via `--project` does not match any folder inside
`overleaf_projects/`. Project names are case-sensitive and must match exactly.
Run without `--project` first to let the ingestion agent download the project.

### Groq rate limit errors
The free Groq tier has per-minute token limits. The system will automatically retry
with exponential backoff. If it exhausts all retries, configure a Gemini or OpenAI
API key as a fallback in your `.env` file.

---

## 🗺️ Roadmap

- [ ] **Docker containerization** — Package the system into a Docker image with persistent
  volumes for the SQLite database and downloaded project files.
- [ ] **Scheduled execution** — Replace manual CLI triggers with a cron-based or
  APScheduler-based autonomous scheduler (e.g., literature search daily, progress
  tracking every 8 hours, supervisor report weekly).
- [ ] **Web Dashboard** — A local intranet Streamlit/Flask UI for lab managers to
  visualize `progress_snapshots` as velocity graphs and manage project/researcher
  assignments without CLI access.
- [ ] **Internal peer-review pipeline** — Replace the Stanford `paperreview.ai` dependency
  with a self-contained review pipeline powered by multi-query arXiv and Semantic Scholar
  searches, grounded in the same 7-dimension evaluation framework.
- [ ] **Microservices migration** — Split agents into independently deployable services
  communicating over a message queue (e.g., RabbitMQ or Redis Streams).