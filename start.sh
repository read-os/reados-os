#!/usr/bin/env bash
# ReadOS — Start Script
# Usage: ./start.sh [port] [--debug]

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT=${1:-8080}
DEBUG=${READOS_DEBUG:-false}

if [ "$2" == "--debug" ]; then
    DEBUG=true
fi

# Activate venv if present
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Use gunicorn for production if available, else flask dev server
if command -v gunicorn &>/dev/null && [ "$DEBUG" != "true" ]; then
    echo "Starting ReadOS on http://0.0.0.0:$PORT (gunicorn)"
    exec gunicorn app:create_app\(\) \
        --bind "0.0.0.0:$PORT" \
        --workers 2 \
        --threads 4 \
        --timeout 120 \
        --access-logfile logs/access.log \
        --error-logfile logs/error.log \
        --log-level info \
        --preload
else
    echo "Starting ReadOS on http://0.0.0.0:$PORT (flask dev)"
    READOS_PORT=$PORT READOS_DEBUG=$DEBUG exec python3 app.py
fi
