#!/usr/bin/env bash
# Provenance-checked production smoke wrapper around the read-only C++ gate.
set -euo pipefail

SOURCE="${BASH_SOURCE[0]}"
while [ -h "$SOURCE" ]; do
    SOURCE_DIR="$(CDPATH= cd -P -- "$(dirname -- "$SOURCE")" && pwd)"
    SOURCE="$(readlink -- "$SOURCE")"
    case "$SOURCE" in
        /*) ;;
        *) SOURCE="$SOURCE_DIR/$SOURCE" ;;
    esac
done
HERE="$(CDPATH= cd -P -- "$(dirname -- "$SOURCE")" && pwd)"
ESP32_RS="$(CDPATH= cd -P -- "$HERE/.." && pwd)"
REPO_ROOT="$(CDPATH= cd -P -- "$ESP32_RS/../../../.." && pwd)"
CPP_SMOKE="$(CDPATH= cd -P -- "$ESP32_RS/../esp32/tools" && pwd)/qemu_smoke.sh"

exec python3 "$HERE/artifact_provenance.py" \
    --repo-root "$REPO_ROOT" \
    exec --kind production -- \
    "$CPP_SMOKE" "$@"
