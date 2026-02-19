#!/usr/bin/env bash
# setup.sh — runs on the Pi after deploy rsync
# Installs binaries, services, venv, and restarts everything
set -euo pipefail

cd "$(dirname "$0")"

# Install C binary (built by `make -C src` into build/)
echo "Installing treadmill_io..."
sudo install -m 755 build/treadmill_io /usr/local/bin/

# Install FTMS if present
if [ -f ftms-daemon ]; then
    echo "Installing ftms-daemon..."
    sudo install -m 755 ftms-daemon /usr/local/bin/
fi

# Clean up old underscore-named service
sudo systemctl disable --now treadmill_io 2>/dev/null || true
sudo rm -f /etc/systemd/system/treadmill_io.service

# Install services
for svc in services/*.service; do
    sudo cp "$svc" /etc/systemd/system/
done
sudo systemctl daemon-reload
sudo systemctl enable treadmill-io treadmill-server

# FTMS only if binary was deployed
if [ -f ftms-daemon ]; then
    sudo systemctl enable ftms
fi

# TLS certs (Tailscale — auto-renewed on each deploy)
TS_DOMAIN=$(sudo tailscale cert --help >/dev/null 2>&1 && tailscale status --json | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
if [ -n "$TS_DOMAIN" ]; then
    echo "Generating TLS cert for $TS_DOMAIN..."
    sudo tailscale cert "$TS_DOMAIN"
    sudo cp "$HOME/$TS_DOMAIN.crt" ts-cert.pem
    sudo cp "$HOME/$TS_DOMAIN.key" ts-key.pem
    sudo chown "$(whoami):$(whoami)" ts-cert.pem ts-key.pem
    ln -sf ts-cert.pem cert.pem
    ln -sf ts-key.pem key.pem
fi

# Venv + deps
VENV_DIR="$HOME/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating venv..."
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q google-genai fastapi uvicorn python-multipart gpxpy

# Restart services
echo "Restarting services..."
sudo systemctl restart treadmill-io treadmill-server
if [ -f ftms-daemon ]; then
    sudo systemctl restart ftms
fi

echo "Done! Services restarted."
