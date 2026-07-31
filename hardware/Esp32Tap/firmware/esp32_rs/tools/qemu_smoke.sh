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
    bash -c '
        set -euo pipefail
        esp32_rs=$1
        repo_root=$2
        cpp_smoke=$3
        shift 3

        firmware_dir="$(CDPATH= cd -P -- "$esp32_rs/.." && pwd)"
        [ "$firmware_dir" = "$repo_root/hardware/Esp32Tap/firmware" ] || {
            echo "qemu_smoke: unsafe firmware workspace parent" >&2
            exit 1
        }
        bundle="$(readlink -f -- "$esp32_rs/build")"
        case "$bundle" in
            "$esp32_rs"/.artifacts/prod/*) ;;
            *)
                echo "qemu_smoke: production bundle resolved outside artifact store" >&2
                exit 1
                ;;
        esac
        [ -d "$bundle" ] && [ ! -L "$bundle" ] || {
            echo "qemu_smoke: production bundle is not a physical directory" >&2
            exit 1
        }

        smoke_root="$(mktemp -d -- "$firmware_dir/.esp32tap-smoke.XXXXXXXX")"
        cleanup() {
            case "${smoke_root:-}" in
                "$firmware_dir"/.esp32tap-smoke.*) ;;
                *) return ;;
            esac
            [ -d "$smoke_root" ] && [ ! -L "$smoke_root" ] || return
            rm -rf -- "$smoke_root"
        }
        trap cleanup EXIT
        trap "exit 129" HUP
        trap "exit 130" INT
        trap "exit 143" TERM
        [ "$(stat -c %u -- "$smoke_root")" = "$(id -u)" ] &&
            [ "$(stat -c %a -- "$smoke_root")" = 700 ] || {
                echo "qemu_smoke: unsafe private workspace" >&2
                exit 1
            }
        mkdir -m 700 -- "$smoke_root/build" "$smoke_root/tools"

        for member in \
            esp32tap.bin bootloader.bin partition-table.bin flash_args sdkconfig
        do
            source_member="$bundle/$member"
            target_member="$smoke_root/build/$member"
            [ -f "$source_member" ] && [ ! -L "$source_member" ] || {
                echo "qemu_smoke: unsafe production member: $member" >&2
                exit 1
            }
            cp -f -- "$source_member" "$target_member"
            chmod 0444 -- "$target_member"
            cmp -s -- "$source_member" "$target_member" || {
                echo "qemu_smoke: private copy mismatch: $member" >&2
                exit 1
            }
        done
        [ "$(find "$smoke_root/build" -mindepth 1 -maxdepth 1 -printf . | wc -c)" = 5 ] || {
            echo "qemu_smoke: private build has unexpected members" >&2
            exit 1
        }

        # The separately HEAD-anchored C++ gate derives ESP32_DIR from $0.
        # Give it a synthetic private tools path while sourcing its exact bytes.
        bash -c "source \"\$1\" \"\${@:2}\"" \
            "$smoke_root/tools/qemu_smoke.sh" "$cpp_smoke" "$@"
    ' "$SOURCE" "$ESP32_RS" "$REPO_ROOT" "$CPP_SMOKE" "$@"
