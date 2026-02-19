#!/usr/bin/env bash
set -euo pipefail

# cd to script's directory (project root)
cd "$(dirname "$0")"

PI_HOST="${PI_HOST:-rpi}"
PI_DIR="treadmill"          # deploy target on Pi: ~/treadmill/
VENV_DIR=".venv"

deploy_ui() {
    echo "=== Deploying UI to $PI_HOST ==="
    # Clean local stale assets before building
    rm -rf static/assets && mkdir -p static/assets
    (cd ui && npx vite build)
    ssh "$PI_HOST" "rm -rf ~/$PI_DIR/static/assets && mkdir -p ~/$PI_DIR/static/assets"
    scp static/index.html "$PI_HOST":~/$PI_DIR/static/
    scp static/assets/index-*.js static/assets/index-*.css "$PI_HOST":~/$PI_DIR/static/assets/
    echo "Done! UI deployed."
}

if [ "${1:-}" = "ui" ]; then
    deploy_ui
    exit 0
fi

echo "=== Deploying to $PI_HOST:~/$PI_DIR ==="

# Ensure target directory exists
ssh "$PI_HOST" "mkdir -p ~/$PI_DIR"

# --- treadmill_io C binary (built on Pi, needs libpigpio) ---
echo "[1/6] Building and deploying treadmill_io..."
rsync -az src/ "$PI_HOST":~/$PI_DIR/src/
rsync -az third_party/ "$PI_HOST":~/$PI_DIR/third_party/
scp Makefile gpio.json "$PI_HOST":~/$PI_DIR/
ssh "$PI_HOST" "cd ~/$PI_DIR && make"
scp treadmill_io.service "$PI_HOST":/tmp/treadmill_io.service
ssh "$PI_HOST" 'sudo systemctl stop treadmill_io 2>/dev/null || true; sudo pkill -9 treadmill_io 2>/dev/null || true; sleep 1'
ssh "$PI_HOST" "sudo install -m 755 ~/$PI_DIR/treadmill_io /usr/local/bin/ && sudo cp /tmp/treadmill_io.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now treadmill_io"

# --- Python server + engine ---
echo "[2/6] Syncing Python files..."
scp server.py workout_session.py program_engine.py treadmill_client.py pyproject.toml "$PI_HOST":~/$PI_DIR/

# --- Static assets ---
echo "[3/6] Syncing static assets..."
deploy_ui

# --- Venv + deps ---
echo "[4/6] Ensuring venv and dependencies..."
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

# --- Restart server (kill legacy nohup if present) ---
echo "[5/6] Restarting server..."
ssh "$PI_HOST" 'pkill -f "python3 server.py" 2>/dev/null || true; sleep 1'
scp treadmill-server.service "$PI_HOST":/tmp/treadmill-server.service
ssh "$PI_HOST" 'sudo cp /tmp/treadmill-server.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now treadmill-server && sudo systemctl restart treadmill-server'

# --- Optional: FTMS Bluetooth daemon ---
FTMS_BIN="ftms/target/aarch64-unknown-linux-gnu/release/ftms-daemon"
if [ -f "$FTMS_BIN" ]; then
    echo "[6/6] Deploying FTMS daemon..."
    ssh "$PI_HOST" 'sudo systemctl stop ftms 2>/dev/null || true; sleep 1'
    scp "$FTMS_BIN" "$PI_HOST":/tmp/ftms-daemon
    scp ftms/ftms.service "$PI_HOST":/tmp/ftms.service
    ssh "$PI_HOST" 'sudo install -m 755 /tmp/ftms-daemon /usr/local/bin/ && sudo cp /tmp/ftms.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now ftms'
else
    echo "[6/6] Skipping FTMS daemon (not built)"
fi

echo "Done!"
echo "  Services: sudo systemctl status treadmill_io treadmill-server ftms"
echo "  UI: http://$PI_HOST:8000"
