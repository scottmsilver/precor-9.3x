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
# `run recstore` was here. The crate it gated is DELETED: the persistence tier
# is LittleFS now (net/store.rs). Two of the properties that suite proved are
# genuinely the filesystem's by construction — a slot never straddling a NOR
# sector, an erased sequence never sorting as the newest record. The THIRD is
# not, and saying it was is how this sweep briefly ran with no power-loss
# coverage at all: "a torn write never damages a record that was already
# committed" is a claim about THIS mount, and `storetorn` below measures it by
# resetting the SoC inside the write. `storepers` and `records` are the rest of
# the replacement — real flash in a real guest, untouched by the swap.
run progcore   cargo test --manifest-path program_core/Cargo.toml -q
# The coach tier's judgement, host-only and ~0 s. EVERY property that decides
# what a model reply MEANS lives here: the bounded streaming extractor, the
# clamps applied to a tool call, and the truncation salvage. The live endpoint
# is DELIBERATELY not a gate (it needs a real key, it is nondeterministic, and
# it costs money), so this file is what stands in for it — which is why its
# fixtures are the failure shapes rather than the happy path.
run coachcore  cargo test --manifest-path coach_core/Cargo.toml -q
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
# Mandatory Pi-parity adversarial gate: all attacks A-G, including deliberate
# relay fault injection/recovery and sticky physical-console takeover.
run reviewer   env -C tools/qemu_scenarios python3 -m pytest test_reviewer_attacks.py -q -n 3
run records    env -C tools/qemu_scenarios python3 -m pytest test_records.py -q -n 4
# test_store_persistence.py was committed and passing and NOTHING RAN IT — the
# same hole verify_harness_copy.py and check_log_contract.sh were in. It is the
# only gate that proves a record reaches real flash and survives a real SoC
# reset, so a silent regression there would look exactly like a working device
# until somebody power-cycled one.
run storepers  env -C tools/qemu_scenarios python3 -m pytest test_store_persistence.py -q
# THE POWER-LOSS GATE, and it is the one the `recstore` deletion removed. That
# store carried two torn-write tests and they caught BOTH of its original
# defects; deleting it deleted them, and `QT reboot` above is a CLEAN restart
# after the write finished — it exercises reload-from-flash and never
# interruption. This file resets the SoC INSIDE `write_slot` (mid-staging at
# four byte offsets, between the sync and the rename, and just after the rename)
# and asserts the slot reads as exactly the old record or exactly the new one.
# Without it, net/store.rs's central safety claim is littlefs's reputation
# rather than a measured property of this mount.
run storetorn  env -C tools/qemu_scenarios python3 -m pytest test_store_power_loss.py -q -n 4
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
# The AI coach tier, against a LOCAL STUB the test controls. The live endpoint
# is DELIBERATELY not a gate — a real key, nondeterministic words and somebody
# else's uptime is the definition of an intermittent, and an intermittent is
# worse than a hard failure. `test_coach_live.py` is the opt-in confirmation
# (COACH_LIVE_KEY=...), never run here.
#
# The headline case is `test_stop_stays_responsive_while_a_coach_call_is_in
# _flight`: belt moving under a program, a model call in flight against an
# endpoint that will not answer for 8 s, and `POST /api/program/stop` must still
# complete promptly. That is the same belt-availability class as the TLS
# handshake budget and the dribbling-body deadline, and it is the reason the
# round trip is on its own task rather than on the ONE httpd worker.
run coach      env -C tools/qemu_scenarios python3 -m pytest test_coach.py -q -n 4
run tls        env -C tools/qemu_scenarios python3 -m pytest test_tls.py -q
run tlspersist env -C tools/qemu_scenarios python3 -m pytest test_tls_persistence.py -q
run mdns       env -C tools/qemu_scenarios python3 -m pytest test_mdns.py -q
# The device with NO WORKING RADIO. This is the one BLE property QEMU can
# prove, and it was NOT free: the first BLE-enabled image REBOOT-LOOPED, because
# `nimble_port_init` does not return an error when the controller cannot come
# up — it assert()s inside the closed-source blob, which panics, which resets
# the SoC, which drops the relay mid-run. The guard in front of that call is
# what this gate holds in place. Note that the whole qemu-test image carries
# `--features ble`, so every scenario above ALSO runs against a device whose
# radio failed; this file states the property rather than leaving it implied.
run bledegrade env -C tools/qemu_scenarios python3 -m pytest test_ble_degraded.py -q -n 3
# The BLE tier's BELT EDGE, with no radio. `access_cb`'s mbuf copy is the only
# part of a Control Point write that needs Bluetooth; everything below it —
# parse, effect, the lease, the clamps, the auto-emulate policy, the FTMS
# result mapping — is ordinary Rust on the real controller, and the qemu-test
# `QT ble_cp` verb drives exactly that. Two REAL defects were review-only until
# this file existed: a Stop denied by the lease while a program owned the belt
# (answered FAILED with the belt still running at 6 mph), and a negative
# Set-Target-Inclination refused where the daemon flattened the belt. Both were
# run RED against the pre-fix image.
run blecp      env -C tools/qemu_scenarios python3 -m pytest test_ble_control_point.py -q -n 4
run smoke      bash tools/qemu_smoke.sh
run scenarios  env -C tools/qemu_harness python3 -m pytest test_scenarios.py -q -n 4
if [ -n "${DEEP:-}" ]; then
  run adversar   env -C tools/qemu_scenarios python3 -m pytest test_adversarial.py -q
  # The two heap-convergence cases held back from the per-commit gate above.
  run memheap    env -C tools/qemu_scenarios python3 -m pytest test_mem_review.py -q -n 2 -k "storm or rejected"
  # The coach tier's reviewer repros — ALL SEVEN, as one gate. They were the
  # reviewers' reproductions of five coach defects, and the fix round closed
  # every one: the truncated args object echoed verbatim into invalid JSON, the
  # actions buffer saturating mid-entry at the DECLARED maximum of four calls,
  # the tool name written between quotes UNESCAPED (a `"` injected members, a
  # control byte made the body unparseable), and `stop_treadmill` reporting
  # "treadmill stopped" while leaving the belt at speed.
  #
  # This block used to run 2 of the 7 and describe the other 5 as "red by
  # design". They had stopped being red — the comment outlived the defects — so
  # five passing tests sat outside the sweep, which is EXACTLY the hole
  # test_store_persistence.py was in and which the block below is careful not
  # to be. Measured green 7/7 in 59 s before wiring in.
  run coachrev   env -C tools/qemu_scenarios python3 -m pytest test_coach_review.py -q -n 2
fi
echo "SWEEP: $(( $(date +%s)-S ))s  $([ $fail -eq 0 ] && echo ALL GREEN || echo HAS FAILURES)"
exit $fail
