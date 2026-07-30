# Esp32Tap Provenance-Safe Fast Inner Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject stale firmware before QEMU starts and run a conservative focused developer gate, without weakening or duplicating the normal/deep release sweeps.

**Architecture:** A pure-Python core creates immutable source snapshots, describes all build inputs, attests complete flash-bundle generations, owns the build-lock protocol, and verifies toolchain identity. Builds publish versioned generations behind atomically replaced symlinks. A separate pure selector derives authoritative changes from Git and a thin runner executes host gates, performs at most one recognized stale/missing rebuild, and then runs focused QEMU cases.

**Tech Stack:** Python 3 standard library, Bash, Git, Docker/OCI labels, Cargo + ESP-IDF, pytest/pytest-xdist, flock, existing QEMU harness.

**Design:** `docs/superpowers/specs/2026-07-30-esp32tap-fast-inner-loop-design.md`

---

## Fixed boundaries

- `tools/sweep.sh` normal and `DEEP` command lists are fingerprinted before
  implementation and are not edited.
- Assertions, timeouts, worker counts, fresh guest/flash isolation, and
  hardware-versus-QEMU evidence labels are unchanged.
- The physical worktree, not the logical branch name, keys build locks and
  caches.
- Generated bundles, targets, snapshots, and benchmark data never enter Git.
- Every behavior change follows RED → minimal GREEN → focused regression →
  commit.
- Build/QEMU tasks record wall time, load, image bytes, and image SHA.

## Planned files

| File | Responsibility |
|---|---|
| `tools/artifact_inputs.py` | Declared inputs, NUL-safe Git enumeration, immutable snapshots, transitive gate completeness |
| `tools/artifact_provenance.py` | Lock path/API, manifest schema, bundle/toolchain verification, atomic generation publication |
| `tools/build_image.sh` | Exact Docker context digest and recipe-labeled image build/check |
| `tools/build.sh` | Snapshot-only gates/build and versioned generation publication |
| `tools/fast_select.py` | Authoritative changes and exact policy table |
| `tools/fast.sh` | One-rebuild inner-loop orchestration |
| `tools/benchmark_fast.py` | Load-matched baseline/candidate sampling and statistics |
| `tools/fixtures/sweep_contract_base.json` | Reviewed normal/DEEP command fingerprints at implementation base |
| `tools/test_*.py` | Pure host tests for each unit |

---

### Task 0: Capture the implementation baseline before changing source

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/fixtures/sweep_contract_base.json`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_sweep_contract.py`
- Create outside the repository:
  `/tmp/esp32tap-fast-baseline-<physical-worktree-key>.json`

- [ ] **Step 1: Record immutable base identity**

```bash
BASE_SHA="$(git rev-parse HEAD)"
git status --short
```

Expected: clean worktree. Store `BASE_SHA` in the fixture and bead notes.

- [ ] **Step 2: Write the failing sweep fingerprint test**

The test parses `tools/sweep.sh` without executing it and compares:

- ordered top-level `run NAME ...` argv;
- ordered `DEEP`-only `run NAME ...` argv;
- final aggregate-failure behavior.

Fixture shape:

```json
{
  "base_sha": "<40 hex>",
  "normal": [{"name": "harnesslock", "argv": ["python3", "tools/verify_harness_copy.py"]}],
  "deep": [],
  "aggregates_failures": true
}
```

Populate the complete arrays from `git show "$BASE_SHA":.../tools/sweep.sh`.

- [ ] **Step 3: Verify RED then GREEN**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_sweep_contract.py -q
```

RED: fixture absent/incomplete. GREEN: exact current sweep passes.

- [ ] **Step 4: Rebuild current source before timing**

The tracked image is known stale. Run:

```bash
docker build -t esp32tap-rust:build hardware/Esp32Tap/firmware/esp32_rs
env ONLY=qemu bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
```

Expected: current-source image, not the 1,299,008-byte tracked blob. Record
bytes and SHA.

- [ ] **Step 5: Capture pre-implementation timing**

Run three warm samples each, without changing tests:

```bash
/usr/bin/time -f '%e' env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios \
  python3 -m pytest test_reviewer_attacks.py -q -n 3
/usr/bin/time -f '%e' cargo test \
  --manifest-path hardware/Esp32Tap/firmware/esp32_rs/program_core/Cargo.toml -q
```

Store command, SHA, image SHA/bytes, start load averages, exit status, and wall
time in `/tmp/esp32tap-fast-baseline-<key>.json`. This is orientation data;
Task 11 performs the required alternating acceptance comparison.

- [ ] **Step 6: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/fixtures/sweep_contract_base.json \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_sweep_contract.py
git commit -m "test(Esp32Tap): freeze release sweep contract"
```

---

### Task 1: Track the missing partition source without removing old artifacts

**Files:**

- Modify: `.gitignore`
- Track: `hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py`

- [ ] **Step 1: Write RED**

Test that the exact CSV is tracked and not ignored using
`git check-ignore --no-index`. Do **not** yet assert that old bundles are
untracked.

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py -q
```

Expected: FAIL because global `*.csv` hides the file.

- [ ] **Step 2: Minimal GREEN**

Add:

```gitignore
!hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv
```

Then:

```bash
git add -f hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py -q
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore \
  hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py
git commit -m "build(Esp32Tap): track the partition source"
```

Record Task 1 HEAD as `CLEAN_BASE_SHA` in bead notes. Task 11 uses this SHA,
not Task 0, because it is the first clean-build-capable baseline without any
fast-loop behavior.

---

### Task 2: Implement immutable build inputs and snapshots

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_inputs.py`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py`

- [ ] **Step 1: Write executable RED tests**

Use temporary Git repositories and assert exact values:

```python
def test_same_bytes_different_mtime_same_digest(repo):
    before = working_digest(repo)
    os.utime(repo / "src/lib.rs", (1, 1))
    assert working_digest(repo) == before

def test_same_size_content_change_changes_digest(repo):
    before = working_digest(repo)
    (repo / "src/lib.rs").write_text("bbbb\n")
    assert working_digest(repo) != before

def test_snapshot_ignores_later_live_edit(repo, tmp_path):
    snap = create_snapshot(repo, tmp_path / "snapshot", target_cache=tmp_path / "target")
    (repo / "src/lib.rs").write_text("changed\n")
    assert (snap.root / "src/lib.rs").read_text() != "changed\n"
```

Also test dirty tracked, relevant untracked, deletion, rename, safe internal
symlink preservation, escaping-symlink rejection, output exclusion, physical
worktree key, and source mtimes strictly newer than prior target-cache output.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py -q
```

Expected: import failure.

- [ ] **Step 3: Implement exact API**

```python
@dataclass(frozen=True)
class Snapshot:
    root: Path
    digest: str
    paths: tuple[str, ...]
    worktree_key: str

def declared_inputs(repo_root: Path) -> tuple[str, ...]: ...
def working_digest(repo_root: Path) -> str: ...
def target_cache(repo_root: Path, kind: str) -> Path: ...
def create_snapshot(repo_root: Path, destination: Path, target_cache: Path) -> Snapshot: ...
def verify_gate_input_completeness(snapshot_root: Path) -> None: ...
```

Implementation rules:

- candidate argv: `git ls-files -z -co --exclude-standard`;
- sorted length-prefixed relative path + file bytes;
- internal symlinks copied as symlinks, including `tools/check_pins.py`;
- escaping/broken symlinks fail;
- exact output directories only are excluded:
  `esp32_rs/build/`, `esp32_rs/build_qemu_test/`, `esp32_rs/.artifacts/`,
  every crate `target/`, caches, secrets, and unrelated untracked files;
- files named `build.rs`, `tools/build.sh`, `tools/build_image.sh`, and
  `firmware/build_safety_manifest.py` are explicitly tested as declared;
- target cache:
  `/tmp/esp32tap-target-<sha256(real-worktree)[:12]>/<prod|qemu>`;
- prod/QEMU never share target directories;
- snapshot mtimes set to `max(time.time_ns(), newest_target_mtime_ns + 1)`;
- completeness executes all four host gates in the minimal snapshot with the
  live checkout unavailable. Any missing import/read/native invocation fails.

Do not edit through the `check_pins.py` symlink.

- [ ] **Step 4: GREEN and commit**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py -q
git add hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_inputs.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py
git commit -m "build(Esp32Tap): snapshot complete build inputs"
```

---

### Task 3: Implement bundle manifests and continuous lock ownership

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py`

- [ ] **Step 1: Write RED tests**

Test exact expected members:

```python
EXPECTED = {
    "esp32tap.bin", "bootloader.bin", "partition-table.bin",
    "flash_args", "sdkconfig",
}
```

Required tests:

- complete manifest round trip;
- missing=20, stale=21, invalid=22, internal=23;
- missing/extra/changed member invalid;
- schema/kind/feature/profile mismatch invalid;
- IDF commit, Rust verbose version, target triple, linker version, esptool
  version, component-lock digest, Docker image ID, recipe label mismatch each
  invalid;
- child process launched through `locked_exec` retains shared FD and blocks an
  exclusive publisher until exit;
- exception releases lock;
- lock path matches existing resolved physical-worktree algorithm.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py -q
```

- [ ] **Step 3: Implement exact schema/API**

```python
@dataclass(frozen=True)
class Toolchain:
    image_id: str
    recipe_sha256: str
    image_tag: str
    idf_commit: str
    rustc_verbose: str
    target: str
    linker_version: str
    esptool_version: str
    component_lock_sha256: str
    profile: str
    features: tuple[str, ...]

@contextmanager
def shared_bundle(repo_root: Path, kind: str): ...

def verify_locked(bundle: Path, expected: Toolchain, input_digest: str) -> Result: ...
def locked_exec(repo_root: Path, kind: str, argv: list[str]) -> NoReturn: ...
def locked_exec_many(repo_root: Path, kinds: tuple[str, ...], argv: list[str]) -> NoReturn: ...
def publish_generation_atomic(staging: Path, public_link: Path, manifest: dict) -> None: ...
```

Publication layout:

```text
.artifacts/prod/<manifest-digest>/
.artifacts/qemu/<manifest-digest>/
build -> .artifacts/prod/<manifest-digest>
build_qemu_test -> .artifacts/qemu/<manifest-digest>
```

Create generation fully, fsync members/manifest/directory, create a temporary
relative symlink, then `os.replace(temp_link, public_link)`. Readers resolve
the link only while holding the shared lock. On failure, old link remains.
`publish_generation_atomic` also owns a tested legacy migration: when the
public path is the tracked/pre-migration real directory, it atomically renames
that directory to an exact transaction swap, records its device and inode in a
durable marker, and installs the symlink with an atomic exchange. A failure
before that exchange leaves the original directory untouched. After durable
publication, it may use a no-replace rename to durably retire the cooperative
transaction swap to a task-specific tombstone.

All cooperating ESP32Tap builders and readers must honor the physical lock.
Fresh 256-bit transaction names protect against accidental collision, and
normal directory permissions protect against other UIDs. An active same-UID
process replacing a current transaction pathname is explicitly outside the
threat model: unprivileged Linux provides no identity-conditional
rename/exchange primitive for that case. No pathname validation is claimed to
close that race.

Prior transaction evidence remains inert even if a same-UID process changes
it. Startup and publication never scan, validate, rename, unlink, or otherwise
mutate prior markers, swaps, symlinks, or tombstones. A collision between a
fresh token and an existing path fails without overwriting it. Inspection or
garbage collection is allowed only under an offline, exclusive maintenance
workflow with stronger isolation than the online lock. Tests cover cooperative
success, every crash point, inert forged prior evidence, and fresh-token
collisions while keeping the old or new public state available.

CLI `exec --kind ... -- COMMAND` sets the lock FD inheritable, verifies, and
`os.execvp`s COMMAND so smoke delegation retains the lock continuously.
CLI `exec-many --kind production --kind qemu-test -- COMMAND` acquires kinds
in sorted order, verifies both, and preserves both inheritable FDs across the
single `execvp`.

- [ ] **Step 4: GREEN and commit**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py -q
git add hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py
git commit -m "build(Esp32Tap): attest locked bundle generations"
```

---

### Task 4: Bind Docker image identity to an exact context

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/.dockerignore`
- Create executable: `hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/README.md`

- [ ] **Step 1: RED**

Tests use a fake `docker` binary and assert:

- Dockerfile/context change changes recipe digest;
- build/target/cache change does not;
- absent/stale label rejects;
- matching OCI labels return immutable `.Id` and the creation-time toolchain
  attestation without invoking `docker run`;
- script Git mode is `100755`.

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image.py -q
```

- [ ] **Step 2: Implement**

`.dockerignore` denies everything then allows only `Dockerfile` and any exact
future context files. `build_image.sh --recipe` hashes the Dockerfile plus the
sorted allowed context. Default mode builds a temporary image, runs the exact
toolchain probes once inside it, validates their output, and commits the final
image with these inspectable OCI labels:

```text
org.treddy.esp32tap.recipe-sha256
org.treddy.esp32tap.toolchain-json
```

`build_image.sh --check` performs only `docker image inspect`: it compares the
recipe label with the current recipe, validates the creation-time toolchain
JSON, and emits the complete `Toolchain` plus immutable image `.Id`. It never
starts a container. Toolchain probes use exact commits/verbose versions, never
`git describe` alone. Set mode:

```bash
chmod 0755 hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh
```

- [ ] **Step 3: GREEN and commit**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image.py -q
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh
git ls-files -s hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh
git add hardware/Esp32Tap/firmware/esp32_rs/.dockerignore \
  hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image.py \
  hardware/Esp32Tap/firmware/esp32_rs/README.md
git commit -m "build(Esp32Tap): bind toolchain image to recipe"
```

---

### Task 5: Build and publish from one immutable gated snapshot

**Files:**

- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_snapshot_build.py`

- [ ] **Step 1: RED**

With fake Docker/gates, assert:

- snapshot created before recipe check and first host gate;
- recipe checker and all gates execute from snapshot paths;
- container mounts snapshot, not live repo;
- toolchain fields all reach manifest;
- persistent targets are physical-worktree-keyed, prod/QEMU separated, and
  mounted at stable `/target/<kind>`;
- same-size source edit rebuilds;
- two worktrees never share targets/locks;
- container writes run as host UID/GID and cleanup succeeds;
- live mutation prevents publication;
- readers observe old or new symlink generation, never absent/partial;
- failed gate/build preserves old generation;
- `ONLY=both` publishes both manifests.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_snapshot_build.py -q
```

- [ ] **Step 3: Implement exact order**

Under exclusive lock:

1. create immutable snapshot;
2. run snapshot's `build_image.sh --check`;
3. run four host gates from snapshot;
4. mount snapshot at `/project`, target at `/target`, cache at `/cargo`;
5. run container as `$(id -u):$(id -g)`;
6. build requested kinds into snapshot staging with exact stable paths;
7. collect complete toolchain identity and five bundle members;
8. verify live digest still equals snapshot digest;
9. publish versioned generations and atomically replace symlinks;
10. trap-removes task-specific snapshot/staging.

- [ ] **Step 4: Host GREEN**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_snapshot_build.py -q
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
```

- [ ] **Step 5: Create an exact temporary proof commit**

Stage the Task 5 files and create a commit object without moving the branch:

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_snapshot_build.py
PROOF_TREE="$(git write-tree)"
PROOF_COMMIT="$(
  printf '%s\n' 'temporary Task 5 publication proof' |
    git commit-tree "$PROOF_TREE" -p HEAD
)"
```

- [ ] **Step 6: Real build and legacy migration proof**

Do not replace the tracked `build_qemu_test` directory in the implementation
worktree. Prove the first atomic publication from the temporary commit that
contains Task 5, including migration of that real directory to a symlink, in a
disposable detached worktree:

```bash
PROOF_WT="$(mktemp -d /tmp/esp32tap-publish-proof.XXXXXX)"
git worktree add --detach "$PROOF_WT" "$PROOF_COMMIT"
cleanup_proof() { git worktree remove --force "$PROOF_WT"; }
trap cleanup_proof EXIT
"$PROOF_WT/hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh"
/usr/bin/time -v env ONLY=both \
  bash "$PROOF_WT/hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh"
test -L "$PROOF_WT/hardware/Esp32Tap/firmware/esp32_rs/build"
test -L "$PROOF_WT/hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test"
python3 "$PROOF_WT/hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py" \
  verify --kind production \
  "$PROOF_WT/hardware/Esp32Tap/firmware/esp32_rs/build"
python3 "$PROOF_WT/hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py" \
  verify --kind qemu-test \
  "$PROOF_WT/hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test"
cleanup_proof
trap - EXIT
```

Record timing/load/bytes/SHA.

- [ ] **Step 7: Commit**

```bash
git commit -m "build(Esp32Tap): publish gated snapshot builds"
```

---

### Task 6: Prove clean checkout, then remove generated bundles from Git

**Files:**

- Modify: `.gitignore`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py`
- Remove from index: five `build_qemu_test` tracked files

- [ ] **Step 1: Clean-checkout proof before deletion**

Create a detached temporary worktree at Task 5 HEAD. Resolve and remove only
its known output paths, then build from scratch:

```bash
CLEAN_HEAD="$(git rev-parse HEAD)"
CLEAN_WT="$(mktemp -d /tmp/esp32tap-clean-proof.XXXXXX)"
git worktree add --detach "$CLEAN_WT" "$CLEAN_HEAD"
cleanup_clean() { git worktree remove --force "$CLEAN_WT"; }
trap cleanup_clean EXIT
rm -rf -- \
  "$CLEAN_WT/hardware/Esp32Tap/firmware/esp32_rs/build" \
  "$CLEAN_WT/hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test" \
  "$CLEAN_WT/hardware/Esp32Tap/firmware/esp32_rs/.artifacts"
env ONLY=both \
  bash "$CLEAN_WT/hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh"
python3 "$CLEAN_WT/hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py" \
  verify --kind production \
  "$CLEAN_WT/hardware/Esp32Tap/firmware/esp32_rs/build"
python3 "$CLEAN_WT/hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py" \
  verify --kind qemu-test \
  "$CLEAN_WT/hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test"
cleanup_clean
trap - EXIT
```

Expected: both valid from clean source.

- [ ] **Step 2: RED layout assertion**

Extend the layout test to require no tracked files beneath the exact output
directories `esp32_rs/build/` and `esp32_rs/build_qemu_test/`, while explicitly
allowing source files such as `build.rs` and `tools/build*.sh`. Require ignored
public links/generations. Run; expected FAIL.

- [ ] **Step 3: Remove only from index**

Add:

```gitignore
hardware/Esp32Tap/firmware/esp32_rs/build
hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test
hardware/Esp32Tap/firmware/esp32_rs/.artifacts/
hardware/Esp32Tap/firmware/esp32_rs/.bench/
```

Then `git rm --cached` the five tracked files. Do not recursively delete a
workspace root.

- [ ] **Step 4: GREEN**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py -q
git add .gitignore hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py
git add -u hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test
```

- [ ] **Step 5: Publish valid local bundles for subsequent real tests**

With the generated paths now ignored, replace the retained legacy output with
current symlink generations and verify both before Task 7:

```bash
env ONLY=both bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py \
  verify --kind production hardware/Esp32Tap/firmware/esp32_rs/build
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py \
  verify --kind qemu-test hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test
```

- [ ] **Step 6: Commit**

```bash
git commit -m "build(Esp32Tap): untrack generated flash bundles"
```

---

### Task 7: Enforce provenance at every QEMU and artifact entry

**Files:**

- Modify: `tools/qemu_harness/conftest.py`
- Modify: `tools/qemu_scenarios/conftest.py`
- Modify: `tools/qemu_harness/qemu_session.py`
- Modify: `tools/qemu_harness/run.sh`
- Modify: `tools/run_harness.sh`
- Replace symlink with executable: `tools/qemu_smoke.sh`
- Modify: `tools/verify_harness_copy.py`
- Create: `tools/test_provenance_entrypoints.py`

All paths above are under
`hardware/Esp32Tap/firmware/esp32_rs/`.

- [ ] **Step 1: RED from checked fixture**

Materialize the pre-migration five blobs with:

```bash
git show <implementation-base>:hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test/esp32tap.bin
```

and corresponding members into a temp fixture without a manifest. Do not rely
on the current local output.

Tests assert missing/stale bundles fail, never skip, before fake Docker/port
functions; artifact-only S6 checks both bundles and retains both shared locks
through every fixture read; `QemuSession` acquires its shared lock then
verifies before first port and holds it through assembly/session teardown;
writer cannot publish during assembly; smoke/run_harness/harness-run cannot
delegate before verification.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py -q
```

- [ ] **Step 3: Implement**

- both conftests use mandatory session fixtures with `pytest.fail`, retaining
  the shared-lock context managers for the entire pytest session;
- `QemuSession` holds `shared_bundle()` through flash assembly/session life;
- `qemu_harness/run.sh` becomes a checked delegation to `../run_harness.sh`;
- `run_harness.sh` uses `artifact_provenance.py exec-many` for production and
  QEMU so both FDs survive exec;
- replace smoke symlink with an executable `artifact_provenance.py exec`
  wrapper around the unchanged C++ smoke script;
- update exact SHA pins/rationales for all strengthened harness files.

Set `0755` on both executable wrappers and assert Git mode `100755`.

- [ ] **Step 4: GREEN and focused QEMU**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py -q
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/verify_harness_copy.py
env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios \
  python3 -m pytest test_program.py -k pause_of_stopped -q
```

- [ ] **Step 5: Commit**

Stage every listed source/test file explicitly, then commit:

```bash
git add \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/conftest.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/conftest.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/qemu_session.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/run.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/run_harness.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_smoke.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/verify_harness_copy.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py
git commit -m "test(Esp32Tap): reject stale artifacts before QEMU"
```

---

### Task 8: Implement authoritative conservative selection

**Files:**

- Create: `tools/fast_select.py`
- Create: `tools/test_fast_select.py`

Paths are under `hardware/Esp32Tap/firmware/esp32_rs/`.

- [ ] **Step 1: Define exact Git argv and policy table in tests**

Working tree:

```text
git -C <repo-root> diff -M --name-status -z --
git -C <repo-root> diff -M --cached --name-status -z --
git -C <repo-root> ls-files --others --exclude-standard -z --
```

Discover `<repo-root>` with `git -C <script-directory> rev-parse
--show-toplevel`. `--base REV` adds `git -C <repo-root> diff -M --name-status
-z REV --`; `--range A..B` adds `git -C <repo-root> diff -M --name-status -z
A..B --`. Both union the working-tree commands above. Options conflict; clean
committed work requires one. Renames include old and new paths. Explicit paths
must be repository-relative, normalized, non-escaping, and only augment a
nonempty authoritative set. A changed path outside `esp32_rs`, unless it is a
documentation-only path, selects broad.

Exact initial table:

| Pattern | Policy | Exact host argv | Exact QEMU argv | artifacts |
|---|---|---|---|---|
| `hardware/Esp32Tap/firmware/esp32_rs/program_core/**` | program-host | `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/program_core/Cargo.toml -q` | `<none>` | none |
| `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/program.rs`, `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/tasks/interval_executor.rs`, `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/control.rs` | program-control | `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/program_core/Cargo.toml -q`; `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/safety_core/Cargo.toml -q`; `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/difftest/Cargo.toml -q` | `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_program.py -q -n 4`; `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_reviewer_attacks.py -q -n 3 -k console_takeover` | qemu |
| `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/**`, `hardware/Esp32Tap/firmware/esp32_rs/reqbudget/**` | request-api | `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/reqbudget/Cargo.toml -q` | `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_http_entry.py -q`; `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_reviewer_attacks.py -q -n 3 -k "body_policy or unread_declared_body"` | qemu |
| `hardware/Esp32Tap/firmware/esp32_rs/safety_core/**` | safety | `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/safety_core/Cargo.toml -q`; `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/difftest/Cargo.toml -q` | `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_normal_exit.py -q`; `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_reviewer_attacks.py -q -n 3` | qemu |
| `hardware/Esp32Tap/firmware/esp32_rs/ble_core/**`, `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/ble/**` | ble | `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/ble_core/Cargo.toml -q` | `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_ble_degraded.py -q -n 3`; `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_ble_control_point.py -q -n 4` | qemu |
| `hardware/Esp32Tap/firmware/esp32_rs/coach_core/**`, `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/coach.rs` | coach | `cargo test --manifest-path hardware/Esp32Tap/firmware/esp32_rs/coach_core/Cargo.toml -q` | `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_coach.py -q -n 4` | qemu |
| `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/records.rs`, `hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/store.rs` | storage | `<none>` | `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_records.py -q -n 4`; `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_store_persistence.py -q`; `env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m pytest test_store_power_loss.py -q -n 4` | qemu |
| `docs/**`, `*.md` outside declared executable inputs | docs | `python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py -q` | `<none>` | none |
| build scripts, Dockerfile/context, sdkconfig/partition source, harness, selector/runner, route-registration diff, outside subtree, unknown | broad | `env -C hardware/Esp32Tap/firmware/esp32_rs bash tools/sweep.sh` | `<embedded in sweep>` | production + qemu |

Policy precedence is broad first. A diff hunk adding/removing
`httpd_register_uri_handler`, `httpd_uri_t`, or `register_*_handlers` in any
source file is an exact route-registration trigger and therefore broad even
when its path otherwise matches a focused row. Tests cover each trigger.
`check_log_contract.sh` is deliberately absent from focused host argv because
it reads both firmware binaries; the unchanged broad sweep runs it only after
its build, while focused policies rely on provenance plus their locked QEMU
gates.

Real-repository test enumerates `git ls-files -z`; every path selects a named
policy or explicit broad fallback. Malformed selector output/process failure
means broad.

- [ ] **Step 2: RED, implement, GREEN**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py -q
```

Implement `Selection` JSON with paths, policies, exact argv, workers,
artifact kinds, and broad reason. Rerun to GREEN.

- [ ] **Step 3: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/fast_select.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py
git commit -m "test(Esp32Tap): select conservative fast gates"
```

---

### Task 9: Implement the one-rebuild runner

**Files:**

- Create executable: `tools/fast.sh`
- Create: `tools/test_fast_runner.py`

- [ ] **Step 1: RED**

Fake-command tests assert:

- host before artifact;
- docs skips provenance;
- valid reuses;
- only exit 20/21 rebuilds once then rechecks;
- exit 22/23 never rebuilds;
- build failure prevents QEMU;
- malformed selector invokes broad sweep;
- broad invokes `bash tools/sweep.sh` verbatim;
- per-gate time/log and artifact identity printed.

- [ ] **Step 2: Implement and GREEN**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py -q
chmod 0755 hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh
```

Use task-specific `mktemp -d` logs and exact argv arrays; no `eval`.

- [ ] **Step 3: Exact real integration setup**

Stage Task 9 and create a temporary commit object so the disposable worktree
contains `fast.sh` without moving the implementation branch. Then make and
commit one comment-only localized change to `program_core/src/state.rs`:

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py
FAST_PROOF_TREE="$(git write-tree)"
FAST_PROOF_COMMIT="$(
  printf '%s\n' 'temporary Task 9 integration proof' |
    git commit-tree "$FAST_PROOF_TREE" -p HEAD
)"
FAST_WT="$(mktemp -d /tmp/esp32tap-fast-proof.XXXXXX)"
git worktree add --detach "$FAST_WT" "$FAST_PROOF_COMMIT"
cleanup_fast() { git worktree remove --force "$FAST_WT"; }
trap cleanup_fast EXIT
git -C "$FAST_WT" add \
  hardware/Esp32Tap/firmware/esp32_rs/program_core/src/state.rs
git -C "$FAST_WT" commit -m "test: localized fast-loop proof"
env -C "$FAST_WT/hardware/Esp32Tap/firmware/esp32_rs" \
  bash tools/fast.sh --base HEAD~1
```

Expected: authoritative one-commit change, program-host policy, no QEMU. Make a
second temp commit in `esp32tap/src/net/program.rs`; run the same command
twice. The first invocation expects exactly one missing/stale rebuild followed
by focused program-control cases; the second expects reuse of that current
QEMU bundle and no rebuild. Then:

```bash
git -C "$FAST_WT" status --short
cleanup_fast
trap - EXIT
```

- [ ] **Step 4: Commit**

```bash
git commit -m "test(Esp32Tap): add provenance-safe fast runner"
```

---

### Task 10: Add deterministic benchmark tooling and documentation

**Files:**

- Create executable: `tools/benchmark_fast.py`
- Create: `tools/test_benchmark_fast.py`
- Modify: `README.md`

- [ ] **Step 1: RED**

Tests assert exact nearest-rank p95, median, 20% load-pair rejection,
nonzero/retry failure, ten provenance samples, ten host samples, ten warm
firmware pairs, three cold baseline/candidate pairs, isolated cache paths, and
executable mode.

- [ ] **Step 2: Implement deterministic tool**

Inputs are two explicit command arrays plus JSON sample records. Never clear
`/tmp/rustcargo`; cold samples use new task-specific target cache directories.
Exit nonzero unless:

- recognized missing/stale provenance rejection p95 <1 s;
- host p95 <5 s;
- firmware p95 <30 s;
- candidate median at least 50% below the exact Task 0 broad reviewer command;
- 10/10 candidates pass without retries;
- each accepted pair starts within 20% one-minute load.

- [ ] **Step 3: GREEN and docs**

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_benchmark_fast.py -q
chmod 0755 hardware/Esp32Tap/firmware/esp32_rs/tools/benchmark_fast.py
```

README documents supported image build, stale remediation, dirty/range fast
commands, unchanged release/deep gates, and benchmark procedure. Do not add
measured claims yet.

- [ ] **Step 4: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/benchmark_fast.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_benchmark_fast.py \
  hardware/Esp32Tap/firmware/esp32_rs/README.md
git commit -m "test(Esp32Tap): add fast-loop benchmark contract"
```

---

### Task 11: Measure acceptance in controlled worktrees

**Files:**

- Modify after measurement:
  - `hardware/Esp32Tap/firmware/esp32_rs/README.md`
  - `hardware/Esp32Tap/firmware/esp32_rs/2026-07-30-independent-firmware-audit.md`

- [ ] **Step 1: Create controlled baseline and candidate worktrees**

Baseline worktree at the `CLEAN_BASE_SHA` recorded after Task 1; candidate at
Task 10 HEAD. In each, create the same reproducible localized comment-only
commit in `net/program.rs`. Build separate physical-worktree-keyed
artifacts/caches. The Task 0 timing JSON remains orientation evidence only.

- [ ] **Step 2: Run alternating warm samples**

For five missing samples, point `artifact_provenance.py verify` at a
task-specific absent bundle/public link and require exit 20. For five stale
samples, keep a valid bundle unchanged, make a reversible edit to one declared
source input so the live input digest differs, verify exit 21, then restore
that source input before the next sample. Invoke the verifier directly, never
`fast.sh`, so no sample auto-rebuilds. Collect ten program-host samples
separately. Then alternate the exact Task 0 broad command
`env -C hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios python3 -m
pytest test_reviewer_attacks.py -q -n 3` in the baseline worktree with
candidate `tools/fast.sh --base HEAD~1` ten times.

For each of the three cold baseline/candidate pairs, create a fresh detached
baseline worktree and a fresh detached candidate worktree under distinct
`mktemp -d /tmp/esp32tap-bench-cold.XXXXXX` paths. Because target caches are
keyed by resolved physical worktree, each member of every pair has a unique
empty cache without adding a cache override or clearing a shared cache. Remove
each worktree with `git worktree remove --force` after recording the pair.
`benchmark_fast.py` records dataset, command argv, SHA, load, artifact
identity, time, exit, retry count, and pair index.

- [ ] **Step 3: Enforce acceptance**

```bash
python3 tools/benchmark_fast.py evaluate .bench/acceptance.json
```

If insufficient load-matched pairs exist, file a bead and rerun later. Do not
relax thresholds or claim acceptance.

- [ ] **Step 4: Update measured docs only after PASS**

Add the actual median/p95/load band/image identity and state that QEMU timing
is workflow evidence, not device cycle-time evidence.

- [ ] **Step 5: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/README.md \
  hardware/Esp32Tap/firmware/esp32_rs/2026-07-30-independent-firmware-audit.md
git commit -m "docs(Esp32Tap): record fast-loop acceptance"
```

---

### Task 12: Final integration and independent review

- [ ] **Step 1: Run all new host tests**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_sweep_contract.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_source_layout.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_snapshot_build.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_benchmark_fast.py -q
```

- [ ] **Step 2: Run normal release sweep once**

```bash
bash hardware/Esp32Tap/firmware/esp32_rs/tools/sweep.sh
```

Expected: ALL GREEN and sweep fingerprint unchanged. `DEEP=1` is unnecessary
unless implementation unexpectedly changes production memory/request paths.

- [ ] **Step 3: Audit generated state and modes**

```bash
git status --short
git ls-files hardware/Esp32Tap/firmware/esp32_rs/build \
  hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test
git ls-files -s hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/benchmark_fast.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_smoke.sh
```

Expected: no generated bundle tracked; all four scripts mode `100755`.

- [ ] **Step 4: Independent spec then quality reviews**

Provide approved design, this plan, base/head SHAs, all RED/GREEN evidence,
benchmark JSON, release result, artifact identities, and hardware limitations.
Fix every blocking finding through the same task implementer and re-review.

- [ ] **Step 5: Close bead and push**

```bash
bd close <implementation-bead> --reason="Fast loop implemented, measured, release-gated, and independently approved."
git pull --rebase
git push
bd dolt push
git status
```

If Beads reports no Dolt remote, include that exact result in the handoff.
