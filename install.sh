#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# ReadOS — Installation Script
# Supports: Debian/Ubuntu Linux (including Kobo Linux base)
# Usage: bash install.sh
# ═══════════════════════════════════════════════════════════════

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ReadOS Installation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is required. Install with: sudo apt install python3"
    exit 1
fi

PYTHON=$(command -v python3)
PIP=$(command -v pip3 || command -v pip)

echo "→ Python: $($PYTHON --version)"

# Create virtualenv if not exists
if [ ! -d "venv" ]; then
    echo "→ Creating virtual environment..."
    $PYTHON -m venv venv
fi

source venv/bin/activate
PIP="venv/bin/pip"

echo "→ Installing dependencies..."
$PIP install --upgrade pip -q
$PIP install -r requirements.txt -q

# Create required directories
echo "→ Creating directories..."
mkdir -p books cache/covers tmp logs

# Generate secret key if config not set
if grep -q "REPLACE_WITH_RANDOM_SECRET_KEY" config.yaml 2>/dev/null; then
    KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
    sed -i "s/REPLACE_WITH_RANDOM_SECRET_KEY/$KEY/" config.yaml
    echo "→ Generated secret key"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✓ Installation complete!"
echo ""
echo "  To start ReadOS:"
echo "    ./start.sh"
echo ""
echo "  Or manually:"
echo "    source venv/bin/activate"
echo "    python app.py"
echo ""
echo "  Then open: http://localhost:8080"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
