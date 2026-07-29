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
run difftest   cargo test --manifest-path difftest/Cargo.toml -q
run normalexit env -C tools/qemu_scenarios python3 -m pytest test_normal_exit.py -q
run httpentry  env -C tools/qemu_scenarios python3 -m pytest test_http_entry.py -q
run profiles   env -C tools/qemu_scenarios python3 -m pytest test_profiles.py -q
run program    env -C tools/qemu_scenarios python3 -m pytest test_program.py -q -n 4
run tls        env -C tools/qemu_scenarios python3 -m pytest test_tls.py -q
run tlspersist env -C tools/qemu_scenarios python3 -m pytest test_tls_persistence.py -q
run mdns       env -C tools/qemu_scenarios python3 -m pytest test_mdns.py -q
run smoke      bash tools/qemu_smoke.sh
run scenarios  env -C tools/qemu_harness python3 -m pytest test_scenarios.py -q -n 4
if [ -n "${DEEP:-}" ]; then
  run adversar   env -C tools/qemu_scenarios python3 -m pytest test_adversarial.py -q
fi
echo "SWEEP: $(( $(date +%s)-S ))s  $([ $fail -eq 0 ] && echo ALL GREEN || echo HAS FAILURES)"
exit $fail
