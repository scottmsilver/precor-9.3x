# Esp32Tap Provenance-Safe Fast Inner Loop

Date: 2026-07-30
Status: Owner-approved design; implementation not started
Tracking: `precor-9_3x-40g`, `precor-9_3x-344`

## Decision

Optimize the developer inner loop before optimizing the complete release
sweep. The fast lane will combine:

1. a deterministic provenance check for the QEMU firmware image; and
2. conservative, path-based selection of focused host and QEMU gates.

The normal and `DEEP=1` release sweeps remain unchanged. Unknown or
cross-cutting changes fall back to the broader normal sweep. A speed
optimization may skip work only when a versioned rule proves that work is
unaffected; it may never weaken an assertion, add a retry, loosen a deadline,
or reuse guest state between tests.

## Problem and Evidence

The current inner loop spends time in three places:

- building the QEMU image;
- starting an isolated Docker/QEMU guest for each scenario; and
- running broad scenario files when only one behavior changed.

Incremental firmware builds are already reasonably fast: recent current-source
QEMU builds took 3.4 to 14.2 seconds. Focused scenarios took roughly 4.9 to
15.8 seconds, while the seven-test reviewer suite took 41.6 to 91.0 seconds
depending on host load. Host-only Rust and structural gates are usually
sub-second to a few seconds.

The largest avoidable loss was not compilation. `build_qemu_test/esp32tap.bin`
is tracked and the fixture checks only that it exists. Restoring that generated
file produced a 1,299,008-byte stale image with a new filesystem timestamp.
Tests then ran against firmware older than the source, generated false
failures, and had to be rebuilt and repeated. The current-source images were
1,306,272 bytes or larger. Modification time therefore cannot establish
provenance.

The QEMU fixture intentionally starts a fresh process and flash image per test.
That isolation protects NVS state, fault latches, audit ordering, ports, crash
detection, and power-loss tests. Sharing a live guest is not part of this
design.

## Goals

- Fail a missing or stale QEMU artifact before a guest or container starts.
- Reuse a verified current artifact without rebuilding it.
- Give an uncommitted, localized change a focused verification command.
- Keep selection conservative and auditable in Git.
- Preserve fresh-guest isolation and every existing safety assertion.
- Measure the fast lane so future tuning is based on wall time and flake rate.

Warm-loop targets on the current host are:

- provenance rejection: under one second;
- host-only localized change: under five seconds where its existing gates
  permit;
- localized firmware change requiring QEMU: under roughly 30 seconds when the
  incremental image cache is warm.

These are workflow targets, not test deadlines. Host contention may increase
wall time without changing guest-state acceptance criteria.

## Non-Goals

- Removing or weakening the normal or deep release sweep.
- Increasing `pytest-xdist` workers without repeated measurements.
- Reusing one booted guest across tests.
- Treating QEMU timing as hardware cycle-time evidence.
- Replacing Task 8's validation-only on-device performance instrumentation.
- Solving every clean-checkout build defect in `precor-9_3x-344`; this design
  addresses generated firmware provenance and coordinates with that issue.

## Artifact Provenance

### Generated artifacts

Rust firmware build outputs, including `build/` and `build_qemu_test/`, are
generated state and must not be Git source-of-truth. Implementation will stop
tracking them and add explicit ignore rules. Removing tracked generated files
must not remove the build recipes or required source inputs.

### Manifest

`ONLY=qemu tools/build.sh` will write an atomic manifest beside the completed
image only after all existing build gates succeed. The manifest will contain:

- a schema version;
- artifact kind (`qemu-test`);
- enabled features and Cargo profile;
- firmware binary SHA-256 and byte size;
- a deterministic digest of all declared build inputs;
- the build container/toolchain identity already printed by the build; and
- the generated sdkconfig digest.

The input digest will hash path names and file contents in sorted order. Its
declared input set includes:

- all Rust workspace manifests, lockfiles, configuration, linker/build files,
  and source used by the firmware feature set;
- `esp32tap`, `safety_core`, `program_core`, `reqbudget`, `ble_core`, and
  `coach_core` inputs;
- normal and QEMU sdkconfig defaults;
- partition/build metadata consumed by the build; and
- the build/provenance scripts themselves.

Dirty and untracked source files are hashed from the working tree, not from
`HEAD`. Git timestamps and output-directory timestamps are never inputs.

### Fixture enforcement

Before acquiring a port, starting Docker, merging flash, or launching QEMU,
the fixture will:

1. require the image and manifest;
2. recompute the declared input digest;
3. verify manifest schema, feature/profile identity, image size, and image
   SHA-256; and
4. fail with one exact remediation command when anything differs:
   `ONLY=qemu bash tools/build.sh`.

The check remains under the existing build/read lock so a build cannot replace
the image between verification and flash assembly.

The checker will also have a cheap standalone mode so the fast lane and CI can
validate provenance without starting pytest.

## Impact-Aware Fast Lane

Add one command, provisionally `tools/fast.sh`, backed by a small testable
selector. With no explicit paths it inspects staged, unstaged, and untracked
working-tree changes. Explicit paths are accepted for reviewing an already
committed task.

The selector maps paths to named gate groups, for example:

- program/control: `program_core`, focused program lifecycle, reviewer control;
- HTTP/request handling: request-budget/structural checks and focused API or
  reviewer cases;
- safety controller: safety/differential host gates plus the broad control
  QEMU group;
- BLE: `ble_core` and relevant BLE QEMU cases;
- coach: `coach_core` and coach contract cases;
- records/storage: record host checks, records, persistence, and torn-write
  cases; and
- documentation-only: syntax/link or documentation checks, with no firmware
  build unless executable examples changed.

Every input path must resolve to at least one policy. Shared build files,
sdkconfig, the QEMU harness, safety/control interfaces, feature flags, route
registration, the selector itself, and unknown paths select the conservative
broad lane. No rule may silently select an empty gate set.

The fast lane runs in this order:

1. cheap syntax, structural, and affected host gates;
2. provenance check;
3. one incremental `ONLY=qemu` build only if a selected QEMU gate needs it and
   provenance is stale;
4. selected focused QEMU gates with their currently proven worker counts; and
5. a summary of selected policies, wall time per gate, image identity, and the
   exact reason for any broad fallback.

The selector does not use `-n auto`. Existing worker counts of three or four
remain ceilings until repeated benchmarks show a lower p95 wall time without
new flakes or guest-time distortion.

## Correctness and Failure Behavior

- A stale or unverifiable image is a hard failure in direct pytest use.
- The fast lane may rebuild that image automatically once, then re-check it.
- A failed selected gate stops the fast lane and reports its retained log.
- A selector bug must fail broad, not narrow.
- QEMU sessions keep private ports and flash and continue to boot once per
  test.
- Release completion still requires the normal sweep; `DEEP=1` remains
  required where the existing workflow requires it.

## Verification

Host tests will prove:

- deterministic input digests independent of filesystem ordering and mtimes;
- dirty and untracked inputs invalidate a manifest;
- changed image bytes, features, sdkconfig, or schema invalidate it;
- atomic/incomplete manifests are rejected;
- every tracked workspace path is classified or forces broad fallback;
- multiple changed paths produce the union of their gates;
- unknown and cross-cutting paths select broad;
- documentation-only changes do not build firmware; and
- a QEMU-selected change builds at most once per fast-lane invocation.

Integration tests will prove:

- direct pytest refuses the known tracked stale-image shape before Docker;
- a fresh build creates a valid manifest and focused QEMU scenario passes;
- modifying a relevant source invalidates the artifact immediately;
- normal and deep sweep command lists are unchanged; and
- each logical QEMU test still receives a fresh guest and flash image.

Benchmark output will record cold/warm build time, provenance-check time,
selected gate time, host load, and pass/fail. Optimization is accepted only if
assertions and isolation are unchanged.

## Deferred Optimizations

After the fast lane has timing history, benchmark representative suites at
`-n 2`, `-n 3`, and `-n 4`. Change a worker count only when repeated p95 wall
time improves with zero additional flakes.

Container reuse may later avoid startup overhead only if each logical test
still starts a new QEMU process from a private immutable flash seed. Live-guest
pooling is rejected because its state leakage would invalidate the safety
evidence.
