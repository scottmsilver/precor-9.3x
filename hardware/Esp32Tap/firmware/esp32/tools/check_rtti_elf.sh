#!/usr/bin/env bash
# check_rtti_elf.sh — artifact-level RTTI gate for the esp32s3 build.
#
# With CONFIG_COMPILER_CXX_RTTI unset, IDF 5.5 applies -fno-rtti to every
# C++ compile via the response file build/toolchain/cxxflags (referenced
# as CMAKE_CXX_FLAGS=@..., so it does NOT appear inline in
# compile_commands.json). This script verifies the PROPERTY on the linked
# ELF instead of trusting the flag plumbing: with -fno-rtti no typeinfo
# (_ZTI*) / typeinfo-name (_ZTS*) symbols are emitted for firmware
# classes. libstdc++'s own prebuilt typeinfo may legitimately be present,
# so the check is scoped to firmware identifiers.
#
# Usage: tools/check_rtti_elf.sh   (host binutils readelf handles xtensa
#        ELF symbol tables fine; run after the docker build)

set -u -o pipefail

ESP32_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ELF="$ESP32_DIR/build/esp32tap.elf"

[ -f "$ELF" ] || { echo "check_rtti_elf: $ELF missing — build first" >&2; exit 1; }

# Firmware identifiers: the esp32tap namespaces plus the global-namespace
# classes forked from cpp/ that have virtual members anywhere.
PATTERN='esp32tap|ModeStateMachine|EmulationCycle|SerialReader|SerialWriter|KeyCache|KvPair'

# 1) The response file (if present) must carry -fno-rtti.
CXXFLAGS_FILE="$ESP32_DIR/build/toolchain/cxxflags"
if [ -f "$CXXFLAGS_FILE" ]; then
    grep -q -- '-fno-rtti' "$CXXFLAGS_FILE" \
        || { echo "check_rtti_elf: FAIL — $CXXFLAGS_FILE lacks -fno-rtti" >&2; exit 1; }
    echo "check_rtti_elf: OK — build/toolchain/cxxflags contains -fno-rtti"
fi

# 2) No typeinfo symbols for firmware classes in the linked artifact.
HITS=$(readelf -sW "$ELF" | grep -E '_ZT[IS]' | grep -cE "$PATTERN" || true)
TOTAL=$(readelf -sW "$ELF" | grep -cE '_ZT[IS]' || true)
if [ "$HITS" -ne 0 ]; then
    echo "check_rtti_elf: FAIL — $HITS firmware typeinfo symbol(s) found (RTTI leaked in):" >&2
    readelf -sW "$ELF" | grep -E '_ZT[IS]' | grep -E "$PATTERN" | head -10 >&2
    exit 1
fi
echo "check_rtti_elf: OK — 0 firmware typeinfo symbols in esp32tap.elf" \
     "($TOTAL total _ZTI/_ZTS, all from prebuilt libstdc++/IDF internals)"
echo "check_rtti_elf: PASS"
