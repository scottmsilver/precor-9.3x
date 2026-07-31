#!/usr/bin/env bash
# Run every Rust QEMU gate while inheriting leases for both artifact bundles.
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

# The checked process owns both inherited shared-lock OFDs. Its Rust-only
# subprocess and final committed-harness exec therefore cannot observe a
# publisher generation change between the two test tiers.
exec python3 "$HERE/artifact_provenance.py" \
    --repo-root "$REPO_ROOT" \
    exec-many --kind production --kind qemu-test -- \
    bash -c '
        set -euo pipefail
        here="$1"
        shift
        harness="$here/qemu_harness"
        export IDF_IMAGE="${IDF_IMAGE:-esp32tap-rust:build}"

        if [ "${RS_SCENARIOS:-1}" = "1" ] && [ -z "${1:-}" ]; then
            echo "run_harness.sh: Rust-only scenarios (tools/qemu_scenarios) ..."
            (
                cd -- "$here/qemu_scenarios"
                exec python3 -m pytest . -v -p no:cacheprovider
            )
        fi

        cd -- "$harness"
        exec python3 -m pytest -m "not net" "$@" -p no:cacheprovider
    ' esp32tap-run-harness "$HERE" "$@"
