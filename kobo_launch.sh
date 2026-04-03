#!/bin/sh
# ═══════════════════════════════════════════════════════════════
# ReadOS — Kobo Launcher
# Place this script in /mnt/onboard/.adds/reados/
# Compatible with KFMon + NickelMenu
# ═══════════════════════════════════════════════════════════════

READOS_DIR="/mnt/onboard/.adds/reados"
LOGFILE="$READOS_DIR/reados.log"
PORT=8080

cd "$READOS_DIR"

# Use system Python or bundled
PYTHON=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON" ]; then
    echo "Python not found" >> "$LOGFILE"
    exit 1
fi

# Start ReadOS in background
export READOS_PORT=$PORT
export READOS_BOOKS_DIR="/mnt/onboard/books"
export READOS_DB_PATH="$READOS_DIR/reados.db"
export READOS_CACHE_DIR="$READOS_DIR/cache"

$PYTHON "$READOS_DIR/app.py" >> "$LOGFILE" 2>&1 &
READOS_PID=$!
echo "ReadOS started (PID $READOS_PID) on port $PORT" >> "$LOGFILE"

# Wait for server to start
sleep 3

# Open browser (Kobo uses Qt WebEngine or similar)
if command -v xdg-open > /dev/null 2>&1; then
    xdg-open "http://localhost:$PORT"
elif command -v chromium-browser > /dev/null 2>&1; then
    chromium-browser --app="http://localhost:$PORT" --kiosk &
elif [ -f /usr/local/Kobo/Qt-linux-x11-Qt5.4/bin/qmlscene ]; then
    # Kobo Elipsa / Sage browser launch
    /usr/local/Kobo/Qt-linux-x11-Qt5.4/bin/qmlscene \
        -I /usr/local/Kobo/qml \
        /usr/local/Kobo/qml/ReadOS/browser.qml \
        "http://localhost:$PORT" &
fi

# Trap signals for clean shutdown
trap "kill $READOS_PID 2>/dev/null; exit 0" INT TERM
wait $READOS_PID
