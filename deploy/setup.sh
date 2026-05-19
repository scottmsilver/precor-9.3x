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
# Make a freshly-provisioned, minimal Pi OS converge with a fully-loaded one:
# install only what's missing (idempotent) so the operator never hand-runs
# apt. A bare image typically lacks these; a long-lived box already has them.
#   - python3 + venv/pip : treadmill-server
#   - libpigpio1         : the runtime shared lib treadmill_io dynamically
#                          links (libpigpio.so.1)
#   - rsync              : used by the deploy path
#   - openssl            : per-device self-signed TLS cert (HTTPS)
#   - avahi-daemon       : publishes the _treadmill._tcp mDNS service
# Works on any Debian-based Pi OS whose apt provides libpigpio1 (DietPi and
# Raspberry Pi OS both pull 1.79-1+rpt1 from archive.raspberrypi.com). Must
# precede the venv step and the treadmill_io restart.
need=""
command -v python3 >/dev/null 2>&1 || need="$need python3 python3-venv python3-pip"
if ! { [ -e /usr/lib/libpigpio.so.1 ] || [ -e /lib/libpigpio.so.1 ] \
       || [ -e /usr/lib/aarch64-linux-gnu/libpigpio.so.1 ]; }; then
  need="$need libpigpio1"
fi
command -v rsync >/dev/null 2>&1 || need="$need rsync"
command -v openssl >/dev/null 2>&1 || need="$need openssl"
[ -x /usr/sbin/avahi-daemon ] || need="$need avahi-daemon"
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
sudo systemctl enable --now avahi-daemon 2>/dev/null || true

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

# --- TLS cert (per-device self-signed, generated on this host) --------------
# The treadmill is a personal LAN appliance: no CA, no internet dependency.
# Generate a self-signed cert once, on-device, so a freshly commissioned Pi
# serves HTTPS out of the box — the iOS app's ATS hard-blocks cleartext, and
# Android trusts this cert via a trust-all manager. The private key is a
# per-device secret: gitignored and rsync-excluded (deploy.sh --exclude
# '*.pem'), so a normal redeploy never clobbers or regenerates it. cwd here ==
# server.py WorkingDirectory, and server.py reads cert.pem/key.pem relative to
# it. Idempotent: (re)generate only if the key is missing or the cert is
# absent/expired — an existing valid per-device cert is preserved.
if [ ! -f key.pem ] || ! openssl x509 -in cert.pem -noout -checkend 0 >/dev/null 2>&1; then
  HOST_SHORT="$(hostname -s)"
  PRIMARY_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  SAN="DNS:${HOST_SHORT},DNS:${HOST_SHORT}.local,DNS:localhost,IP:127.0.0.1"
  [ -n "$PRIMARY_IP" ] && SAN="${SAN},IP:${PRIMARY_IP}"
  echo "Generating per-device self-signed TLS cert (CN=${HOST_SHORT}, SAN=${SAN})..."
  rm -f cert.pem key.pem ts-cert.pem ts-key.pem
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout key.pem -out cert.pem -days 3650 \
    -subj "/CN=${HOST_SHORT}" -addext "subjectAltName=${SAN}"
  chmod 600 key.pem
  chmod 644 cert.pem
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
