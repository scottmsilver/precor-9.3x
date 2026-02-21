#!/usr/bin/env bash
set -euo pipefail

# cd to project root (parent of deploy/)
cd "$(dirname "$0")/.."

PI_HOST="${PI_HOST:-rpi}"
PI_DIR="${PI_DIR:-treadmill}"
PI_USER="${PI_USER:-$(ssh "$PI_HOST" whoami)}"
VENV_DIR="${VENV_DIR:-.venv}"

render_service() {
    sed -e "s|@USER@|$PI_USER|g" \
        -e "s|@DEPLOY_DIR@|$PI_DIR|g" \
        -e "s|@VENV_DIR@|$VENV_DIR|g" \
        "$1"
}

stage() {
    echo "=== Staging build/ ==="
    rm -rf build && mkdir -p build/services build/static

    # C++ source + build system (built on Pi via `make -C src`)
    cp -r src/ build/src/
    cp -r third_party/ build/third_party/
    cp gpio.json build/

    # Python
    cp server.py workout_session.py program_engine.py \
       treadmill_client.py hrm_client.py pyproject.toml build/

    # Setup script
    cp deploy/setup.sh build/
    chmod +x build/setup.sh

    # UI
    echo "Building UI..."
    rm -rf static/assets && mkdir -p static/assets
    (cd ui && npx vite build)
    cp -r static/index.html static/assets build/static/

    # FTMS binary (if cross-compiled)
    FTMS_BIN="ftms/target/aarch64-unknown-linux-gnu/release/ftms-daemon"
    if [ -f "$FTMS_BIN" ]; then
        cp "$FTMS_BIN" build/
    fi

    # HRM binary (if cross-compiled)
    HRM_BIN="hrm/target/aarch64-unknown-linux-gnu/release/hrm-daemon"
    if [ -f "$HRM_BIN" ]; then
        cp "$HRM_BIN" build/
    fi

    # Render service templates
    for tmpl in deploy/*.service.in; do
        name=$(basename "$tmpl" .in)
        render_service "$tmpl" > "build/services/$name"
    done

    echo "Staged to build/"
}

deploy_full() {
    stage

    echo "=== Deploying to $PI_HOST:~/$PI_DIR ==="
    ssh "$PI_HOST" "mkdir -p ~/$PI_DIR"
    rsync -az --delete \
        --exclude='*.o' --exclude='*.d' --exclude='*.test.o' \
        --exclude='.gemini_key' --exclude='*.pem' \
        --exclude='program_history.json' --exclude='__pycache__' \
        build/ "$PI_HOST":~/$PI_DIR/

    # Build C binary on Pi
    echo "Building on Pi..."
    ssh "$PI_HOST" "cd ~/$PI_DIR && make -C src"

    # Run setup (installs binaries, services, restarts)
    echo "Running setup..."
    ssh "$PI_HOST" "cd ~/$PI_DIR && bash setup.sh"

    echo "Done!"
    echo "  Services: sudo systemctl status treadmill-io treadmill-server ftms hrm"
    echo "  UI: https://$PI_HOST:8000"
}

deploy_ui() {
    echo "=== Deploying UI to $PI_HOST ==="
    rm -rf static/assets && mkdir -p static/assets build/static
    (cd ui && npx vite build)
    cp -r static/index.html static/assets build/static/

    ssh "$PI_HOST" "rm -rf ~/$PI_DIR/static/assets && mkdir -p ~/$PI_DIR/static/assets"
    rsync -az build/static/ "$PI_HOST":~/$PI_DIR/static/
    echo "Done! UI deployed."
}

case "${1:-}" in
    ui)
        deploy_ui
        ;;
    --stage-only)
        stage
        ;;
    *)
        deploy_full
        ;;
esac
