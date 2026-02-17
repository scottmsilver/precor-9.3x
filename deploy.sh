#!/usr/bin/env bash
set -euo pipefail

# cd to script's directory (project root)
cd "$(dirname "$0")"

PI_HOST="${PI_HOST:-rpi}"
VENV_DIR=".venv"

echo "=== Deploying to $PI_HOST ==="

# --- Python server + engine ---
echo "[1/5] Syncing Python files..."
scp server.py program_engine.py treadmill_client.py pyproject.toml "$PI_HOST":~/

# --- Static assets ---
echo "[2/5] Syncing static assets..."
ssh "$PI_HOST" 'mkdir -p ~/static/assets'
scp static/index.html "$PI_HOST":~/static/
# Clean old hashed assets, copy new ones
ssh "$PI_HOST" 'rm -f ~/static/assets/index-*'
scp static/assets/index-*.js static/assets/index-*.css "$PI_HOST":~/static/assets/

# --- Venv + deps ---
echo "[3/5] Ensuring venv and dependencies..."
ssh "$PI_HOST" bash -s "$VENV_DIR" <<'REMOTE'
VENV_DIR="$1"
if [ ! -d "$VENV_DIR" ]; then
    echo "  Creating venv..."
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install -q --upgrade pip
pip install -q google-genai fastapi uvicorn python-multipart gpxpy
REMOTE

# --- Restart server ---
echo "[4/5] Restarting server..."
ssh "$PI_HOST" bash -s "$VENV_DIR" <<'REMOTE'
VENV_DIR="$1"
pkill -f "python3 server.py" 2>/dev/null || true
sleep 1
source "$VENV_DIR/bin/activate"
nohup python3 server.py > /tmp/server.log 2>&1 &
sleep 2
if pgrep -f "python3 server.py" > /dev/null; then
    echo "  Server running (PID $(pgrep -f 'python3 server.py'))"
else
    echo "  ERROR: Server failed to start. Check /tmp/server.log"
    tail -5 /tmp/server.log
    exit 1
fi
REMOTE

echo "[5/5] Done!"
echo "  UI: http://$PI_HOST:8000"
