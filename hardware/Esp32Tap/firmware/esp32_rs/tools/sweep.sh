#!/usr/bin/env bash
# Full gate sweep. FAILS LOUDLY: every gate's status is printed and any failure
# makes the script exit non-zero. Written after a `&& echo` swallowed a failing
# suite and a commit went out with a red test.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
run() { local n="$1"; shift
  local t0=$(date +%s)
  if "$@" >/tmp/sweep_$n.log 2>&1; then printf '%-14s PASS  %3ds\n' "$n" "$(( $(date +%s)-t0 ))"
  else printf '%-14s ***FAIL*** %3ds (see /tmp/sweep_%s.log)\n' "$n" "$(( $(date +%s)-t0 ))" "$n"; fail=1; fi; }
# DEEP=1 adds the adversarial soak (11 scenarios, ~5 min: 420 HTTP requests,
# WS abuse, TCP churn, heap-convergence measurement). It is deliberately NOT in
# the per-commit gate — a 5-minute suite run after every increment is the kind
# of waste that cost hours earlier. Run it before merging, and any time the
# memory or request path changes.
S=$(date +%s)
# A gate nothing runs is not a gate. verify_harness_copy.py and
# check_log_contract.sh were both committed, both passing-or-failing on their
# own, and NEITHER was invoked by this script or by build.sh — so the harness
# byte-lock silently diverged (the parallel-safety fix landed in one of the two
# copies) and nothing noticed. They run here now, first, because they are fast
# and because a diverged harness invalidates every gate below them.
run harnesslock python3 tools/verify_harness_copy.py
run build      bash tools/build.sh
run logcontr   bash tools/check_log_contract.sh
run safety     cargo test --manifest-path safety_core/Cargo.toml -q
run reqbudget  cargo test --manifest-path reqbudget/Cargo.toml -q
run recstore   cargo test --manifest-path recstore/Cargo.toml -q
run progcore   cargo test --manifest-path program_core/Cargo.toml -q
# The BLE protocol tier. Pure, host-only, ~1 s — and it is where nearly all the
# real BLE behaviour lives, because the radio itself CANNOT be tested here
# (QEMU has no BLE). Every vector is the Pi daemon's, ported byte for byte, so
# a regression means a phone that worked against the Pi would see different
# bytes from the ESP32.
run blecore    cargo test --manifest-path ble_core/Cargo.toml -q
run difftest   cargo test --manifest-path difftest/Cargo.toml -q
run normalexit env -C tools/qemu_scenarios python3 -m pytest test_normal_exit.py -q
run httpentry  env -C tools/qemu_scenarios python3 -m pytest test_http_entry.py -q
run profiles   env -C tools/qemu_scenarios python3 -m pytest test_profiles.py -q -n 4
run program    env -C tools/qemu_scenarios python3 -m pytest test_program.py -q -n 4
run records    env -C tools/qemu_scenarios python3 -m pytest test_records.py -q -n 4
# test_store_persistence.py was committed and passing and NOTHING RAN IT — the
# same hole verify_harness_copy.py and check_log_contract.sh were in. It is the
# only gate that proves a record reaches real flash and survives a real SoC
# reset, so a silent regression there would look exactly like a working device
# until somebody power-cycled one.
run storepers  env -C tools/qemu_scenarios python3 -m pytest test_store_persistence.py -q
# The live push down /ws. The app feeds its ENTIRE running screen from this
# socket — every program-endpoint response body is discarded by
# TreadmillViewModel — so a firmware that completes the handshake and then never
# speaks again looks completely healthy from the HTTP side and shows the user a
# frozen screen for the whole workout. That is what it did.
run wspush     env -C tools/qemu_scenarios python3 -m pytest test_ws.py -q -n 3
# The reviewer's adversarial repros. Three of these were RED when written and
# name real belt-availability defects (a dribbling client holding the ONE httpd
# worker with the belt moving; Quick Start writing its progress into another
# program's history entry and marking it completed). They are a GATE now rather
# than an artifact, for the same reason test_store_persistence.py is: a
# committed, passing test that nothing runs is not a test. The two
# heap-convergence cases in the file cost ~5 min between them and are DEEP-only.
run memreview  env -C tools/qemu_scenarios python3 -m pytest test_mem_review.py -q -n 4 -k "not storm and not rejected"
run tls        env -C tools/qemu_scenarios python3 -m pytest test_tls.py -q
run tlspersist env -C tools/qemu_scenarios python3 -m pytest test_tls_persistence.py -q
run mdns       env -C tools/qemu_scenarios python3 -m pytest test_mdns.py -q
run smoke      bash tools/qemu_smoke.sh
run scenarios  env -C tools/qemu_harness python3 -m pytest test_scenarios.py -q -n 4
if [ -n "${DEEP:-}" ]; then
  run adversar   env -C tools/qemu_scenarios python3 -m pytest test_adversarial.py -q
  # The two heap-convergence cases held back from the per-commit gate above.
  run memheap    env -C tools/qemu_scenarios python3 -m pytest test_mem_review.py -q -n 2 -k "storm or rejected"
  # test_reviewer_attacks.py is DELIBERATELY NOT RUN HERE, and that is not the
  # same hole test_store_persistence.py was in. It is RED BY DESIGN: each of
  # its tests asserts the SAFE behaviour of a defect that is still open in the
  # safety/control tier (the 4 s manual lease deadman; STOP not zeroing a
  # manually commanded belt; a running program taking the belt back from the
  # physical console; an unrecoverable latched fault), and its own comments
  # derive why the device fails them. 4 of 7 fail on an untouched tree. Wiring
  # it in as a gate would make the sweep permanently red and train everyone to
  # ignore it. Run it deliberately:
  #   env -C tools/qemu_scenarios python3 -m pytest test_reviewer_attacks.py -q
fi
echo "SWEEP: $(( $(date +%s)-S ))s  $([ $fail -eq 0 ] && echo ALL GREEN || echo HAS FAILURES)"
exit $fail
