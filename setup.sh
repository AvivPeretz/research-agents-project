#!/bin/bash

# =============================================================
# Academic Research Multi-Agent System — Automated Setup Script
# =============================================================
# Run this script once from the project root directory:
#   bash setup.sh
# =============================================================

set -e  # Exit immediately if any command fails

# ── Colors for terminal output ────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── Helper functions ──────────────────────────────────────────
print_header() {
    echo ""
    echo -e "${BLUE}${BOLD}============================================${NC}"
    echo -e "${BLUE}${BOLD}  $1${NC}"
    echo -e "${BLUE}${BOLD}============================================${NC}"
    echo ""
}

print_step() {
    echo -e "${BOLD}▶ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# ─────────────────────────────────────────────────────────────
print_header "Academic Research Multi-Agent System — Setup"
echo -e "This script will set up your local environment automatically."
echo -e "Estimated time: ${BOLD}2–5 minutes${NC} depending on your internet speed."
echo ""


# ── Step 1: Check Python version ─────────────────────────────
print_header "Step 1 of 5 — Checking Python Version"

# Detect correct python command (python3 on macOS/Linux, python on Windows)
if command -v python3 &>/dev/null; then
    PYTHON_CMD="python3"
elif command -v python &>/dev/null; then
    PYTHON_CMD="python"
else
    print_error "Python 3 is not installed or not in your PATH."
    echo "Please install Python 3.10 or higher from https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$($PYTHON_CMD -c 'import sys; print(sys.version_info.major)')
PYTHON_MINOR=$($PYTHON_CMD -c 'import sys; print(sys.version_info.minor)')

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    print_error "Python $PYTHON_VERSION detected. This project requires Python 3.10 or higher."
    echo "Please upgrade Python from https://www.python.org/downloads/"
    exit 1
fi

print_success "Python $PYTHON_VERSION detected — OK"


# ── Step 2: Create virtual environment ───────────────────────
print_header "Step 2 of 5 — Creating Virtual Environment"

if [ -d "venv" ]; then
    print_warning "A 'venv' folder already exists. Skipping creation."
else
    print_step "Creating virtual environment in ./venv ..."
    $PYTHON_CMD -m venv venv
    print_success "Virtual environment created."
fi

# Activate the virtual environment
print_step "Activating virtual environment..."

# Detect OS for correct activation path
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    # Windows (Git Bash / Cygwin)
    source venv/Scripts/activate
else
    # macOS / Linux
    source venv/bin/activate
fi

print_success "Virtual environment activated."


# ── Step 3: Install Python dependencies ──────────────────────
print_header "Step 3 of 5 — Installing Python Dependencies"

print_step "Running pip install from requirements.txt ..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
print_success "All Python packages installed successfully."


# ── Step 4: Install Playwright browsers ──────────────────────
print_header "Step 4 of 5 — Installing Playwright Browser"

print_step "Downloading Chromium browser engine for Playwright..."
echo "(This may take 1–3 minutes on the first run)"
echo ""

# Try standard install first, fall back to --with-deps for Linux
if playwright install chromium 2>/dev/null; then
    print_success "Playwright Chromium installed successfully."
else
    print_warning "Standard install failed. Retrying with system dependencies (Linux mode)..."
    playwright install --with-deps chromium
    print_success "Playwright Chromium installed with system dependencies."
fi


# ── Step 5: Create .env file ──────────────────────────────────
print_header "Step 5 of 5 — Setting Up Environment Variables"

if [ -f ".env" ]; then
    print_warning ".env file already exists. Skipping creation to avoid overwriting your credentials."
else
    if [ -f ".env.example" ]; then
        cp .env.example .env
        print_success ".env file created from .env.example template."
    else
        # Fallback: create .env from scratch if .env.example is missing
        cat > .env << 'EOF'
# ============================================================
# LLM PROVIDERS
# ============================================================

# [REQUIRED] Primary LLM — Free tier at console.groq.com
GROQ_API_KEY=

# [OPTIONAL] Fallback LLM #1 — Free tier at aistudio.google.com
GEMINI_API_KEY=

# [OPTIONAL] Fallback LLM #2 — Paid, at platform.openai.com
OPENAI_API_KEY=


# ============================================================
# EMAIL — GMAIL RELAY (Sender Account)
# ============================================================
# A dedicated Gmail account used only for sending notification emails.
# Use a Gmail App Password (NOT your regular Gmail password).
# To generate one: Google Account → Security → 2-Step Verification → App Passwords

NOTIFICATION_SENDER_EMAIL=
NOTIFICATION_SENDER_PASSWORD=


# ============================================================
# OVERLEAF / UNIVERSITY ACCOUNT (Receiver Account)
# ============================================================
# Your university email connected to Overleaf.
# Used for: logging into Overleaf and receiving Stanford review tokens.

OVERLEAF_EMAIL=
OVERLEAF_PASSWORD=
EOF
        print_success ".env file created from built-in template."
    fi

    echo ""
    print_warning "ACTION REQUIRED: Open the .env file and fill in your credentials."
    echo ""
    echo "  Required fields:"
    echo "    GROQ_API_KEY              → Get free key at https://console.groq.com"
    echo "    NOTIFICATION_SENDER_EMAIL → Your Gmail relay address"
    echo "    NOTIFICATION_SENDER_PASSWORD → Gmail App Password (not your login password)"
    echo "    OVERLEAF_EMAIL            → Your university email"
    echo "    OVERLEAF_PASSWORD         → Your Overleaf password"
    echo ""
    echo "  See the README for detailed instructions on each field."
fi


# ── Final summary ─────────────────────────────────────────────
print_header "Setup Complete 🎉"

echo -e "${GREEN}${BOLD}Everything is installed and ready.${NC}"
echo ""
echo -e "${BOLD}Next steps:${NC}"
echo ""
echo "  1. Fill in your credentials:"
echo -e "       ${YELLOW}nano .env${NC}   (or open .env in any text editor)"
echo ""
echo "  2. Activate the virtual environment (required each session):"
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
    echo -e "       ${YELLOW}source venv/Scripts/activate${NC}"
else
    echo -e "       ${YELLOW}source venv/bin/activate${NC}"
fi
echo ""
echo "  3. Run the first-time Overleaf login (opens a browser window):"
echo -e "       ${YELLOW}python main.py --agent ingestion${NC}"
echo ""
echo "  4. Run a test to confirm everything works:"
echo -e "       ${YELLOW}python main.py --agent literature --project \"Your_Project_Name\"${NC}"
echo ""
echo "  For full usage instructions, see README.md"
echo ""
