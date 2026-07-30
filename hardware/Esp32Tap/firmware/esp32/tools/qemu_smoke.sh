#!/usr/bin/env bash
# qemu_smoke.sh — ESP-IDF QEMU boot smoke gate for the Esp32Tap firmware.
#
# Boots the built esp32s3 image headless under the espressif QEMU fork
# (shipped inside the PINNED espressif/idf:release-v5.5 image), captures
# the UART0 console log for >= SMOKE_UPTIME_S seconds of GUEST uptime,
# and asserts:
#   1. app_main completes ("esp32tap phase-1 safety core started");
#   2. all three supervised tasks report started (serial_engine,
#      emulate_cycle, interval_executor);
#   3. NO task-WDT trigger, panic, abort, or reboot occurs (exactly one
#      ROM boot banner in the whole capture);
#   4. boot mode is PROXY with the relay released (app_main's
#      "boot state:" audit line).
#
# Guest uptime is proven by the interval executor's 5 s heartbeat log
# lines, not wall time (QEMU may run slower than real time). UART1/UART2
# are silent in QEMU — that is a normal Proxy condition and the serial
# engine polls zero bytes forever; console-silence safety semantics are
# untouched (freshness only gates Emulate entry, and boot is Proxy).
#
# Usage: tools/qemu_smoke.sh            (from firmware/esp32/, or anywhere)
# Env:   IDF_IMAGE (default espressif/idf:release-v5.5 — keep pinned)
#        SMOKE_UPTIME_S (default 15), SMOKE_WALL_TIMEOUT_S (default 20)
#
# SMOKE_WALL_TIMEOUT_S bounds only the CAPTURE WINDOW; the gate is
# SMOKE_UPTIME_S of GUEST uptime, which is unchanged. With `-icount` the
# guest free-runs at ~26x wall, so 15 s of guest uptime costs ~0.6 s of
# wall — the 90 s window this used to open was ~150x over-provisioned and
# every healthy run paid all of it (exit 124 is the expected ending). 20 s
# keeps ~34x headroom over the measured need while cutting the gate from
# 91 s to ~5 s. Nothing it asserts changed.

set -u -o pipefail

ESP32_DIR="$(cd "$(dirname "$0")/.." && pwd)"
IDF_IMAGE="${IDF_IMAGE:-espressif/idf:release-v5.5}"
SMOKE_UPTIME_S="${SMOKE_UPTIME_S:-15}"
SMOKE_WALL_TIMEOUT_S="${SMOKE_WALL_TIMEOUT_S:-20}"
# build/ is root-owned when produced by the docker build — log to /tmp.
LOG="${SMOKE_LOG:-/tmp/esp32tap_qemu_smoke.log}"

fail() { echo "qemu_smoke: FAIL — $*" >&2; exit 1; }

[ -f "$ESP32_DIR/build/esp32tap.bin" ] || fail \
    "build/esp32tap.bin missing — run the docker idf.py build first"

# The repo root is 5 levels up from firmware/esp32 (docker mounts the
# whole worktree so relative symlinks/paths keep working).
REPO_ROOT="$(cd "$ESP32_DIR/../../../.." && pwd)"
REL="${ESP32_DIR#"$REPO_ROOT"/}"

echo "qemu_smoke: booting esp32s3 under QEMU ($IDF_IMAGE), target guest uptime ${SMOKE_UPTIME_S}s ..."

docker run --rm -v "$REPO_ROOT":/project -w "/project/$REL" "$IDF_IMAGE" \
    bash -c '
        set -u
        cd build || exit 3
        # Merge a padded flash image for QEMU (esptool from the IDF env).
        # The emulated flash MUST be the size the app image header declares:
        # IDF spi_flash init aborts + reboots forever ("Detected size(...)
        # smaller than the size in the binary image header(...)") otherwise.
        # Take it from the build'"'"'s own flash_args rather than assuming 2MB.
        FS=$(sed -n "s/.*--flash_size \([0-9A-Za-z]*\).*/\1/p" flash_args | head -1)
        [ -n "$FS" ] || { echo "no --flash_size in build/flash_args" >&2; exit 3; }
        python -m esptool --chip esp32s3 merge_bin -o qemu_flash.bin \
            @flash_args --fill-flash-size "$FS" >/dev/null 2>&1 \
        || python -m esptool --chip esp32s3 merge-bin -o qemu_flash.bin \
            @flash_args --pad-to-size "$FS" >/dev/null \
        || exit 3
        cd ..
        # Headless boot; wall timeout only bounds the capture window —
        # exit 124 (timeout) is the EXPECTED end of a healthy run.
        timeout '"$SMOKE_WALL_TIMEOUT_S"' qemu-system-xtensa -icount shift=auto,sleep=off -nographic \
            -machine esp32s3 \
            -drive file=build/qemu_flash.bin,if=mtd,format=raw \
            2>&1
        true
    ' >"$LOG" 2>&1
echo "qemu_smoke: captured $(wc -l <"$LOG") console lines -> $LOG"

require() { # require <pattern> <description>
    grep -q "$1" "$LOG" || fail "$2 (pattern not found: $1)"
    echo "qemu_smoke: OK — $2"
}
forbid() { # forbid <pattern> <description>
    if grep -qi "$1" "$LOG"; then
        grep -i "$1" "$LOG" | head -3 >&2
        fail "$2 (forbidden pattern present: $1)"
    fi
    echo "qemu_smoke: OK — no $2"
}

# (1) app_main completed.
require "esp32tap phase-1 safety core started" "app_main completed"

# (2) all three supervised tasks started.
require "serial_engine task started"     "serial_engine task started"
require "emulate_cycle task started"     "emulate_cycle task started"
require "interval_executor task started" "interval_executor task started"

# (4) boot mode PROXY, relay released, TX disabled.
require "boot state: mode=PROXY relay=released tx_enable=0" \
    "boot state is PROXY with relay released"

# (3) no WDT / panic / abort / reboot.
forbid "Task watchdog got triggered" "task-WDT trigger"
forbid "Guru Meditation"             "panic (Guru Meditation)"
forbid "abort()"                     "abort()"
forbid "Panic handler"               "panic handler entry"
BOOTS=$(grep -c "ESP-ROM:esp32s3" "$LOG" || true)
[ "$BOOTS" -eq 1 ] || fail "expected exactly 1 ROM boot banner, saw $BOOTS (reboot occurred)"
echo "qemu_smoke: OK — single boot, no reboot"

# Guest uptime >= SMOKE_UPTIME_S proven by heartbeat log lines.
MAX_UPTIME=$(sed -n 's/.*heartbeat uptime=\([0-9]\+\)s.*/\1/p' "$LOG" | sort -n | tail -1)
[ -n "${MAX_UPTIME:-}" ] || fail "no interval_executor heartbeat seen"
[ "$MAX_UPTIME" -ge "$SMOKE_UPTIME_S" ] || fail \
    "guest uptime ${MAX_UPTIME}s < required ${SMOKE_UPTIME_S}s (raise SMOKE_WALL_TIMEOUT_S?)"
echo "qemu_smoke: OK — panic-free guest uptime ${MAX_UPTIME}s >= ${SMOKE_UPTIME_S}s"

echo "qemu_smoke: PASS"
