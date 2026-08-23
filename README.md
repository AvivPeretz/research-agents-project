# Academic Research Multi-Agent System

A Python multi-agent pipeline that automates research workflows for academic labs. Six
specialized agents — orchestrated by `main.py` — handle Overleaf project synchronization,
literature discovery, manuscript progress tracking, external peer-review submission, lab
supervision reporting, and email delivery. The system is designed to run unattended on a
lab server and notify researchers and supervisors by email.

---

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Configuration](#configuration)
- [How to Run](#how-to-run)
- [Testing](#testing)
- [File Structure](#file-structure)
- [Current Status](#current-status)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)

---

## Architecture

The pipeline runs in five sequential phases. Each phase is owned by a dedicated agent.
All agents extend a shared `BaseAgent` abstract class that provides the Multi-LLM waterfall,
exponential-backoff retry logic, and rotating file logging.

---

### Phase 0 — Data Ingestion (Delta Sync)
**`DataIngestionAgent`** (`ingestion/data_ingestion_agent.py`)

- Uses Playwright with a persisted browser session (JSON storage state) to log into
  Overleaf and scan the project dashboard.
- Implements a **Delta Sync**: only projects whose "last modified" timestamp changed
  since the previous run are re-downloaded, saving bandwidth and processing time.
- Downloads both the ZIP source archive and the compiled PDF for each changed project.
- On session expiry, opens a visible browser window and waits up to 5 minutes for the
  operator to log in manually; sends an admin alert email before opening the window.
- Per-project failures are isolated: one failing download does not abort the rest of
  the sync cycle.

---

### Phase 1 — Literature Research
**`LiteratureResearchAgent`** (`agents/literature_research_agent.py`)

- Reads the downloaded `.tex` files for each project and uses the LLM to extract two
  targeted search queries: a topic keyword set and a method keyword set.
- Manuscript text is **structure-aware sampled** rather than blindly truncated: instead
  of taking the first `MAX_PROJECT_TEXT_CHARS` characters (which for a long document only
  captures the introduction), `OverleafConnector.extract_representative_sample` scans for
  `\chapter`/`\section`/`\subsection`/`\subsubsection` markers and builds a sample spanning
  the whole document — abstract in full, an excerpt after every heading (budget allocated
  adaptively across however many headings exist), and extra room reserved for the final
  section (typically the conclusion). Falls back to prefix truncation for documents with
  no detectable LaTeX sectioning commands.
- Runs a **three-tier search pipeline**:
  1. **Semantic Scholar API** (primary) — authenticated with `SEMANTIC_SCHOLAR_API_KEY`
     for higher rate limits; returns full abstracts, citation counts, and venues.
  2. **SerpAPI Google Scholar** (fallback) — activated only when Semantic Scholar returns
     no results; returns structured JSON without browser automation.
     Requires `SERPAPI_API_KEY` (250 free searches/month at serpapi.com).
  3. **scholarly Python library** (last resort) — activated only when both above sources
     fail; pure Python, no API key required.
  4. If all three sources fail, the agent logs an error and skips the project — no empty
     report is sent.
- After collection, each paper is enriched via the **OpenAlex API** (topics, keywords,
  open-access URL). OpenAlex is an enrichment layer, not a search source.
- An LLM relevance filter drops papers that are clearly off-topic before capping the
  pool at 15 unique papers per run.
- Before the final summarization call, each paper's abstract is **adaptively truncated**
  (`utils/token_budget.py`) so the total JSON payload sent to the LLM stays under a fixed
  character budget regardless of how many papers were fetched — the per-paper cap scales
  down as the paper count grows, rather than using one fixed length that only works for a
  specific batch size. This prevents oversized-request rejections from the LLM provider on
  projects that return many papers with long abstracts.
- LLM output is validated against a **Pydantic v2 schema** (`LiteratureReport`) before
  any downstream processing, eliminating hallucinated or malformed records.
- Results are written to a rolling CSV comparison table and a Markdown summary per
  project; a formatted HTML email is sent to the researcher.
- All projects are processed in parallel via `ThreadPoolExecutor`.

---

### Phase 2 — Manuscript Progress Tracking
**`ProgressTrackingAgent`** (`agents/progress_tracking_agent.py`)

- Reads the plain text extracted from `.tex` files and computes the delta versus the
  text seen on the previous run (SQLite-backed, per-project).
- Sends only the new or modified sentences to the LLM, saving tokens on unchanged content.
- The LLM acts as an academic reviewer and provides targeted feedback on the tone,
  structure, and clarity of the day's additions.
- Records a progress snapshot (date, delta character count, `had_changes` flag) to the
  `progress_snapshots` table; used by SupervisorStatusAgent for trend analysis.
- All projects are processed in parallel via `ThreadPoolExecutor`.

---

### Phase 3 — Research Enhancement (External Peer Review)
**`ResearchEnhancementAgent`** (`agents/research_enhancement_agent.py`)

- Manages a two-phase external review cycle via Stanford's `paperreview.ai`:
  - **Upload phase**: Playwright uploads the compiled PDF and submits the university email,
    then captures the review access token directly from the confirmation page. paperreview.ai
    itself warns that email delivery of this token is unreliable for some addresses, so the
    token is saved immediately rather than relying on an email arriving later.
  - **Fetch phase**: on a later run, polls paperreview.ai's JSON API
    (`GET /api/review/<token>`) with the saved token. Processing can take hours, so a
    not-ready response just means "try again next run" — it is not treated as a failure.
- Translates the raw academic critique into an actionable to-do list with estimated effort
  and deadlines using the LLM.
- **Internal review fallback**: when the Stanford pipeline fails at any stage, the agent
  generates a full internal review directly from the manuscript text and rolling CSV data
  via a single LLM call. The result is saved and emailed identically to a Stanford review,
  and the project status is recorded as `INTERNAL_REVIEW_COMPLETED`.
- Projects shorter than 3,000 characters are skipped (`SKIPPED_INSUFFICIENT_TEXT`).
- Projects already in `REVIEW_COMPLETED` or `INTERNAL_REVIEW_COMPLETED` are not
  re-processed.
- Chromium is launched with `--no-sandbox` and `--disable-dev-shm-usage` for Docker/Linux
  compatibility.

---

### Phase 4 — Supervisor Status Report
**`SupervisorStatusAgent`** (`agents/supervisor_status_agent.py`)

- Groups all active projects by their assigned supervisor email address.
- For each supervisor, calculates 28-day objective metrics per project: active vs silent
  days, average characters per active day, current silent streak, weekly character counts.
- Sends the raw metrics to the LLM for classification into `ON_TRACK`, `NEEDS_ATTENTION`,
  or `STALLED`, validated against a `SupervisorReport` Pydantic schema.
- Formats the result as a Markdown table and dispatches a weekly email to each supervisor.
- New projects (under 14 days old) are automatically classified as `ON_TRACK`.

---

### Support — Notifications
**`NotificationAgent`** (`agents/notification_agent.py`)

- Instantiated once by `main.py` and injected into all agents (Dependency Injection).
- Formats Markdown reports as styled HTML emails branded per agent (colour-coded).
- Routes each notification to the correct researcher via the SQLite database.
- Sends via a Gmail SMTP relay account using App Passwords; retries on transient failures.
- `send_admin_alert()` delivers operational alerts (session expiry, pipeline crashes) to
  the operator email.
- Does not use the LLM. It extends `BaseAgent` in its class declaration but skips
  `BaseAgent.__init__()`, so none of the LLM-waterfall machinery applies to it.

---

### Infrastructure
**`BaseAgent`** (`agents/base_agent.py`) — abstract base class for all LLM agents.

- **Multi-LLM Waterfall**: tries Groq (primary) → Gemini (fallback 1) → NVIDIA NIM
  (fallback 2) → OpenAI (fallback 3). Each provider gets up to 3 retries with
  exponential backoff before the waterfall advances. Permanent auth/context-size
  errors skip retries immediately.
- Raises `RuntimeError` when all providers are exhausted — never returns `None` silently.
- Sets up a `RotatingFileHandler` (5 MB limit, 3 backups) plus `StreamHandler` for each
  agent, writing to `logs/<AgentName>.log`.

- Rate-limit cooldowns are shared across every agent in the same process (a 429 on one
  provider protects every other agent's calls for the rest of the run) and persisted to
  SQLite (`llm_provider_cooldowns`) so they survive a cold restart, not just an
  in-process retry.
- Permanent quota/billing errors (`insufficient_quota`, HTTP 402) are classified
  separately from transient rate-limit errors: they skip retries and do not start a
  cooldown, since retrying a billing-exhausted key wastes an attempt on every
  subsequent call until a human intervenes.
- A full waterfall exhaustion for a given project sends exactly one deduplicated admin
  alert per project per run (`_alert_waterfall_exhausted`) rather than failing silently.
- **`NotificationAgent`** extends `BaseAgent` in its class declaration (for type
  consistency across the agent list) but does not call `BaseAgent.__init__()` and does
  not use the LLM waterfall — it manages its own minimal logger setup.

**`main.py`** — orchestrator and CLI entry point, wrapped in an `fcntl`-based run-lock
(`run.lock`, POSIX only) so a scheduled invocation that overlaps a still-running one
exits cleanly instead of racing it. See [How to Run](#how-to-run).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10–3.13 |
| LLM providers | Groq (`openai/gpt-oss-120b`), Gemini (`gemini-2.5-flash`), NVIDIA NIM (`nvidia/nemotron-3-super-120b-a12b`), OpenAI (`gpt-4o-mini`) — waterfall order, verified from `agents/base_agent.py`; expect this roster to keep changing as providers are evaluated |
| LLM contracts | Pydantic v2 |
| Browser automation | Playwright (Chromium) |
| Literature search | Semantic Scholar API → SerpAPI → scholarly |
| Literature enrichment | OpenAlex API |
| Email | smtplib (Gmail SMTP relay, outbound notifications only) |
| Database | SQLite via a custom `DatabaseManager` wrapper |
| Dashboard | Streamlit (`dashboard.py`) |
| Logging | Python `RotatingFileHandler` |
| Testing | pytest (400 tests, 2 additional `live` tests deselected by default) |

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10–3.13 (not 3.14+) | [python.org](https://www.python.org/downloads/) |
| pip | Latest | Bundled with Python |
| Git | Any | For cloning the repository |
| A Groq API Key | Free | [console.groq.com](https://console.groq.com) — **required** |
| A Gmail account | Any | Used as the SMTP relay for outbound notifications |
| Gmail App Password | — | See [Configuration](#configuration) |
| An Overleaf account | Free/Pro | Your university Overleaf account |

Gemini, NVIDIA NIM, and OpenAI API keys are **optional**. They activate as automatic
LLM fallbacks when Groq is unavailable. The system runs correctly with only Groq
configured.

---

## Setup

### Option A — Automated Setup (Recommended)

A setup script handles virtual environment creation, dependency installation, Playwright
browser download, and `.env` file scaffolding.

```bash
git clone https://github.com/AvivPeretz/research-agents-project.git
cd research-agents
bash setup.sh
```

After the script finishes:

1. Open `.env` and fill in your credentials (see [Configuration](#configuration)).
2. Create `researchers_map.json` in the project root (see
   [Registering Projects](#registering-projects)).
3. Run the Overleaf session setup (see [First-Time Overleaf Login](#first-time-overleaf-login)).

---

### Option B — Manual Setup

```bash
git clone https://github.com/AvivPeretz/research-agents-project.git
cd research-agents

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate          # macOS / Linux
# .\venv\Scripts\Activate.ps1    # Windows PowerShell

# Install dependencies
pip install -r requirements.txt

# Download Playwright browser
playwright install chromium
# On Linux if you encounter permission errors:
# playwright install --with-deps chromium

# Create your .env file
cp .env.example .env
```

Open `.env` and fill in your credentials (see [Configuration](#configuration)).

---

### First-Time Overleaf Login

The Data Ingestion Agent stores its Overleaf session as a JSON file (`scholar_state.json`).
Before the first automated run, create this session file by running the dedicated setup script:

```bash
python setup_overleaf_session.py
```

A visible browser window opens. Your credentials are pre-filled automatically. If a
reCAPTCHA appears, solve it manually. You have **5 minutes** to reach the Overleaf dashboard.
Once you do, the session is saved and the window closes. All future runs use the saved
session silently.

Re-run this script whenever the session expires (typically once per quarter).

If you are deploying to a remote server, run this script locally and copy the state file:

```bash
scp scholar_state.json user@server:/path/to/project/
```

---

### Registering Projects

For email notifications to be routed to the correct researcher, each Overleaf project
must be registered in the database. Create `researchers_map.json` in the project root:

```json
{
  "Your_Overleaf_Project_Name": "student.name@university.edu",
  "Another_Project": "another.student@university.edu"
}
```

Project names must match **exactly** the names shown on the Overleaf dashboard
(case-sensitive, spaces included). On the first run, `main.py` migrates this data into
the SQLite database automatically. The JSON file is only needed once.

---

The Gmail account receiving forwarded emails must be the same account configured as
`NOTIFICATION_SENDER_EMAIL` in `.env`.

---

## Configuration

All settings are managed via a `.env` file in the project root. Never commit this file
to Git — it is already in `.gitignore`.

```dotenv
# ── LLM PROVIDERS ─────────────────────────────────────────────────────────────

# [REQUIRED] Primary LLM — free tier at console.groq.com
GROQ_API_KEY=gsk_...

# [OPTIONAL] Fallback LLM #1 — free tier at aistudio.google.com
GEMINI_API_KEY=AIza...

# [OPTIONAL] Fallback LLM #2 — build.nvidia.com NIM endpoint
NVIDIA_NIM_API_KEY=nvapi-...

# [OPTIONAL] Fallback LLM #3 — paid, at platform.openai.com
OPENAI_API_KEY=sk-...


# ── EMAIL — GMAIL RELAY (sender account) ──────────────────────────────────────
# A Gmail account used only for sending notifications. Does not need to be your
# university email. Recommendation: create a dedicated Gmail account for this.

NOTIFICATION_SENDER_EMAIL=your-relay@gmail.com
NOTIFICATION_SENDER_PASSWORD=xxxx xxxx xxxx xxxx
# ^ This is a Gmail App Password (16 characters with spaces), NOT your login password.
# To generate: Google Account → Security → 2-Step Verification → App Passwords


# ── OVERLEAF / UNIVERSITY ACCOUNT ─────────────────────────────────────────────
# Your university email connected to Overleaf.
# Used for logging in to Overleaf and submitted as the contact email for
# Stanford paperreview.ai (the review token itself is scraped from the
# confirmation page, not received by email).

OVERLEAF_EMAIL=your.name@university.edu
OVERLEAF_PASSWORD=your-overleaf-password


# ── OPTIONAL API KEYS ─────────────────────────────────────────────────────────

# [OPTIONAL] Semantic Scholar API key — higher rate limits for literature search.
# Without it the API still works but at a lower unauthenticated rate limit.
# Register at semanticscholar.org/product/api
SEMANTIC_SCHOLAR_API_KEY=

# [OPTIONAL] SerpAPI key — Google Scholar fallback for literature search.
# Activated only when Semantic Scholar returns no results.
# Free tier: 250 searches/month at https://serpapi.com/
SERPAPI_API_KEY=
```

### How to generate a Gmail App Password

1. Go to your [Google Account](https://myaccount.google.com).
2. Navigate to **Security** → **2-Step Verification** (must be enabled first).
3. Scroll to **App Passwords**.
4. Select app: **Mail**, device: **Other** → type a name (e.g., "ResearchAgents").
5. Copy the 16-character password into `NOTIFICATION_SENDER_PASSWORD`, including spaces.

---

## How to Run

```bash
source venv/bin/activate
python main.py [--agent AGENT] [--project PROJECT] [--dry-run]
```

### Agent flags

| `--agent` value | Description |
|---|---|
| `all` | Run the full pipeline *(default)* |
| `ingestion` | Phase 0 — Delta-sync Overleaf projects |
| `literature` | Phase 1 — Fetch and summarize related papers |
| `progress` | Phase 2 — Analyse manuscript delta and provide feedback |
| `enhancement` | Phase 3 — Submit to Stanford peer-review and collect results |
| `supervisor` | Phase 4 — Generate and send the supervisor status report |
| `gc` | Garbage collector — delete Markdown files older than 30 days |

### Common invocations

```bash
# Run the full pipeline across all projects
python main.py

# Run only the literature agent across all projects
python main.py --agent literature

# Run only the progress agent for a single project
python main.py --agent progress --project "My_Thesis"

# Run the supervisor report
python main.py --agent supervisor

# Run the full pipeline on one project only
python main.py --project "My_Thesis"

# Dry run — prints what would execute without running any agent
python main.py --dry-run

# Show help
python main.py --help
```

When running `--agent all`, agents 1–3 (literature, progress, enhancement) are skipped
for projects that the ingestion agent did not update in the same run. Use an explicit
`--agent` flag to run any agent on existing (already-downloaded) projects regardless of
whether they changed.

### Checking logs

Each agent writes to its own rotating log file in `logs/`:

```
logs/
├── LiteratureResearchAgent.log
├── ProgressTrackingAgent.log
├── ResearchEnhancementAgent.log
├── SupervisorStatusAgent.log
├── NotificationAgent.log
└── DatabaseManager.log
```

Log files rotate at 5 MB; up to 3 backups are kept per agent.

### Running the dashboard

A Streamlit dashboard (`dashboard.py`) provides a UI for inspecting project state,
agent run history, and progress snapshots without using the CLI.

```bash
streamlit run dashboard.py
```

---

## Testing

The test suite uses pytest and covers unit tests, integration tests, crash/resilience
tests, DB tests, idempotency tests, and stress tests.

```bash
source venv/bin/activate
pytest tests/ -v
```

Current count (verified by running the suite in this session): **400 passed, 2
deselected**. The 2 deselected tests carry the `live` pytest marker (see
`pytest.ini`) — they make real network calls to LLM provider APIs and require real
API keys, so they're excluded from the default run. Run them explicitly with
`pytest -m live tests/live/`.

Tests are organized under `tests/`:

| Directory | Coverage area |
|---|---|
| `tests/unit/` | Config, schemas, OverleafConnector, GarbageCollector, delta engine |
| `tests/integration/` | DataIngestionAgent, LiteratureAgent, ProgressAgent, EnhancementAgent, NotificationAgent, SupervisorAgent, CSV paths, search fallback chain |
| `tests/crash/` | LLM provider failures, Playwright failures, alerting reliability, pipeline resilience, state machine, DB failures, email failures, filesystem failures |
| `tests/db/` | DatabaseManager — table creation, CRUD, migration, snapshots |
| `tests/idempotency/` | Re-running agents on unchanged data produces no side effects |
| `tests/stress/` | Concurrent project processing under load |

---

## File Structure

```
research-agents/
├── agents/
│   ├── base_agent.py               # Abstract base class: LLM waterfall, logging
│   ├── literature_research_agent.py
│   ├── progress_tracking_agent.py
│   ├── research_enhancement_agent.py
│   ├── supervisor_status_agent.py
│   └── notification_agent.py
├── ingestion/
│   └── data_ingestion_agent.py     # Overleaf delta sync via Playwright
├── domain/
│   └── schemas.py                  # Pydantic v2 contracts for all LLM outputs
├── utils/
│   ├── database_manager.py         # SQLite wrapper — single source of truth
│   ├── garbage_collector.py        # TTL-based Markdown file cleanup
│   ├── library_manager.py          # File I/O: Markdown summaries, rolling CSVs
│   ├── literature_fetcher.py       # Semantic Scholar → SerpAPI → scholarly chain
│   ├── overleaf_connector.py       # LaTeX → plain text via regex, structure-aware sampling
│   └── token_budget.py             # Adaptive char-budget caps for LLM payload sizing
├── tests/                          # 400 pytest tests (+2 `live` tests, opt-in only)
│   ├── crash/
│   ├── db/
│   ├── idempotency/
│   ├── integration/
│   ├── live/                       # Real-network LLM provider calls; `live` marker
│   ├── stress/
│   ├── unit/
│   ├── fixtures/
│   └── conftest.py
├── research_library/               # Generated output (gitignored)
│   ├── literature_reviews/
│   ├── project_tracking/
│   ├── project_enhancement/
│   ├── comparison_tables/
│   └── system.db                   # SQLite database
├── overleaf_projects/              # Downloaded .tex + PDF files (gitignored)
├── logs/                           # Rotating log files (gitignored)
├── config.py                       # Centralized Config class
├── main.py                         # Orchestrator + argparse CLI
├── dashboard.py                    # Streamlit monitoring dashboard
├── setup_overleaf_session.py       # One-time Overleaf session bootstrap
├── setup.sh                        # Automated setup script (venv + deps + .env)
├── requirements.txt
├── pytest.ini                      # `live` marker config — excludes real-network tests by default
├── .env.example                    # Template — copy to .env and fill in
└── .gitignore
```

---

## Current Status

The system has completed a university course requirement and is currently in a live
test phase against real research projects, ahead of eventual deployment to a lab
server. This section separates what's actually running from what's designed but not
yet built, and from what's blocked on a decision outside engineering.

**Implemented and verified (real code, real test coverage):**
- All six agents described in [Architecture](#architecture), orchestrated by `main.py`.
- The four-provider LLM waterfall (Groq → Gemini → NVIDIA NIM → OpenAI), including
  shared/persisted cooldowns and permanent-vs-transient error classification.
- The `fcntl`-based run-lock preventing overlapping `main.py` invocations.
- 400 passing tests across unit, integration, crash/resilience, DB, idempotency, and
  stress suites (verified by running the suite in this session — see
  [Testing](#testing)).

**Designed but not yet implemented:**
- **Heartbeat + independent watchdog** (`docs/superpowers/plans/2026-08-21-heartbeat-watchdog.md`):
  a design for a `run_heartbeats` SQLite table plus a standalone, stdlib-only watchdog
  script that would notice a missing/failed run through a code path independent of the
  normal email-alerting path. This closes a known gap — today, if the primary
  notification path fails at the exact moment an agent crashes, nothing else notices —
  but the design has not been implemented: there is no `watchdog.py` and no
  `run_heartbeats` table in the codebase yet.

**Blocked on a decision outside engineering:**
- **Overleaf Premium / native Git integration**: the project's own engineering analysis
  (`docs/superpowers/plans/2026-08-21-stability-hardening.md`) recommends migrating off
  session-based Overleaf scraping to a paid Git-integration tier, independent of cost,
  because the current approach carries both operational fragility (see below) and a
  ToS risk that a paid tier removes outright. This is pending a department-head
  decision on the subscription cost, not an engineering blocker.
- **Supervisor email mapping** for the projects currently in live testing is entered
  and maintained manually by the operator; it is not yet a self-service or automated
  process.

---

## Known Limitations

**Overleaf session renewal (manual, ~quarterly)**
The Overleaf session is stored as a JSON file. Overleaf's sessions expire roughly once
per quarter. When that happens, re-run `setup_overleaf_session.py` locally, then copy
the resulting `scholar_state.json` to the server. Full zero-intervention operation is
not achievable for the Overleaf login step.

**Google Scholar scraping removed**
Direct Google Scholar scraping via Playwright has been removed. The fallback chain for
literature search is now: Semantic Scholar → SerpAPI → scholarly Python library. The
scholarly library may hit rate limits under heavy use; SerpAPI (250 free searches/month)
is the more reliable fallback.

**SerpAPI free tier**
The SerpAPI fallback is limited to 250 Google Scholar searches per month on the free
plan. If your lab tracks many projects simultaneously, monitor usage at serpapi.com.

**Stanford `paperreview.ai` dependency**
The external peer-review phase depends on Stanford's third-party service. If that service
changes its upload form, confirmation page, or `/api/review/<token>` response format, the
upload/fetch logic in `ResearchEnhancementAgent` will need updating. The internal review
fallback activates automatically on upload failure or if a project stays unreviewed past
the 48-hour timeout, maintaining continuity.

**`requirements.txt` does not include the dashboard's dependencies**
`dashboard.py` imports `streamlit` and `plotly`, but neither package is listed in
`requirements.txt`. A fresh `pip install -r requirements.txt` followed by
`streamlit run dashboard.py` will fail with `ModuleNotFoundError` until those two
packages are installed separately. This was flagged in a prior review and has
recurred — verify `requirements.txt` against actual imports (e.g. `grep` every
`import`/`from` in the repo against the pinned package list) before each release
rather than assuming it stayed in sync.

**Single-machine deployment**
The current architecture runs all agents sequentially in a single process. On a lab server
with many projects, long runs (literature + enhancement for 10+ projects) may take
30–60 minutes per cycle. The `ThreadPoolExecutor` parallelism inside individual agents
mitigates this, but cross-agent parallelism is not implemented.

---

## Roadmap

- [ ] **Docker containerization** — Package the system into a Docker image with persistent
  volumes for SQLite and downloaded project files; deploy to the university lab server.
  Not yet started.
- [ ] **Heartbeat + independent watchdog** — Designed (see [Current Status](#current-status)
  and `docs/superpowers/plans/2026-08-21-heartbeat-watchdog.md`) but not implemented:
  no `watchdog.py`, no `run_heartbeats` table exist in the codebase yet.
- [ ] **Overleaf Premium / Git integration migration** — Recommended by engineering
  analysis; pending a department-head decision on subscription cost (see
  [Current Status](#current-status)).
- [ ] **Streamlit/Flask intranet dashboard** — A hosted lab manager UI for visualizing
  `progress_snapshots` as velocity graphs, managing project/researcher assignments, and
  reviewing agent run history without CLI access.
- [ ] **External Status Agent** — Track research velocity across time (paper submission
  rate, citation growth, collaboration patterns) and surface early-warning signals for
  stalled projects.
- [x] **Internal peer-review pipeline** — Self-contained review powered by manuscript
  text + rolling CSV data; activated automatically as fallback when Stanford pipeline fails.
- [x] **Three-tier literature search** — Semantic Scholar → SerpAPI → scholarly, with
  OpenAlex enrichment and LLM relevance filtering.
- [x] **Multi-LLM waterfall** — Groq → Gemini → NVIDIA NIM → OpenAI with per-provider
  exponential backoff, shared/persisted cooldowns, and permanent-error classification.
  The provider roster has changed twice already (a Cerebras integration was added and
  fully removed after a live billing failure) — treat this order as subject to further
  change as providers are evaluated, not as a fixed architecture decision.
- [x] **Run-lock** — `fcntl`-based lock in `main.py` preventing overlapping scheduled
  invocations from racing each other.
