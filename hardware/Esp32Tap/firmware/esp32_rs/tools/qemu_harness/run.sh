#!/usr/bin/env bash
# Checked compatibility entrypoint for the Rust behavioral harness.
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
HARNESS_DIR="$(CDPATH= cd -P -- "$(dirname -- "$SOURCE")" && pwd)"
TOOLS_DIR="$(CDPATH= cd -P -- "$HARNESS_DIR/.." && pwd)"
ESP32_RS="$(CDPATH= cd -P -- "$TOOLS_DIR/.." && pwd)"
REPO_ROOT="$(CDPATH= cd -P -- "$ESP32_RS/../../../.." && pwd)"

# Verify before delegation even when this historical entrypoint is invoked
# directly. The delegated wrapper repeats the check under inherited shared
# locks, keeping both generations continuously leased across its two tiers.
exec python3 "$TOOLS_DIR/artifact_provenance.py" \
    --repo-root "$REPO_ROOT" \
    exec-many --kind production --kind qemu-test -- \
    "$TOOLS_DIR/run_harness.sh" "$@"
