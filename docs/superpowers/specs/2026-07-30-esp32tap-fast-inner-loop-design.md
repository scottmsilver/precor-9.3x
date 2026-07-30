# Esp32Tap Provenance-Safe Fast Inner Loop

Date: 2026-07-30
Status: Owner-approved design under review; implementation not started
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
tracking them and add explicit ignore rules.

That migration also closes the clean-checkout prerequisite tracked by
`precor-9_3x-344`: `sdkconfig.defaults` names
`partitions_esp32tap.csv`, but the file is currently untracked and hidden by
the repository's global `*.csv` ignore. The implementation must either
explicitly unignore and track that exact source file or generate it
deterministically from a tracked source and verify the result. Generated
flash bundles may not be removed from Git until a clean checkout can build
both production and QEMU bundles without borrowing any old output.

### Manifest

Each successful branch of `tools/build.sh` will write an atomic manifest beside
its completed flash bundle only after all existing build gates succeed.
`ONLY=qemu`, `ONLY=prod`, and the normal `ONLY=both` invocation therefore
publish the same schema for the outputs they build. Each manifest will contain:

- a schema version;
- artifact kind (`production` or `qemu-test`);
- enabled features and Cargo profile;
- an exact expected-member list plus SHA-256 and byte size for
  `esp32tap.bin`, `bootloader.bin`, `partition-table.bin`, `flash_args`, and
  the copied generated `sdkconfig`;
- a deterministic digest of all declared build inputs;
- the immutable Docker image ID, the build-image recipe fingerprint, and the
  configured image tag;
- exact IDF commit, Rust toolchain, target, linker, esptool/component-lock,
  Cargo profile, and feature identities; and
- the generated sdkconfig digest (also present as a bundle member).

The input digest will hash path names and file contents in sorted order. Its
declared input set includes:

- all Rust workspace manifests, lockfiles, configuration, linker/build files,
  and source used by the firmware feature set;
- `esp32tap`, `safety_core`, `program_core`, `reqbudget`, `ble_core`, and
  `coach_core` inputs;
- normal and QEMU sdkconfig defaults;
- partition/build metadata consumed by the build; and
- the build/provenance scripts, container recipe, component locks, linker
  configuration, and toolchain selectors themselves.

Dirty and untracked source files are hashed from the working tree, not from
`HEAD`. Git timestamps and output-directory timestamps are never inputs.

The build creates an immutable temporary snapshot of the declared working-tree
inputs while holding the existing exclusive build lock. Snapshot creation
happens before any build gate. The snapshot includes tracked, dirty, and
relevant untracked files with their repository-relative paths. All four
existing pre-build gates run from that snapshot, and Cargo and ESP-IDF compile
only that snapshot; none of them reads the live bind mount.

The snapshot input declaration includes the gates and every transitive input
they consume: Python imports and fixtures, `design.py`, the safety-model tests
and manifests, the committed C++ parity core/native harness, generated-model
source inputs, and the Rust workspace/build inputs listed above. A structural
test fails when a gate imports, opens, compiles, or invokes a repository file
that the input declaration does not classify.

The snapshot's content digest is the manifest input digest. After all four
gates and the bundle build complete but before publication, the builder
recomputes the live working-tree digest and requires it to equal the snapshot
digest. A later live edit therefore invalidates publication, while a transient
edit-and-revert during gates or compilation cannot affect the artifact because
no consumer can see the live tree. Temporary snapshots and unpublished
bundles are removed on every exit.

The build image is created through a tracked wrapper which fingerprints the
Dockerfile plus its declared build context and writes that fingerprint into an
OCI image label. Before Cargo starts, `build.sh` requires the configured image
to carry the current recipe label; a changed recipe with an old local image is
rejected with the image-rebuild command. The verifier recomputes the recipe
fingerprint, resolves the configured local image to its immutable Docker image
ID, and verifies both the label binding and the manifest values. A mutable tag
alone, or an unlabeled/mislabeled image, is never accepted as toolchain
identity.

### Fixture enforcement

Before acquiring a port, starting Docker, merging flash, or launching QEMU,
every Rust QEMU entry point will:

1. require the complete expected bundle and manifest;
2. recompute the declared input digest;
3. verify manifest schema, artifact kind, feature/profile/toolchain identity,
   exact member set, every member's size and SHA-256, and generated config;
   and
4. fail with one exact remediation command when anything differs:
   `ONLY=qemu bash tools/build.sh` or `ONLY=prod bash tools/build.sh`.

This enforcement covers both pytest families:
`tools/qemu_scenarios` and `tools/qemu_harness`. Their session-scoped image
fixtures are mandatory provenance checks: they call the checker and use
`pytest.fail`, never `pytest.skip`, for a missing, stale, or invalid required
bundle. The check runs before Docker availability checks and before any test
or fixture may read an artifact. This includes artifact-only tests such as the
production-image surface check, which never construct a `QemuSession`.

The locally rooted `qemu_harness/conftest.py` is therefore an explicit,
allowlisted committed harness strengthening. It is committed rather than left
as a dirty edit, and `verify_harness_copy.py` is updated to verify the new
approved baseline. `qemu_session.py` also invokes the checker before its first
`_lease()` call as defense in depth for non-pytest callers; it is not the sole
enforcement point.

The Rust `qemu_smoke.sh` entry becomes a checked wrapper around the read-only
shared smoke implementation. The normal sweep verifies the production bundle
before its smoke launch and the QEMU bundle before pytest.

Verification and flash assembly hold the existing shared/read build lease
continuously, including under xdist, so an exclusive build cannot replace a
member after it was hashed. Automatic rebuild first releases the read lease,
acquires the exclusive build lease, publishes atomically, releases it, then
reacquires the read lease and verifies again. The checker will also have a
cheap standalone mode so the fast lane and CI can validate provenance without
starting pytest.

## Impact-Aware Fast Lane

Add one command, provisionally `tools/fast.sh`, backed by a small testable
selector. With no revision arguments it inspects the union of staged,
unstaged, and untracked working-tree changes. For committed work it accepts an
authoritative `--base REV` or `--range REV1..REV2` and derives NUL-delimited
Git name/status records, including both sides of renames and deleted paths.
Explicit paths may augment a nonempty authoritative working-tree or
revision-derived set, but never replace it. A clean tree requires
`--base`/`--range`; explicit paths alone are rejected for committed work. A
zero-change selection is always an error, not an empty successful gate.

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

Every changed or deleted path must resolve to at least one policy. Shared build files,
sdkconfig, the QEMU harness, safety/control interfaces, feature flags, route
registration, the selector itself, and unknown paths select the conservative
broad lane. No rule may silently select an empty gate set.

The fast lane runs in this order:

1. cheap syntax, structural, and affected host gates;
2. provenance check only when the selected lane consumes a firmware artifact;
3. one incremental build only if such a gate needs it and the checker reports
   a recognized missing or stale artifact;
4. selected focused QEMU gates with their currently proven worker counts; and
5. a summary of selected policies, wall time per gate, image identity, and the
   exact reason for any broad fallback.

Documentation-only lanes skip artifact provenance and firmware building unless
the changed document is itself an executable build/test input.

Broad fallback executes the existing `tools/sweep.sh` verbatim; it does not
copy or reconstruct the sweep's gate list. The sweep retains its aggregate
failure reporting. Production and QEMU manifests are published by its normal
`ONLY=both` build and are checked at their respective consumers.

The selector does not use `-n auto`. Existing worker counts of three or four
remain ceilings until repeated benchmarks show a lower p95 wall time without
new flakes or guest-time distortion.

## Correctness and Failure Behavior

- A missing or recognized stale artifact is a hard failure in direct pytest
  or smoke use.
- The fast lane may automatically rebuild once only for those two recognized
  states, then re-check it.
- A malformed manifest, unknown schema, member mismatch not attributable to
  current inputs, checker exception, toolchain-policy failure, source mutation
  during build, build failure, or lock failure does not trigger a rebuild
  loop. It fails with a distinct exit code and diagnostic.
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
- bootloader, partition table, flash arguments, sdkconfig, or member-set
  changes invalidate the bundle;
- a working-tree mutation during the build prevents manifest publication, and
  a transient edit-and-revert cannot enter the immutable build snapshot;
- mutable image tags cannot conceal a Docker image/recipe change, and an image
  without the current recipe label is rejected before compilation;
- every tracked workspace path is classified or forces broad fallback;
- multiple changed paths produce the union of their gates;
- revision ranges handle clean trees, deletions, and renames without trusting
  a caller-supplied partial path list;
- unknown and cross-cutting paths select broad;
- documentation-only changes do not build firmware; and
- a QEMU-selected change builds at most once per fast-lane invocation.

Integration tests will prove:

- both direct pytest families refuse the known tracked stale-image shape before
  port allocation or Docker;
- missing required bundles fail rather than skip, including artifact-only test
  selection that never creates a QEMU session;
- direct smoke refuses a stale production bundle before Docker;
- a fresh build creates a valid manifest and focused QEMU scenario passes;
- modifying a relevant source invalidates the artifact immediately;
- all four existing host build gates execute against the identical immutable
  snapshot compiled into the artifact, and undeclared transitive gate inputs
  fail the structural completeness test;
- normal and deep sweep command lists are unchanged; and
- each logical QEMU test still receives a fresh guest and flash image.

Benchmark output will record cold/warm build time, provenance-check time,
selected gate time, one- and five-minute host load, image identity, and
pass/fail. Before implementation, capture the corresponding broad-lane
baseline. Acceptance uses ten alternating warm baseline/candidate samples and
three cold-build samples whose starting one-minute loads are within 20% of
their paired run. Report median and p95 wall time and observed flake rate.

The implementation is accepted only when:

- provenance rejection p95 is under one second;
- a representative host-only lane p95 is under five seconds;
- a representative localized program or HTTP firmware lane has warm p95 under
  30 seconds and at least 50% lower median wall time than its prior broad gate;
- all ten warm candidate samples pass with no retries; and
- assertions, guest isolation, deadlines, release commands, and deep commands
  are byte-for-byte or structurally proven unchanged where applicable.

If host load cannot satisfy the comparison band, the benchmark is postponed;
the implementation is not accepted on incomparable timing data.

## Deferred Optimizations

After the fast lane has timing history, benchmark representative suites at
`-n 2`, `-n 3`, and `-n 4`. Change a worker count only when repeated p95 wall
time improves with zero additional flakes.

Container reuse may later avoid startup overhead only if each logical test
still starts a new QEMU process from a private immutable flash seed. Live-guest
pooling is rejected because its state leakage would invalidate the safety
evidence.
