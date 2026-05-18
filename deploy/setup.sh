#!/usr/bin/env bash
# setup.sh — runs on the Pi after deploy rsync. Installs strictly from
# deploy/manifest.txt, wires treadmill_io into Path A, applies the 512MB
# trim ladder (zram thin margin), and restarts with treadmill_io LAST.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck source=/dev/null
source ./lib-artifacts.sh

USER_NAME="$(whoami)"

# Clean up the legacy underscore-named unit.
sudo systemctl disable --now treadmill_io 2>/dev/null || true
sudo rm -f /etc/systemd/system/treadmill_io.service

# --- OS runtime prerequisites -----------------------------------------------
# A minimal/provisioned DietPi lacks these; the production Raspberry Pi OS box
# has them pre-installed (the plan wrongly assumed them present). Install only
# what's missing (idempotent): python3 + venv/pip for treadmill-server, and
# libpigpio1 — the runtime shared library treadmill_io dynamically links
# (libpigpio.so.1). The Pi's DietPi apt includes archive.raspberrypi.com, so
# libpigpio1 resolves to the same 1.79-1+rpt1 production runs. Must precede
# the venv step and the treadmill_io restart.
need=""
command -v python3 >/dev/null 2>&1 || need="$need python3 python3-venv python3-pip"
if ! { [ -e /usr/lib/libpigpio.so.1 ] || [ -e /lib/libpigpio.so.1 ] \
       || [ -e /usr/lib/aarch64-linux-gnu/libpigpio.so.1 ]; }; then
  need="$need libpigpio1"
fi
command -v rsync >/dev/null 2>&1 || need="$need rsync"
if [ -n "$need" ]; then
  echo "Installing OS prerequisites:$need"
  sudo apt-get update -qq
  # shellcheck disable=SC2086
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends $need
fi

# --- Manifest-driven install ------------------------------------------------
# Deploy payload = the staged build/ dir flattened into THIS directory
# (deploy.sh rsyncs 'build/' -> ~/treadmill/; the bake ACS does
# 'cp -r build/.' -> ~/treadmill/). Manifest 'src' is repo-relative
# (build/<X>), so strip the staging-root prefix to resolve it here. Rows
# whose final dest IS the payload location (the ~/treadmill app tree:
# python/, static/, gpio.json, pyproject.toml) are already in place from
# that flatten — detected as an identity path and skipped. The rows that do
# real work are the binaries (-> /usr/local/bin) and unit files
# (-> /etc/systemd/system). A manifest src missing from the payload is a
# hard failure (never install a half/stale tree).
manifest_rows ./manifest.txt | while IFS=$'\t' read -r kind src dest mode owner; do
  srcfile="${src#build/}"
  [ -e "$srcfile" ] || { echo "setup: manifest src missing in payload: $srcfile (from $src)" >&2; exit 1; }
  rdest=$(manifest_resolve_dest "$dest" "$USER_NAME")
  abs_src=$(realpath -m -- "$srcfile")
  case "$kind" in
    bin|unit|file)
      tgt="$rdest"
      case "$rdest" in */) tgt="$rdest$(basename "$srcfile")" ;; esac
      abs_tgt=$(realpath -m -- "$tgt")
      if [ "$abs_src" = "$abs_tgt" ]; then
        echo "setup: $srcfile already in place ($tgt) — skip"; continue
      fi
      if [ "$owner" = root ]; then
        sudo install -D -m "$mode" "$srcfile" "$tgt"
      else
        install -D -m "$mode" "$srcfile" "$tgt"
      fi
      ;;
    tree)
      abs_tgt=$(realpath -m -- "$rdest")
      if [ "$abs_src" = "$abs_tgt" ]; then
        echo "setup: tree $srcfile already in place ($rdest) — skip"; continue
      fi
      mkdir -p "$rdest"
      rsync -a --delete "$srcfile" "$rdest"
      ;;
  esac
done

# --- Path A wiring: treadmill_io must start network-independently early ------
sudo systemctl daemon-reload
sudo systemctl enable treadmill-io treadmill-server
if systemctl list-unit-files | grep -q '^treadmill-critical.target'; then
  sudo systemctl add-wants treadmill-critical.target treadmill-io.service 2>/dev/null || true
fi
[ -x /usr/local/bin/ftms-daemon ] && sudo systemctl enable ftms || true
[ -x /usr/local/bin/hrm-daemon ]  && sudo systemctl enable hrm  || true

# --- Trim ladder step 4: zram thin margin (compressed RAM swap, no SD wear) --
if ! systemctl is-enabled systemd-zram-setup@zram0.service >/dev/null 2>&1; then
  sudo apt-get update -qq 2>/dev/null || true
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       systemd-zram-generator 2>/dev/null || \
    logger -t treadmill-setup "zram-generator install failed (no swap margin)"
  printf '[zram0]\nzram-size = ram / 4\ncompression-algorithm = zstd\n' \
    | sudo tee /etc/systemd/zram-generator.conf >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl start systemd-zram-setup@zram0.service 2>/dev/null || true
fi

# --- TLS cert (Tailscale, host-agnostic — derives name from this host) ------
TS_DOMAIN=$(tailscale status --json 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))" 2>/dev/null || true)
if [ -n "$TS_DOMAIN" ]; then
  if sudo tailscale cert "$TS_DOMAIN"; then
    sudo cp "$HOME/$TS_DOMAIN.crt" ts-cert.pem
    sudo cp "$HOME/$TS_DOMAIN.key" ts-key.pem
    sudo chown "$USER_NAME:$USER_NAME" ts-cert.pem ts-key.pem
    ln -sf ts-cert.pem cert.pem
    ln -sf ts-key.pem key.pem
  else
    echo "WARNING: TLS cert generation failed — server will run without HTTPS"
  fi
fi

# --- Venv: minimal deps only (trim ladder step 1) ---------------------------
VENV_DIR="$HOME/.venv"
[ -d "$VENV_DIR" ] || python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q --upgrade pip
"$VENV_DIR/bin/pip" install -q google-genai fastapi uvicorn python-multipart gpxpy

# --- Ordered atomic restart: server/ftms/hrm FIRST, treadmill_io LAST -------
# treadmill_io owns the safety logic (3h timeout, zero-on-emulate). Restart
# it last and atomically so its downtime is minimal and never overlaps an
# emulating belt.
echo "Restarting services (treadmill_io last)..."
sudo systemctl restart treadmill-server
[ -x /usr/local/bin/ftms-daemon ] && sudo systemctl restart ftms || true
[ -x /usr/local/bin/hrm-daemon ]  && sudo systemctl restart hrm  || true
sudo systemctl restart treadmill-io
echo "Done! Services restarted (treadmill_io restarted last)."
