# Academic Research Multi-Agent System 🔬🤖

A Python-based, multi-agent artificial intelligence system designed to automate and enhance academic research workflows. This project utilizes the Groq LLM API, browser automation (Playwright), strict Object-Oriented Programming (OOP) principles, and Defensive Programming to create an autonomous, decoupled pipeline that fetches real-time data, conducts targeted Google Scholar literature reviews, tracks progress, enhances manuscripts, and delivers beautifully formatted email reports.

## 🌟 Features & Architecture

The system is built on a modular, scalable architecture separating data ingestion from LLM processing. It operates a preliminary web-scraping agent followed by four specialized AI agents, all utilizing a shared `BaseAgent` class for robust API connectivity and centralized logging. 

A core feature of the system is its **Dual-Email Architecture**: It utilizes an official University Microsoft 365 account for academic credibility (e.g., retrieving source files and external submissions) while employing a decoupled Gmail relay account to safely dispatch internal notifications, successfully bypassing strict institutional SMTP blocks.

### Phase 0: Data Ingestion (Delta Sync)
**Data Ingestion Agent:**
* Operates completely autonomously using Microsoft's **Playwright** framework.
* Incorporates **Session Persistence** to securely bypass login screens and CAPTCHA after an initial manual authentication.
* Implements a **Delta Sync** mechanism: Scans the researcher's Overleaf dashboard and downloads *only* new or recently modified projects (fetching both ZIP source codes and compiled PDFs), saving bandwidth and processing time.
* Uses direct endpoint navigation to reliably download archives, immune to front-end UI changes.

### Phase 1: Global Research & Data Extraction
**Literature Research Agent:**
* Autonomously reads the actual `.tex` manuscript text to dynamically extract highly targeted research keywords.
* **Hybrid Search Engine Approach:** Navigates **Google Scholar** using Playwright to scrape the latest relevant publications, then seamlessly enriches the findings using the **Semantic Scholar API**. This provides full abstracts, exact citation counts, and publication venues, effectively eliminating LLM data hallucinations.
* Uses the LLM to extract structured metadata from the enriched findings, automatically populating a **14-Column Rolling CSV Comparison Table** per project. The agent is context-aware and can accurately identify and categorize theoretical-mathematical papers without inventing empirical metrics.
* Generates comprehensive markdown summaries featuring direct, clickable URLs to the discovered papers.

### Phase 2: Manuscript Analysis
**Progress Tracking Agent (Triggered only on new data):**
* Analyzes the newly ingested plain text extracted from LaTeX files.
* Utilizes a **Local Memory Diff Engine** to isolate and extract only the *delta* (newly added or modified sentences) since the last run.
* Acts as an academic reviewer to provide highly targeted, surgical feedback on the tone, structure, and clarity of the day's specific writing additions.

### Phase 3: Peer-Review & Innovation
**Research Enhancement Agent (Triggered autonomously):**
* Automates a rigorous external peer-review submission process by interfacing with Stanford's **paperreview.ai**.
* **Phase 1 (Upload):** Uses Playwright to autonomously upload the PDF manuscript and submit the university email address.
* **Phase 2 (Fetch & Analyze):** Operates an IMAP connector to read the incoming automated emails, utilizes Regex to extract the unique Stanford access token, and uses Playwright to scrape the generated peer-review from the web portal.
* Uses Groq LLM to translate harsh academic critiques into an actionable, supportive To-Do list with estimated effort hours and strict deadlines for the research team.

### Phase 4: Communication & Alerting
**Notification Agent (Event-Driven & Injected):**
* Acts as the system's external communication hub. Completely decoupled from the main execution flow, it is triggered via an **Event-Driven Architecture** the moment any agent finalizes a task.
* Implements **Dependency Injection (DI)**: The Orchestrator (`main.py`) instantiates a single shared notification service and injects it into all operational agents, preventing tight coupling and redundant connections.
* Uses a dynamic routing map (`researchers_map.json`) to direct specific project feedback to the appropriate researcher's inbox.
* Translates raw Markdown reports into beautifully styled, professional HTML layouts dynamically branded per agent (e.g., green for literature, purple for project management).
* Automatically sends an executive summary via a secure SMTP relay connection, with full detailed reports embedded directly in the email body.

## 🛠️ Utility Modules & Infrastructure
* **Centralized Configuration (`config.py`):** Acts as the Single Source of Truth for the entire system. Eliminates "magic numbers" and hardcoded paths by centrally managing all environment variables, API limits, UI timeouts, models, and directory structures.
* **Defensive Programming & Resilience:** Built into the `BaseAgent`, featuring strict validation for all LLM inputs and outputs. It implements **Exponential Backoff** for API rate limits, secure JSON parsing with graceful fallbacks, and real exception bubbling (`RuntimeError`) to prevent silent systemic crashes.
* **Library Manager:** Automates the creation of an organized directory structure and handles file I/O operations to maintain a pristine `research_library` ecosystem.
* **Overleaf Connector:** Extracts downloaded ZIP files and uses highly optimized Regular Expressions (RegEx) to strip heavy LaTeX formatting, delivering clean plain text to the LLM agents.
* **Garbage Collector:** Implements a strict Data Retention Policy (TTL). Safely scans specific directories to automatically purge outdated markdown reports older than 30 days, preventing disk space exhaustion while preserving critical rolling CSVs and system state files.
* **Production Logging System:** Uses Python's `RotatingFileHandler` implemented within the `BaseAgent`. Automatically generates distinct `.log` files for each running agent, backing them up once they reach 5MB. This ensures infinite system uptime and safe monitoring without bloating the server's storage.