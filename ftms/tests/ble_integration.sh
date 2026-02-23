#!/usr/bin/env bash
# FTMS BLE Integration Tests (gatttool-based)
#
# Tests the FTMS GATT service over real BLE using two adapters on the same Pi:
# hci0 (built-in) runs ftms-daemon as GATT server, hci1 (USB dongle) acts as
# the test client.
#
# Why gatttool instead of Rust/bluer?
#   BlueZ D-Bus does not support BLE connections between two local adapters on
#   the same host — discovery works but connect() hangs indefinitely. This is a
#   kernel/BlueZ limitation. gatttool bypasses D-Bus and uses L2CAP sockets
#   directly, which works fine for local loopback. Since Rust isn't installed on
#   the Pi and cross-compiling test binaries adds friction, a shell script using
#   gatttool is the simplest and most reliable approach.
#
# Requirements:
#   - ftms-daemon running on hci0
#   - USB BLE dongle as hci1 (up and unblocked: sudo rfkill unblock bluetooth
#     && sudo hciconfig hci1 up)
#   - treadmill_io running
#
# Usage:
#   sudo bash ftms/tests/ble_integration.sh             # run all tests
#   sudo bash ftms/tests/ble_integration.sh cp_speed     # run one test
#   make test-ftms-ble                                   # run from dev machine

set -uo pipefail

# --- Configuration ---
HCI="${FTMS_HCI:-hci1}"
TARGET_ADDR="${FTMS_ADDR:-DC:A6:32:AA:3B:D4}"
ADDR_TYPE="${FTMS_ADDR_TYPE:-public}"

# FTMS characteristic value handles (discovered via --characteristics)
FEAT_HANDLE="0x00bd"    # Feature (2ACC) - Read
SR_HANDLE="0x00bf"      # Speed Range (2AD4) - Read
IR_HANDLE="0x00c1"      # Incline Range (2AD5) - Read
TD_HANDLE="0x00c3"      # Treadmill Data (2ACD) - Notify
CP_HANDLE="0x00ba"      # Control Point (2AD9) - Write+Indicate
TD_CCC="0x00c4"         # Treadmill Data CCCD (enable notifications)
CP_CCC="0x00bb"         # Control Point CCCD (enable indications)

PASS=0
FAIL=0
SKIP=0

# --- Helpers ---

gt() {
    gatttool -i "$HCI" -b "$TARGET_ADDR" -t "$ADDR_TYPE" "$@" 2>&1
}

pass() { echo "  PASS: $1"; ((PASS++)); }
fail() { echo "  FAIL: $1"; ((FAIL++)); }
skip() { echo "  SKIP: $1"; ((SKIP++)); }

hex_to_u16() {
    # Convert two hex bytes (LE) to uint16. Usage: hex_to_u16 "50" "00" → 80
    printf '%d' "0x${2}${1}"
}

hex_to_i16() {
    # Convert two hex bytes (LE) to int16. Usage: hex_to_i16 "00" "00" → 0
    local val
    val=$(printf '%d' "0x${2}${1}")
    if (( val > 32767 )); then
        val=$((val - 65536))
    fi
    echo "$val"
}

# Run an interactive gatttool session with timed commands.
# Usage: gt_interactive <outfile> <timeout_secs> <cmd> [<sleep> <cmd> ...]
# Commands are strings sent to gatttool. Numeric args are sleep durations.
gt_interactive() {
    local outfile=$1; shift
    local tout=$1; shift

    local fifo
    fifo=$(mktemp -u /tmp/gt_fifo.XXXXXX)
    mkfifo "$fifo"

    # Start gatttool reading from fifo
    timeout "$tout" gatttool -i "$HCI" -b "$TARGET_ADDR" -t "$ADDR_TYPE" -I < "$fifo" > "$outfile" 2>&1 &
    local gt_pid=$!

    # Write commands with timing
    exec 3>"$fifo"
    echo "connect" >&3

    # Wait for "Connection successful" before sending commands
    local waited=0
    while [[ $waited -lt 10 ]]; do
        if grep -q "Connection successful" "$outfile" 2>/dev/null; then
            break
        fi
        sleep 0.5
        waited=$((waited + 1))
    done
    if ! grep -q "Connection successful" "$outfile" 2>/dev/null; then
        echo "WARNING: Connection may not be established" >> "$outfile"
    fi
    sleep 0.3  # small buffer after connection

    while [[ $# -gt 0 ]]; do
        if [[ "$1" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
            sleep "$1"
        else
            echo "$1" >&3
        fi
        shift
    done

    sleep 0.5
    echo "disconnect" >&3
    sleep 0.3
    echo "quit" >&3
    exec 3>&-

    wait "$gt_pid" 2>/dev/null || true
    rm -f "$fifo"
}

# --- Tests ---

test_discovery() {
    echo "TEST: BLE Discovery"
    # Try btmgmt scan first (shows advertising names)
    local output
    output=$(timeout 10 btmgmt --index "${HCI#hci}" find -l 2>&1) || true
    if echo "$output" | grep -q "Precor 9.31"; then
        pass "Found 'Precor 9.31' via btmgmt scan"
        return
    fi
    # Fallback: verify we can connect and read a characteristic
    local raw
    raw=$(gt --char-read -a "$FEAT_HANDLE" 2>&1) || true
    if echo "$raw" | grep -q "Characteristic value"; then
        pass "Found 'Precor 9.31' (verified via GATT read)"
    else
        fail "Could not find or connect to 'Precor 9.31'"
    fi
}

test_read_feature() {
    echo "TEST: Read Feature Characteristic (2ACC)"
    local raw
    raw=$(gt --char-read -a "$FEAT_HANDLE") || { fail "Could not read Feature"; return; }
    local hex
    hex=$(echo "$raw" | sed 's/Characteristic value\/descriptor: //')
    local count
    count=$(echo "$hex" | wc -w)
    if [[ "$count" -ne 8 ]]; then
        fail "Feature should be 8 bytes, got $count"
        return
    fi

    # Expected: 0c 10 00 00 03 00 00 00
    local bytes
    read -ra bytes <<< "$hex"
    local machine_feat
    machine_feat="${bytes[3]}${bytes[2]}${bytes[1]}${bytes[0]}"
    local target_feat
    target_feat="${bytes[7]}${bytes[6]}${bytes[5]}${bytes[4]}"

    if [[ "$machine_feat" == "0000100c" ]] && [[ "$target_feat" == "00000003" ]]; then
        pass "Feature: machine=0x$machine_feat target=0x$target_feat"
    else
        fail "Feature: expected machine=0x0000100c target=0x00000003, got machine=0x$machine_feat target=0x$target_feat"
    fi
}

test_read_speed_range() {
    echo "TEST: Read Speed Range Characteristic (2AD4)"
    local raw
    raw=$(gt --char-read -a "$SR_HANDLE") || { fail "Could not read Speed Range"; return; }
    local hex
    hex=$(echo "$raw" | sed 's/Characteristic value\/descriptor: //')
    local count
    count=$(echo "$hex" | wc -w)
    if [[ "$count" -ne 6 ]]; then
        fail "Speed Range should be 6 bytes, got $count"
        return
    fi

    read -ra bytes <<< "$hex"
    local min max step
    min=$(hex_to_u16 "${bytes[0]}" "${bytes[1]}")
    max=$(hex_to_u16 "${bytes[2]}" "${bytes[3]}")
    step=$(hex_to_u16 "${bytes[4]}" "${bytes[5]}")

    local ok=true
    [[ "$min" -eq 80 ]]   || { fail "Speed Range min: expected 80, got $min"; ok=false; }
    [[ "$max" -eq 1931 ]] || { fail "Speed Range max: expected 1931, got $max"; ok=false; }
    [[ "$step" -eq 16 ]]  || { fail "Speed Range step: expected 16, got $step"; ok=false; }
    $ok && pass "Speed Range: min=${min} max=${max} step=${step} (km/h*100)"
}

test_read_incline_range() {
    echo "TEST: Read Incline Range Characteristic (2AD5)"
    local raw
    raw=$(gt --char-read -a "$IR_HANDLE") || { fail "Could not read Incline Range"; return; }
    local hex
    hex=$(echo "$raw" | sed 's/Characteristic value\/descriptor: //')
    local count
    count=$(echo "$hex" | wc -w)
    if [[ "$count" -ne 6 ]]; then
        fail "Incline Range should be 6 bytes, got $count"
        return
    fi

    read -ra bytes <<< "$hex"
    local min max step
    min=$(hex_to_i16 "${bytes[0]}" "${bytes[1]}")
    max=$(hex_to_i16 "${bytes[2]}" "${bytes[3]}")
    step=$(hex_to_u16 "${bytes[4]}" "${bytes[5]}")

    local ok=true
    [[ "$min" -eq 0 ]]   || { fail "Incline Range min: expected 0, got $min"; ok=false; }
    [[ "$max" -eq 150 ]] || { fail "Incline Range max: expected 150, got $max"; ok=false; }
    [[ "$step" -eq 10 ]] || { fail "Incline Range step: expected 10, got $step"; ok=false; }
    $ok && pass "Incline Range: min=${min} max=${max} step=${step} (%*10)"
}

test_treadmill_data() {
    echo "TEST: Read Treadmill Data (2ACD) via notification"
    local outfile
    outfile=$(mktemp /tmp/gt_td.XXXXXX)

    gt_interactive "$outfile" 10 \
        "char-write-req $TD_CCC 0100" \
        4

    local output
    output=$(cat "$outfile")
    rm -f "$outfile"

    # Count notification lines
    local notif_count
    notif_count=$(echo "$output" | grep -c "Notification handle" || true)
    if [[ "$notif_count" -lt 2 ]]; then
        fail "Expected >=2 notifications, got $notif_count"
        echo "    Output: $output"
        return
    fi

    # Parse first notification
    local first_notif
    first_notif=$(echo "$output" | grep "Notification handle" | head -1)
    local hex
    hex=$(echo "$first_notif" | sed 's/.*value: //')
    local count
    count=$(echo "$hex" | wc -w)
    if [[ "$count" -ne 13 ]]; then
        fail "Treadmill Data should be 13 bytes, got $count"
        return
    fi

    read -ra bytes <<< "$hex"
    local flags
    flags=$(hex_to_u16 "${bytes[0]}" "${bytes[1]}")
    if [[ "$flags" -ne $((0x040C)) ]]; then
        fail "Flags should be 0x040C (1036), got $flags"
        return
    fi

    local speed
    speed=$(hex_to_u16 "${bytes[2]}" "${bytes[3]}")
    pass "Treadmill Data: $notif_count notifications, flags=0x$(printf '%04x' $flags), speed=${speed} (km/h*100)"
}

# Helper to check an indication response line
check_indication() {
    local label=$1 indications=$2 index=$3 expected=$4
    local resp
    resp=$(echo "$indications" | sed -n "${index}p" | sed 's/.*value: //')
    read -ra resp_bytes <<< "$resp"
    local actual="${resp_bytes[0]} ${resp_bytes[1]} ${resp_bytes[2]}"
    if [[ "$actual" == "$expected" ]]; then
        pass "$label: response $expected (success)"
    else
        fail "$label: expected '$expected', got '$actual'"
    fi
}

# Run a control point session: request control → start → commands → stop
# Returns indications in $CP_INDICATIONS and count in $CP_IND_COUNT
run_cp_session() {
    local outfile
    outfile=$(mktemp /tmp/gt_cp.XXXXXX)

    gt_interactive "$outfile" 25 \
        "char-write-req $CP_CCC 0200" \
        0.5 \
        "char-write-req $CP_HANDLE 00" \
        1 \
        "char-write-req $CP_HANDLE 07" \
        1 \
        "$@" \
        "char-write-req $CP_HANDLE 0801" \
        1

    CP_OUTPUT=$(cat "$outfile")
    rm -f "$outfile"

    CP_INDICATIONS=$(echo "$CP_OUTPUT" | grep "Indication" || true)
    CP_IND_COUNT=$(echo "$CP_INDICATIONS" | grep -c "Indication" || true)
}

test_control_point_speed() {
    echo "TEST: Control Point — Set Speed only"

    # Set Target Speed: 5.0 km/h = 500 = 0x01F4 LE → 02 f4 01
    run_cp_session \
        "char-write-req $CP_HANDLE 02f401" \
        2

    if [[ "$CP_IND_COUNT" -lt 3 ]]; then
        fail "Expected >=3 indications, got $CP_IND_COUNT"
        echo "    Output:"
        echo "$CP_OUTPUT" | head -20 | sed 's/^/    /'
        return
    fi

    check_indication "Request Control" "$CP_INDICATIONS" 1 "80 00 01"
    check_indication "Start"           "$CP_INDICATIONS" 2 "80 07 01"
    check_indication "Set Speed (5.0 km/h)" "$CP_INDICATIONS" 3 "80 02 01"
    [[ "$CP_IND_COUNT" -ge 4 ]] && check_indication "Stop" "$CP_INDICATIONS" 4 "80 08 01"
}

test_control_point_incline() {
    echo "TEST: Control Point — Set Incline only"

    # Set Target Incline: 5.0% = 50 = 0x0032 LE → 03 32 00
    run_cp_session \
        "char-write-req $CP_HANDLE 033200" \
        2

    if [[ "$CP_IND_COUNT" -lt 3 ]]; then
        fail "Expected >=3 indications, got $CP_IND_COUNT"
        echo "    Output:"
        echo "$CP_OUTPUT" | head -20 | sed 's/^/    /'
        return
    fi

    check_indication "Request Control" "$CP_INDICATIONS" 1 "80 00 01"
    check_indication "Start"           "$CP_INDICATIONS" 2 "80 07 01"
    check_indication "Set Incline (5.0%)" "$CP_INDICATIONS" 3 "80 03 01"
    [[ "$CP_IND_COUNT" -ge 4 ]] && check_indication "Stop" "$CP_INDICATIONS" 4 "80 08 01"
}

test_control_point_speed_and_incline() {
    echo "TEST: Control Point — Set Speed + Incline together"

    run_cp_session \
        "char-write-req $CP_HANDLE 02f401" \
        1 \
        "char-write-req $CP_HANDLE 033200" \
        2

    if [[ "$CP_IND_COUNT" -lt 4 ]]; then
        fail "Expected >=4 indications, got $CP_IND_COUNT"
        echo "    Output:"
        echo "$CP_OUTPUT" | head -25 | sed 's/^/    /'
        return
    fi

    check_indication "Request Control" "$CP_INDICATIONS" 1 "80 00 01"
    check_indication "Start"           "$CP_INDICATIONS" 2 "80 07 01"
    check_indication "Set Speed (5.0 km/h)" "$CP_INDICATIONS" 3 "80 02 01"
    check_indication "Set Incline (5.0%)"   "$CP_INDICATIONS" 4 "80 03 01"
    [[ "$CP_IND_COUNT" -ge 5 ]] && check_indication "Stop" "$CP_INDICATIONS" 5 "80 08 01"
}

# --- Runner ---

run_test() {
    case "$1" in
        discovery)            test_discovery ;;
        read_feature)         test_read_feature ;;
        read_speed_range)     test_read_speed_range ;;
        read_incline_range)   test_read_incline_range ;;
        treadmill_data)       test_treadmill_data ;;
        cp_speed)             test_control_point_speed ;;
        cp_incline)           test_control_point_incline ;;
        cp_speed_and_incline) test_control_point_speed_and_incline ;;
        *) echo "Unknown test: $1"; exit 1 ;;
    esac
}

ALL_TESTS="discovery read_feature read_speed_range read_incline_range treadmill_data cp_speed cp_incline cp_speed_and_incline"

# Check prerequisites
if ! hciconfig "$HCI" 2>/dev/null | grep -q "UP RUNNING"; then
    echo "ERROR: $HCI is not UP. Run: sudo hciconfig $HCI up"
    exit 1
fi

echo "=== FTMS BLE Integration Tests ==="
echo "Adapter: $HCI → Target: $TARGET_ADDR ($ADDR_TYPE)"
echo ""

if [[ $# -gt 0 ]]; then
    for t in "$@"; do
        run_test "$t"
    done
else
    for t in $ALL_TESTS; do
        run_test "$t"
        echo ""
    done
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed, $SKIP skipped ==="
[[ "$FAIL" -eq 0 ]] || exit 1
