#!/usr/bin/env bash
# build.sh — reproducible containerized Rust build for the Esp32Tap ESP32-S3
# safety core.
#
# Produces, under esp32_rs/build/ and esp32_rs/build_qemu_test/ — the EXACT
# layout tools/qemu_smoke.sh and tools/qemu_harness/ expect, at the same
# nesting depth as the C++ tree, so both gates run completely unmodified:
#   esp32tap.bin        the app image
#   bootloader.bin      IDF second-stage bootloader
#   partition-table.bin generated from partitions_esp32tap.csv
#   flash_args          esptool merge_bin argfile
#
# The repo is ALWAYS bind-mounted at /project so the absolute
# CONFIG_PARTITION_TABLE_CUSTOM_FILENAME in sdkconfig.defaults resolves.
#
# The prod and qemu-test images use SEPARATE CARGO_TARGET_DIRs so flipping the
# feature does not invalidate the other image.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ESP32_RS="$(cd "$HERE/.." && pwd)"
REPO_ROOT="$(cd "$ESP32_RS/../../../.." && pwd)"
REL="${ESP32_RS#"$REPO_ROOT"/}/esp32tap"
IMAGE="${RUST_IMAGE:-esp32tap-rust:build}"
CARGO_CACHE="${CARGO_CACHE:-/tmp/rustcargo}"
PROFILE="${PROFILE:-release}"
# ONLY=prod or ONLY=qemu to build a single image.
ONLY="${ONLY:-both}"

mkdir -p "$CARGO_CACHE"

# --- host-side source gates (fail BEFORE spending 10 min in the container) ---
#
# check_unsafe_budget.py is the REAL enforcement of the unsafe containment.
# `#![deny(unsafe_code)]` at the crate root is a lint level any module can lift
# for itself with an inner `#[allow(unsafe_code)]`, so it never enforced the
# claimed containment (proven by counterexample: qemu_test/ does contain
# unsafe). safety_core and the unsafe-free firmware modules now use `forbid`,
# which the compiler does enforce; this script covers what `forbid` cannot —
# the allowlist of unsafe-bearing files, the allowlist of `allow` sites, a
# SAFETY comment on every unsafe block, and the exact line budget.
echo "== host gates =="
python3 "$HERE/check_unsafe_budget.py"
python3 "$HERE/check_case_parity.py"
python3 "$HERE/check_pins.py"
python3 "$HERE/check_wdt_chain.py"

docker run --rm \
    -v "$REPO_ROOT":/project \
    -v "$CARGO_CACHE":/cargo \
    -e CARGO_HOME=/cargo \
    -e PROFILE="$PROFILE" \
    -e NET_FEATURE="${NET_FEATURE:-}" \
    -e ONLY="$ONLY" \
    -w "/project/$REL" \
    "$IMAGE" bash -lc '
set -euo pipefail
source /opt/rust/esp-export.sh >/dev/null 2>&1
source /opt/esp/idf/export.sh  >/dev/null 2>&1

# Pinning IDF to v5.5.4 moves qemu-xtensa off the exported PATH. The
# esp_develop_9.2.2_20260417 build the C++ gate uses is still on disk — put it
# back so BOTH gates run the same emulator.
export PATH="$PATH:$(dirname "$(ls /opt/esp/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa | head -1)")"

echo "== toolchain =="
echo "idf       $(git -C /opt/esp/idf describe --tags)"
echo "rustc     $(rustc +esp --version)"
echo "qemu      $(qemu-system-xtensa --version | head -1)"

build_one() {
    local outdir="$1"; shift
    local tgtdir="$1"; shift
    local extra=("$@")

    echo "== cargo build ${extra[*]:-（prod)} -> $outdir =="
    # The TEST image additionally applies sdkconfig.defaults.qemu, exactly as
    # the C++ run.sh does with
    #   -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.qemu".
    # It enables the OpenCores MAC, disables interrupt-driven hardware MPI
    # (which Guru-Meditates under this QEMU), and turns SILENT_REBOOT into
    # PANIC_PRINT_REBOOT so a harness dump shows WHY a guest died. Production
    # never sees these keys.
    local RS_DIR=/project/hardware/Esp32Tap/firmware/esp32_rs
    local SDK="$RS_DIR/sdkconfig.defaults"
    if [ "$outdir" = "../build_qemu_test" ]; then
        SDK="$SDK;$RS_DIR/sdkconfig.defaults.qemu"
    fi
    ESP_IDF_SDKCONFIG_DEFAULTS="$SDK" \
    CARGO_TARGET_DIR="$tgtdir" cargo +esp build --profile "$PROFILE" "${extra[@]}" 2>&1 \
        | grep -vE "^\s+(Compiling|Downloaded|Checking) " | tail -25

    local T="$tgtdir/xtensa-esp32s3-espidf/$PROFILE"

    # The mandated hard gate on the generated sdkconfig. Located BEFORE
    # elf2image because the image header flash size is derived from it.
    local sdk
    sdk="$(ls -d "$tgtdir"/xtensa-esp32s3-espidf/"$PROFILE"/build/../../../*/out/sdkconfig 2>/dev/null | head -1 || true)"
    if [ -z "$sdk" ]; then
        sdk="$(find "$tgtdir" -name sdkconfig -path "*esp-idf-sys*" | head -1 || true)"
    fi
    [ -n "$sdk" ] || { echo "FATAL: generated sdkconfig not found; the mandated gate cannot run"; exit 1; }

    # SINGLE SOURCE OF TRUTH for the flash size. It was hard-coded to 8MB
    # while esp-idf-sys generated CONFIG_ESPTOOLPY_FLASHSIZE="2MB" (the
    # custom partition table does not apply under esp-idf-sys, so the build
    # is a 2MB single-app one). A header that claims MORE flash than the part
    # actually has makes IDF spi_flash init abort and reboot forever
    # ("Detected size(2048k) smaller than the size in the binary image
    # header(8192k). Probe failed.") — which is exactly the boot loop this
    # image had. Derive it, never assume it.
    local FLASH_SIZE
    FLASH_SIZE="$(grep -E ^CONFIG_ESPTOOLPY_FLASHSIZE= "$sdk" | head -1 | cut -d\" -f2)"
    [ -n "$FLASH_SIZE" ] || { echo "FATAL: CONFIG_ESPTOOLPY_FLASHSIZE missing from $sdk"; exit 1; }
    echo "flash size (from generated sdkconfig): $FLASH_SIZE"

    rm -rf "$outdir"; mkdir -p "$outdir"
    cp "$T/bootloader.bin"      "$outdir/bootloader.bin"
    cp "$T/partition-table.bin" "$outdir/partition-table.bin"
    python -m esptool --chip esp32s3 elf2image \
        --flash_mode dio --flash_freq 80m --flash_size "$FLASH_SIZE" \
        -o "$outdir/esp32tap.bin" "$T/esp32tap"

    cat > "$outdir/flash_args" <<ARGS
--flash_mode dio --flash_freq 80m --flash_size $FLASH_SIZE
0x0 bootloader.bin
0x8000 partition-table.bin
0x10000 esp32tap.bin
ARGS

    # Prove the header we just wrote agrees with the configured part, so a
    # future divergence fails the BUILD instead of silently boot-looping in
    # QEMU (or, worse, only on the bench).
    python -m esptool --chip esp32s3 image_info --version 2 "$outdir/esp32tap.bin" 2>/dev/null \
        | grep -qi "^Flash size: *$FLASH_SIZE\$" \
        || { echo "FATAL: $outdir/esp32tap.bin header flash size != $FLASH_SIZE"; exit 1; }

    echo "-- $outdir --"
    ls -l "$outdir"
    echo "app image: $(stat -c%s "$outdir/esp32tap.bin") bytes (factory partition = 2097152)"

    {
        # The mandated grep, kept verbatim as the first line of defence...
        grep -q "CONFIG_ESP_TASK_WDT_PANIC=y" "$sdk" \
            || { echo "FATAL: CONFIG_ESP_TASK_WDT_PANIC=y missing from $sdk"; exit 1; }
        grep -q "CONFIG_FREERTOS_HZ=1000" "$sdk" \
            || { echo "FATAL: CONFIG_FREERTOS_HZ=1000 missing from $sdk"; exit 1; }
        # ...and then the REAL gate: build_safety_manifest.py'"'"'s own
        # REQUIRED / FORBIDDEN / selector rules, imported (not duplicated) by
        # tools/check_sdkconfig.py. Two hand-picked greps are a SUBSET of the
        # mandated gate and the subset has a hole: CONFIG_ESP_DEBUG_OCDAWARE
        # defaults to =y and is PLAN-forbidden in an Emulate-capable build.
        local qemu_flag=()
        [ "$outdir" = "../build_qemu_test" ] && qemu_flag=(--allow-qemu)
        python3 "$RS_DIR/tools/check_sdkconfig.py" "$sdk" \
            --label "$(basename "$outdir")" "${qemu_flag[@]}" \
            || { echo "FATAL: generated sdkconfig fails the safety-manifest rules"; exit 1; }
        cp "$sdk" "$outdir/sdkconfig"
    }
}

if [ "$ONLY" != "qemu" ]; then
    build_one ../build target/prod
fi
if [ "$ONLY" != "prod" ]; then
    build_one ../build_qemu_test target/qemu --features "qemu-test${NET_FEATURE:+,net}"
fi
'
