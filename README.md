# Academic Research Multi-Agent System 🔬🤖

A Python-based, multi-agent artificial intelligence system designed to automate and enhance academic research workflows. This project utilizes the Groq LLM API, browser automation, and strict Object-Oriented Programming (OOP) principles to create an autonomous pipeline that fetches real-time data, assists researchers with literature reviews, tracks progress, and enhances manuscripts.

## 🌟 Features & Architecture

The system is built on a modular, scalable architecture separating data ingestion from LLM processing. It operates a preliminary web-scraping agent followed by three specialized AI agents, all utilizing a shared `BaseAgent` class for robust API connectivity and logging.

### Phase 0: Data Ingestion (Delta Sync)
**Overleaf Scraper Agent:**
* Operates completely autonomously using Microsoft's **Playwright** framework.
* Incorporates **Session Persistence** to securely bypass login screens and CAPTCHA after an initial manual authentication.
* Implements a **Delta Sync** mechanism: Scans the researcher's Overleaf dashboard and downloads *only* new or recently modified projects, saving bandwidth and processing time.
* Uses direct endpoint navigation to reliably download ZIP archives, immune to front-end UI changes.

### Phase 1: Global Research
**Literature Research Agent:**
* Autonomously queries the LLM for the latest academic advancements on predefined lab topics.
* Generates comprehensive summaries and saves them as Markdown files.
* Maintains a rolling tracking table (CSV) of all researched topics to monitor trends over time.

### Phase 2: Manuscript Analysis
**Progress Tracking Agent (Triggered only on new data):**
* Analyzes the newly ingested plain text extracted from LaTeX files.
* Acts as an academic reviewer to provide constructive critique on tone, structure, and clarity.
* Acts as an academic editor to suggest concrete, paragraph-level writing improvements.

### Phase 3: Peer-Review & Innovation
**Research Enhancement Agent (Triggered only on new data):**
* Simulates a rigorous external peer-review process based on the manuscript's actual text.
* Translates harsh academic critiques into an actionable, supportive To-Do list for the research team.
* Evaluates the innovation level of the paper and suggests highly ambitious, novel directions to push the boundaries of the research.

## 🛠️ Utility Modules
* **Library Manager:** Automates the creation of directory structures and handles file I/O operations (Markdown, CSV) to maintain a pristine `research_library` ecosystem.
* **Overleaf Connector:** Extracts the downloaded ZIP files and uses Regular Expressions (RegEx) to strip LaTeX formatting, delivering clean plain text to the LLM agents.