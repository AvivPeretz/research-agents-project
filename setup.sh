#!/bin/bash

# =============================================================
# Academic Research Multi-Agent System — Automated Setup Script
# Compatible with: macOS, Linux, Windows (Git Bash)
# Supported Python versions: 3.10, 3.11, 3.12, 3.13
# =============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

print_header() {
    echo ""
    echo -e "${BLUE}${BOLD}============================================${NC}"
    echo -e "${BLUE}${BOLD}  $1${NC}"
    echo -e "${BLUE}${BOLD}============================================${NC}"
    echo ""
}

print_step()    { echo -e "${BOLD}▶ $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }

detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        OS="linux"
    elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" || "$OSTYPE" == "win32" ]]; then
        OS="windows"
    else
        OS="unknown"
    fi
}

# ─────────────────────────────────────────────────────────────
print_header "Academic Research Multi-Agent System — Setup"
echo -e "This script will set up your local environment automatically."
echo -e "Estimated time: ${BOLD}2–5 minutes${NC} depending on your internet speed."
echo ""

detect_os
print_step "Detected operating system: $OS"
echo ""


# ── Step 1: Find a compatible Python interpreter ─────────────
print_header "Step 1 of 5 — Checking Python Version"

PYTHON_CMD=""
PYTHON_VERSION=""

# Generic candidates (work on macOS/Linux)
GENERIC_CANDIDATES=("python3.13" "python3.12" "python3.11" "python3.10" "python3" "python")

# Windows-specific full paths (for Git Bash where version-specific commands don't exist)
WINDOWS_CANDIDATES=(
    "/c/Users/$USERNAME/AppData/Local/Programs/Python/Python313/python.exe"
    "/c/Users/$USERNAME/AppData/Local/Programs/Python/Python312/python.exe"
    "/c/Users/$USERNAME/AppData/Local/Programs/Python/Python311/python.exe"
    "/c/Users/$USERNAME/AppData/Local/Programs/Python/Python310/python.exe"
    "/c/Program Files/Python313/python.exe"
    "/c/Program Files/Python312/python.exe"
    "/c/Program Files/Python311/python.exe"
    "/c/Program Files/Python310/python.exe"
)

try_python() {
    local cmd="$1"
    if command -v "$cmd" &>/dev/null || [ -f "$cmd" ]; then
        local MAJOR MINOR
        MAJOR=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null)
        MINOR=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)

        [[ -z "$MAJOR" || -z "$MINOR" ]] && return 1

        if [[ "$MAJOR" -lt 3 ]]; then
            return 1
        fi

        if [[ "$MAJOR" -eq 3 && "$MINOR" -lt 10 ]]; then
            print_warning "Found Python $MAJOR.$MINOR ($cmd) — too old (minimum: 3.10). Skipping."
            return 1
        fi

        if [[ "$MAJOR" -eq 3 && "$MINOR" -ge 14 ]]; then
            print_warning "Found Python $MAJOR.$MINOR ($cmd) — too new (maximum: 3.13). Skipping."
            return 1
        fi

        PYTHON_CMD="$cmd"
        PYTHON_VERSION="$MAJOR.$MINOR"
        return 0
    fi
    return 1
}

# Try generic candidates first
for cmd in "${GENERIC_CANDIDATES[@]}"; do
    if try_python "$cmd"; then
        break
    fi
done

# If not found and on Windows, try full paths
if [[ -z "$PYTHON_CMD" && "$OS" == "windows" ]]; then
    print_step "Searching for Python in common Windows installation paths..."
    for cmd in "${WINDOWS_CANDIDATES[@]}"; do
        if try_python "$cmd"; then
            break
        fi
    done
fi

if [[ -z "$PYTHON_CMD" ]]; then
    print_error "No compatible Python version found. Required: 3.10, 3.11, 3.12, or 3.13."
    echo ""
    echo "  Please install Python 3.12 (recommended) from:"
    echo "  https://www.python.org/downloads/"
    echo ""
    echo "  During installation, check 'Add Python to PATH'."
    echo "  After installing, close this terminal, open a new one, and run: bash setup.sh"
    exit 1
fi

print_success "Using Python $PYTHON_VERSION ($PYTHON_CMD)"


# ── Step 2: Create virtual environment ───────────────────────
print_header "Step 2 of 5 — Creating Virtual Environment"

if [ -d "venv" ]; then
    if [ -f "venv/pyvenv.cfg" ]; then
        VENV_VERSION=$(grep "version" venv/pyvenv.cfg | head -1 | awk '{print $3}' | cut -d. -f1,2)
        if [[ "$VENV_VERSION" != "$PYTHON_VERSION" ]]; then
            print_warning "Existing venv uses Python $VENV_VERSION but we need $PYTHON_VERSION. Recreating..."
            rm -rf venv
        else
            print_warning "Compatible venv already exists. Skipping creation."
        fi
    else
        print_warning "Found venv folder but could not verify its version. Recreating to be safe..."
        rm -rf venv
    fi
fi

if [ ! -d "venv" ]; then
    print_step "Creating virtual environment with Python $PYTHON_VERSION..."
    "$PYTHON_CMD" -m venv venv
    if [ $? -ne 0 ]; then
        print_error "Failed to create virtual environment."
        exit 1
    fi
    print_success "Virtual environment created."
fi

print_step "Activating virtual environment..."
if [[ "$OS" == "windows" ]]; then
    source venv/Scripts/activate
else
    source venv/bin/activate
fi

if [ $? -ne 0 ]; then
    print_error "Failed to activate virtual environment."
    exit 1
fi

print_success "Virtual environment activated."


# ── Step 3: Install Python dependencies ──────────────────────
print_header "Step 3 of 5 — Installing Python Dependencies"

print_step "Installing setuptools (required base dependency)..."
pip install setuptools --quiet --upgrade

print_step "Installing project dependencies from requirements.txt..."
pip install -r requirements.txt --quiet

if [ $? -ne 0 ]; then
    print_error "Dependency installation failed."
    echo ""
    echo "  Try running manually to see the full error:"
    echo "  pip install -r requirements.txt"
    exit 1
fi

print_success "All dependencies installed successfully."


# ── Step 4: Install Playwright browsers ──────────────────────
print_header "Step 4 of 5 — Installing Playwright Browser"

print_step "Downloading Chromium browser engine..."
echo "  (This may take 2–4 minutes on first run)"
echo ""

if [[ "$OS" == "linux" ]]; then
    playwright install --with-deps chromium
else
    playwright install chromium
fi

if [ $? -ne 0 ]; then
    print_error "Playwright browser installation failed."
    echo ""
    if [[ "$OS" == "linux" ]]; then
        echo "  Try running manually: playwright install --with-deps chromium"
    else
        echo "  Try running manually: playwright install chromium"
    fi
    exit 1
fi

print_success "Playwright Chromium installed successfully."


# ── Step 5: Create .env file ──────────────────────────────────
print_header "Step 5 of 5 — Setting Up Environment Variables"

if [ -f ".env" ]; then
    print_warning ".env file already exists. Skipping to avoid overwriting your credentials."
else
    if [ -f ".env.example" ]; then
        cp .env.example .env
        print_success ".env file created from .env.example template."
    else
        cat > .env << 'EOF'
# ============================================================
# LLM PROVIDERS
# ============================================================
GROQ_API_KEY=
GEMINI_API_KEY=
OPENAI_API_KEY=

# ============================================================
# EMAIL — GMAIL RELAY (Sender Account)
# ============================================================
NOTIFICATION_SENDER_EMAIL=
NOTIFICATION_SENDER_PASSWORD=

# ============================================================
# OVERLEAF / UNIVERSITY ACCOUNT (Receiver Account)
# ============================================================
OVERLEAF_EMAIL=
OVERLEAF_PASSWORD=
EOF
        print_success ".env file created from built-in template."
    fi
fi


# ── Final Summary ─────────────────────────────────────────────
print_header "Setup Complete!"

echo -e "${GREEN}${BOLD}Everything is installed and ready.${NC}"
echo ""
echo -e "${BOLD}Next steps:${NC}"
echo ""
echo "  1. Fill in your credentials:"
if [[ "$OS" == "windows" ]]; then
    echo "     Open .env in any text editor and fill in your API keys and email settings."
else
    echo -e "     ${YELLOW}nano .env${NC}   (or open .env in any text editor)"
fi
echo ""
echo "  2. Create your project mapping file (researchers_map.json):"
echo '     {'
echo '       "Your_Overleaf_Project_Name": "your.email@university.edu"'
echo '     }'
echo ""
echo "  3. Activate the virtual environment (required each new terminal session):"
if [[ "$OS" == "windows" ]]; then
    echo -e "     ${YELLOW}venv\\Scripts\\activate${NC}   (PowerShell)"
    echo -e "     ${YELLOW}source venv/Scripts/activate${NC}   (Git Bash)"
else
    echo -e "     ${YELLOW}source venv/bin/activate${NC}"
fi
echo ""
echo "  4. Run the first-time Overleaf login (opens a browser window):"
echo -e "     ${YELLOW}python main.py --agent ingestion${NC}"
echo ""
echo "  5. Run a test:"
echo -e "     ${YELLOW}python main.py --agent literature --project \"Your_Project_Name\"${NC}"
echo ""
echo "  For full instructions, see README.md"
echo ""