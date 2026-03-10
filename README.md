# Academic Research Multi-Agent System 🔬🤖

A Python-based, multi-agent artificial intelligence system designed to automate and enhance academic research workflows. This project utilizes the Groq LLM API and Object-Oriented Programming (OOP) principles to create autonomous agents that assist researchers with literature reviews, progress tracking, and manuscript enhancement.

## 🌟 Features & Architecture

The system is built on a modular, extensible architecture utilizing a shared `BaseAgent` for LLM connectivity and logging. It operates three specialized agents:

1. **Literature Research Agent:** * Autonomously queries the LLM for the latest academic advancements on provided topics.
   * Generates comprehensive summaries and saves them as Markdown files.
   * Maintains a rolling tracking table (CSV) of all researched topics.

2. **Progress Tracking Agent:**
   * Reads raw LaTeX (`.tex`) files from a local Drop Folder (simulating Overleaf integration).
   * Parses and cleans LaTeX formatting using Regular Expressions.
   * Acts as an academic reviewer to provide constructive critique on tone and clarity.
   * Acts as an academic editor to suggest concrete writing improvements.

3. **Research Enhancement Agent:**
   * Simulates a rigorous external peer-review process based on the manuscript's actual text.
   * Translates harsh academic critiques into an actionable, supportive To-Do list for the research team.
   * Analyzes the innovation level of the paper and suggests ambitious directions for improvement.

### Utility Modules
* **Library Manager:** Automates the creation of directories and the saving of outputs (Markdown, CSV) to maintain a clean `research_library` ecosystem.
* **Overleaf Connector:** Handles the ingestion and cleaning of `.tex` files from a local Drop Folder, preparing plain text for LLM consumption.
