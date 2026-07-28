#!/usr/bin/env bash
# Full gate sweep. FAILS LOUDLY: every gate's status is printed and any failure
# makes the script exit non-zero. Written after a `&& echo` swallowed a failing
# suite and a commit went out with a red test.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
run() { local n="$1"; shift
  if "$@" >/tmp/sweep_$n.log 2>&1; then printf '%-14s PASS\n' "$n"
  else printf '%-14s ***FAIL*** (see /tmp/sweep_%s.log)\n' "$n" "$n"; fail=1; fi; }
S=$(date +%s)
run build      bash tools/build.sh
run safety     cargo test --manifest-path safety_core/Cargo.toml -q
run reqbudget  cargo test --manifest-path reqbudget/Cargo.toml -q
run difftest   cargo test --manifest-path difftest/Cargo.toml -q
run normalexit env -C tools/qemu_scenarios python3 -m pytest test_normal_exit.py -q
run smoke      bash tools/qemu_smoke.sh
run scenarios  env -C tools/qemu_harness python3 -m pytest test_scenarios.py -q
echo "SWEEP: $(( $(date +%s)-S ))s  $([ $fail -eq 0 ] && echo ALL GREEN || echo HAS FAILURES)"
exit $fail
