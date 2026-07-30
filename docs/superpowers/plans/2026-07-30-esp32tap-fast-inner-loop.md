# Esp32Tap Provenance-Safe Fast Inner Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a provenance-safe developer command that rejects stale firmware before QEMU starts and runs only conservatively affected gates, while leaving the normal and deep release sweeps unchanged.

**Architecture:** A pure-Python provenance core owns source classification, immutable snapshots, flash-bundle manifests, and verification. The existing build remains the sole producer, but runs all gates and compilation from one immutable snapshot and atomically publishes a complete attested bundle. Every direct QEMU/smoke entry point verifies that bundle, while a separate pure selector maps authoritative Git changes to focused gates and a thin shell runner performs at most one rebuild.

**Tech Stack:** Python 3 standard library, Bash, Git, Docker/OCI labels, Cargo + ESP-IDF, pytest/pytest-xdist, existing file locks and QEMU harness.

**Design:** `docs/superpowers/specs/2026-07-30-esp32tap-fast-inner-loop-design.md`

**Tracking:** Create one implementation bead before Task 1; create follow-up beads for deferred benchmark or environment failures rather than hiding them.

---

## File and Responsibility Map

- `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_inputs.py`
  - one versioned declaration of snapshot/build inputs;
  - deterministic working-tree hashing;
  - immutable snapshot creation;
  - transitive host-gate input completeness.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py`
  - flash-bundle manifest schema;
  - atomic manifest publication;
  - member/toolchain/input verification;
  - stable CLI exit codes.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py`
  - pure host tests for dirty/untracked/deleted/snapshot behavior.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py`
  - pure host tests for bundle and manifest validation.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh`
  - the only supported way to build the Rust toolchain image;
  - binds the recipe digest into an OCI label.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh`
  - exclusive lock;
  - snapshot orchestration;
  - snapshot-local host gates and container build;
  - complete temporary bundle staging and atomic publication.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/conftest.py`
  - mandatory session preflight for both production and QEMU bundles;
  - missing artifacts fail, never skip.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/conftest.py`
  - mandatory QEMU-bundle preflight for Rust-only scenarios.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/qemu_session.py`
  - defense-in-depth verification before port/build lease acquisition.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/verify_harness_copy.py`
  - exact SHA pins and rationale for approved Rust harness strengthenings.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_smoke.sh`
  - replace the symlink with a Rust-owned provenance wrapper;
  - execute the unchanged C++ smoke implementation after verification.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/run_harness.sh`
  - preflight both bundles before delegating to the shared C++ harness.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/fast_select.py`
  - authoritative changed-path discovery;
  - conservative path-to-gate selection;
  - machine-readable selection result.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py`
  - pure selector tests for dirty, staged, untracked, revision, rename,
    deletion, union, and broad fallback.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh`
  - thin inner-loop orchestrator;
  - runs host gates, verifies/rebuilds once, runs focused QEMU gates, times all
    work.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py`
  - command-planning tests using fake commands; no Docker/QEMU.
- `hardware/Esp32Tap/firmware/esp32_rs/tools/benchmark_fast.sh`
  - reproducible baseline/candidate timing protocol.
- `hardware/Esp32Tap/firmware/esp32_rs/README.md`
  - supported image-build, direct-test, fast-lane, and benchmark commands.
- `.gitignore`
  - unignore the required partition CSV;
  - ignore generated Rust flash bundles.

### Invariants throughout implementation

- Do not edit `tools/sweep.sh` command order or `DEEP` contents.
- Do not loosen a pytest timeout, assertion, event sequence, or worker count.
- Do not pool live guests or flash state.
- Do not run broad QEMU suites before focused RED/GREEN is stable.
- Restore/remove generated output from commits; only source, tests, and docs
  are committed.
- Record build time, selected-test time, host load, image bytes, and image
  SHA after each firmware-producing task.

---

### Task 1: Make source inputs reproducible from a clean checkout

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_layout.py`
- Modify: `.gitignore`
- Track: `hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv`
- Remove from Git index:
  - `hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test/bootloader.bin`
  - `hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test/esp32tap.bin`
  - `hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test/flash_args`
  - `hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test/partition-table.bin`
  - `hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test/sdkconfig`

- [ ] **Step 1: Write the failing layout test**

```python
def test_partition_source_is_tracked_and_build_bundles_are_generated():
    tracked = git("ls-files", "-z").stdout.split(b"\0")
    assert b"hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv" in tracked
    assert not any(
        p.startswith(b"hardware/Esp32Tap/firmware/esp32_rs/build")
        for p in tracked
    )
```

Also assert `git check-ignore` does not ignore the partition CSV and does
ignore both Rust output directories.

- [ ] **Step 2: Run RED**

Run:

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_layout.py -q
```

Expected: FAIL because the CSV is ignored/untracked and five QEMU outputs are
tracked.

- [ ] **Step 3: Fix ignore/source layout**

Add exact rules:

```gitignore
!hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv
hardware/Esp32Tap/firmware/esp32_rs/build/
hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test/
```

Use `git add -f` for the existing partition CSV and `git rm --cached` for the
five generated files. Do not delete the local build directory during this
task; later tests still need the known stale artifact for RED.

- [ ] **Step 4: Run GREEN and clean-checkout proof**

Run:

```bash
python3 -m pytest hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_layout.py -q
git diff --check
```

Expected: PASS; generated files are index deletions, CSV is staged source.

- [ ] **Step 5: Commit**

```bash
git add .gitignore \
  hardware/Esp32Tap/firmware/esp32_rs/partitions_esp32tap.csv \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_layout.py
git add -u hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test
git commit -m "build(Esp32Tap): separate source from generated bundles"
```

---

### Task 2: Implement deterministic inputs and immutable snapshots

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_inputs.py`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py`

- [ ] **Step 1: Write pure failing tests**

Cover:

```python
def test_digest_uses_relative_path_and_content_not_mtime(tmp_repo): ...
def test_dirty_tracked_content_changes_digest(tmp_repo): ...
def test_relevant_untracked_content_changes_digest(tmp_repo): ...
def test_deleted_and_renamed_inputs_change_digest(tmp_repo): ...
def test_snapshot_is_immutable_after_live_source_changes(tmp_repo): ...
def test_snapshot_preserves_repo_relative_layout(tmp_repo): ...
def test_output_and_git_metadata_are_never_inputs(tmp_repo): ...
def test_all_host_gate_transitive_inputs_are_declared(real_repo): ...
```

The real-repository completeness test must include the four gate scripts,
their local Python import closure, `design.py`, safety model/tests/manifests,
Rust inputs, C++ parity/native inputs, Dockerfile, build scripts, sdkconfig
defaults, and partition source.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py -q
```

Expected: collection FAIL because `artifact_inputs` does not exist.

- [ ] **Step 3: Implement the minimal API**

Expose:

```python
@dataclass(frozen=True)
class Snapshot:
    root: Path
    digest: str
    paths: tuple[str, ...]

def declared_inputs(repo_root: Path) -> tuple[str, ...]: ...
def working_digest(repo_root: Path) -> str: ...
def create_snapshot(repo_root: Path, destination: Path) -> Snapshot: ...
def verify_gate_input_completeness(repo_root: Path) -> list[str]: ...
```

Rules:

- use Git `ls-files -co --exclude-standard -z` as the candidate set;
- intersect with explicit versioned roots/files;
- sort encoded repository-relative paths;
- hash length-prefixed path and bytes;
- reject symlinks escaping the repository;
- copy declared inputs to a new task-specific `mktemp` directory;
- hash the snapshot after copying and return that digest;
- never copy `.git`, build outputs, targets, caches, secrets, or unrelated
  untracked files.

- [ ] **Step 4: Run GREEN**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py -q
```

Expected: all pure tests PASS in under two seconds.

- [ ] **Step 5: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_inputs.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py
git commit -m "build(Esp32Tap): snapshot firmware inputs deterministically"
```

---

### Task 3: Implement complete flash-bundle provenance

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py`

- [ ] **Step 1: Write failing manifest tests**

Use temporary bundles to cover:

```python
EXPECTED_MEMBERS = {
    "esp32tap.bin",
    "bootloader.bin",
    "partition-table.bin",
    "flash_args",
    "sdkconfig",
}

def test_round_trip_complete_bundle(): ...
def test_missing_manifest_is_missing_not_valid(): ...
def test_current_input_digest_mismatch_is_stale(): ...
def test_member_bytes_size_or_membership_mismatch_is_invalid(): ...
def test_wrong_kind_features_profile_or_schema_is_invalid(): ...
def test_toolchain_image_or_recipe_mismatch_is_invalid(): ...
def test_partial_manifest_is_never_observed(tmp_path): ...
def test_exit_codes_distinguish_missing_stale_invalid_internal(): ...
```

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py -q
```

Expected: collection FAIL because the module does not exist.

- [ ] **Step 3: Implement schema and CLI**

Expose:

```python
SCHEMA = 1
MANIFEST = "artifact-manifest.json"
EXIT_VALID = 0
EXIT_MISSING = 20
EXIT_STALE = 21
EXIT_INVALID = 22
EXIT_INTERNAL = 23

def create_manifest(bundle, *, kind, input_digest, toolchain) -> dict: ...
def publish_manifest_atomic(bundle, manifest) -> None: ...
def verify_bundle(bundle, *, expected_kind, current_inputs, current_toolchain) -> Result: ...
```

CLI:

```bash
python3 tools/artifact_provenance.py verify \
  --kind qemu-test build_qemu_test
python3 tools/artifact_provenance.py publish \
  --kind qemu-test --input-digest ... --toolchain-json ... STAGING_DIR
```

Write JSON to a same-directory temporary file, `fsync`, then `os.replace`.
Verification requires the exact member set and hashes every member while the
caller holds the build/read lock.

- [ ] **Step 4: Run GREEN**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py -q
```

Expected: all tests PASS without Docker.

- [ ] **Step 5: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py
git commit -m "build(Esp32Tap): verify complete flash bundles"
```

---

### Task 4: Bind the toolchain image to its recipe

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image_recipe.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/README.md`

- [ ] **Step 1: Write failing recipe tests**

Test the pure shell/Python helper mode:

```python
def test_recipe_digest_changes_with_dockerfile(tmp_path): ...
def test_unlabeled_image_is_rejected(fake_docker): ...
def test_stale_recipe_label_is_rejected(fake_docker): ...
def test_matching_label_returns_immutable_image_id(fake_docker): ...
```

The fake `docker` executable records arguments and returns controlled inspect
JSON; no real image build occurs.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image_recipe.py -q
```

Expected: FAIL because `build_image.sh` is missing.

- [ ] **Step 3: Implement wrapper and checker modes**

The wrapper computes the Dockerfile/build-context digest and runs:

```bash
docker build \
  --label "org.treddy.esp32tap.recipe-sha256=$recipe_sha" \
  -t "${RUST_IMAGE:-esp32tap-rust:build}" \
  "$ESP32_RS"
```

`--check` resolves `.Id` plus the label with `docker image inspect` and fails
before Cargo if the label is absent or stale. Print machine-readable JSON for
the bundle manifest.

Replace README's raw `docker build` command with `tools/build_image.sh`.

- [ ] **Step 4: Run GREEN**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image_recipe.py -q
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image_recipe.py \
  hardware/Esp32Tap/firmware/esp32_rs/README.md
git commit -m "build(Esp32Tap): attest the Rust toolchain image"
```

---

### Task 5: Build, gate, and publish from one immutable snapshot

**Files:**

- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_snapshot.py`
- Modify if required by relocated root:
  - `hardware/Esp32Tap/firmware/esp32_rs/tools/check_case_parity.py`
  - `hardware/Esp32Tap/firmware/esp32_rs/tools/check_pins.py`
  - `hardware/Esp32Tap/firmware/esp32_rs/tools/check_unsafe_budget.py`
  - `hardware/Esp32Tap/firmware/esp32_rs/tools/check_wdt_chain.py`

- [ ] **Step 1: Write failing orchestration tests**

Use fake `docker`, fake gates, and a temporary repository:

```python
def test_snapshot_exists_before_first_host_gate(): ...
def test_all_four_gates_run_inside_snapshot(): ...
def test_container_mounts_snapshot_not_live_repo(): ...
def test_live_mutation_prevents_publication(): ...
def test_transient_live_edit_cannot_enter_snapshot(): ...
def test_only_qemu_publishes_one_qemu_manifest(): ...
def test_only_both_publishes_prod_and_qemu_manifests(): ...
def test_failed_gate_or_build_leaves_previous_bundle_intact(): ...
def test_toolchain_recipe_is_checked_before_first_gate(): ...
```

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_snapshot.py -q
```

Expected: FAIL because current `build.sh` gates/builds the live tree and
publishes without manifests.

- [ ] **Step 3: Refactor `build.sh` minimally**

Required order under exclusive lock:

1. `build_image.sh --check`;
2. create task-specific snapshot and record digest;
3. run all four gates from snapshot paths;
4. mount snapshot at `/project`;
5. mount persistent Cargo cache and persistent target cache separately;
6. build requested artifact(s) into snapshot-local staging;
7. copy all five expected members into real-repo temporary publication dirs;
8. compare live digest with snapshot digest;
9. publish manifests into the temporary dirs;
10. replace only requested bundle dirs while no readers can hold the lock;
11. remove snapshot/temp dirs in `trap`.

Never write a manifest into an old output directory. Preserve the existing
sdkconfig, flash-size, partition-fit, unsafe, WDT, and image gates.

- [ ] **Step 4: Run host GREEN**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_snapshot.py -q
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
```

Expected: PASS; no Docker required by these tests.

- [ ] **Step 5: Build the labeled image once if required**

Run:

```bash
hardware/Esp32Tap/firmware/esp32_rs/tools/build_image.sh
```

Expected: local image carries the current recipe label. Record wall time
separately; this is toolchain setup, not the inner-loop benchmark.

- [ ] **Step 6: Run one real QEMU-only build**

Run:

```bash
/usr/bin/time -v env ONLY=qemu \
  bash hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/artifact_provenance.py \
  verify --kind qemu-test \
  hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test
```

Expected: build and verification PASS; manifest names all five members.
Record wall time, load, image bytes, and SHA-256.

- [ ] **Step 7: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/build.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_snapshot.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/check_case_parity.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/check_pins.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/check_unsafe_budget.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/check_wdt_chain.py
git commit -m "build(Esp32Tap): publish gated snapshot bundles"
```

Stage only checker files actually changed.

---

### Task 6: Fail every direct QEMU entry point before Docker or ports

**Files:**

- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/conftest.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/conftest.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/qemu_session.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/verify_harness_copy.py`
- Delete symlink/Create file:
  `hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_smoke.sh`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/tools/run_harness.sh`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py`

- [ ] **Step 1: Write failing entry-point tests**

Use fake bundles, a fake `docker`, and a monkeypatched `_lease_port`:

```python
def test_harness_missing_bundle_fails_not_skips_before_docker(): ...
def test_scenarios_stale_bundle_fails_before_docker(): ...
def test_artifact_only_selection_preflights_both_bundles(): ...
def test_qemu_session_verifies_before_first_port_lease(): ...
def test_smoke_wrapper_refuses_stale_prod_before_delegate(): ...
def test_run_harness_refuses_stale_bundle_before_shared_harness(): ...
def test_shared_build_lock_is_held_from_verify_through_flash_assembly(): ...
```

- [ ] **Step 2: Demonstrate the known stale artifact RED**

Before rebuilding/removing the local stale shape, run the fixture test against
a copied 1,299,008-byte bundle without a manifest.

Expected: current code SKIPS or reaches a later action; the new test FAILS.

- [ ] **Step 3: Implement mandatory preflight**

In both conftests, replace existence-only `pytest.skip` with a session fixture
that calls the checker and `pytest.fail`s on any non-valid result. The harness
fixture checks production and test bundles so the artifact-only S6 test is
covered.

In `QemuSession._start`, acquire the shared build lock first, verify the QEMU
bundle, then acquire ports and assemble flash while retaining the lock.
Construction failure must close the lock.

Replace the smoke symlink with:

```bash
#!/usr/bin/env bash
set -euo pipefail
python3 "$(dirname "$0")/artifact_provenance.py" verify \
  --kind production "$(dirname "$0")/../build"
exec bash "$(dirname "$0")/../../esp32/tools/qemu_smoke.sh" "$@"
```

`run_harness.sh` verifies both bundles before delegating.

Update `ALLOWED_STRENGTHENING` SHA pins and reasons for `conftest.py` and
`qemu_session.py`. Keep the C++ live harness and smoke script unchanged.

- [ ] **Step 4: Run focused GREEN without QEMU**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py -q
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/verify_harness_copy.py
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_smoke.sh
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/run_harness.sh
```

Expected: PASS; approved diffs are printed by the harness-copy gate.

- [ ] **Step 5: Prove direct stale failure, then fresh focused QEMU**

Run:

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/test_program.py \
  -k pause_of_stopped -q
```

First, deliberately alter a copied bundle member or source input and verify
failure occurs before Docker. Restore/rebuild, run the same command again, and
expect one PASS.

- [ ] **Step 6: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/conftest.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_scenarios/conftest.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_harness/qemu_session.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/verify_harness_copy.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/qemu_smoke.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/run_harness.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py
git commit -m "test(Esp32Tap): reject stale bundles before QEMU"
```

---

### Task 7: Implement conservative change-to-gate selection

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/fast_select.py`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py`

- [ ] **Step 1: Write failing selector tests**

Cover exact behaviors:

```python
def test_dirty_staged_and_untracked_are_unioned(): ...
def test_revision_range_uses_nul_name_status(): ...
def test_rename_classifies_old_and_new_paths(): ...
def test_deleted_path_is_classified_without_existing(): ...
def test_explicit_paths_only_augment_authoritative_set(): ...
def test_clean_tree_requires_base_or_range(): ...
def test_unknown_shared_and_selector_paths_choose_broad_sweep(): ...
def test_multiple_domains_union_without_duplicate_gates(): ...
def test_docs_only_skips_artifacts(): ...
def test_no_path_can_select_an_empty_success(): ...
```

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py -q
```

Expected: collection FAIL because selector is absent.

- [ ] **Step 3: Implement a data-driven selector**

Expose:

```python
@dataclass(frozen=True)
class Selection:
    paths: tuple[str, ...]
    policies: tuple[str, ...]
    host_commands: tuple[tuple[str, ...], ...]
    qemu_commands: tuple[tuple[str, ...], ...]
    needs_prod: bool
    needs_qemu: bool
    broad_reason: str | None

def changed_paths(repo, *, base=None, revision_range=None, extra=()) -> tuple[str, ...]: ...
def select(paths: Iterable[str]) -> Selection: ...
```

Initial policies must cover program/control, request/API, safety/difftest, BLE,
coach, records/storage, docs-only, and broad cross-cutting. Route
registration, build config, sdkconfig, harness, provenance, selector, and
unknown paths choose `bash tools/sweep.sh` verbatim.

CLI emits JSON; human text goes to stderr so the runner can parse stdout.

- [ ] **Step 4: Run GREEN**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py -q
python3 hardware/Esp32Tap/firmware/esp32_rs/tools/fast_select.py --help
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/fast_select.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py
git commit -m "test(Esp32Tap): select conservative inner-loop gates"
```

---

### Task 8: Orchestrate one-rebuild focused verification

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py`

- [ ] **Step 1: Write failing runner tests with fake commands**

```python
def test_host_gates_run_before_artifact_work(): ...
def test_docs_only_never_checks_or_builds_firmware(): ...
def test_valid_bundle_is_reused_without_build(): ...
def test_missing_or_stale_bundle_builds_once_then_rechecks(): ...
def test_invalid_manifest_or_internal_error_never_auto_rebuilds(): ...
def test_build_failure_does_not_run_qemu(): ...
def test_broad_selection_executes_sweep_verbatim(): ...
def test_each_gate_has_timing_and_retained_log(): ...
```

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py -q
```

Expected: FAIL because `fast.sh` is missing.

- [ ] **Step 3: Implement thin runner**

The runner:

- calls `fast_select.py` with working tree or authoritative revision args;
- prints selected paths/policies;
- runs selected host commands;
- skips provenance for docs-only;
- checks only required artifact kinds;
- rebuilds once only for exit 20/21;
- rechecks after build;
- never rebuilds for exit 22/23;
- runs selected QEMU commands with the selector's proven worker count;
- writes per-gate logs under a task-specific `mktemp -d`;
- prints wall time, load, artifact bytes/SHA, and broad fallback reason.

Do not duplicate `sweep.sh`; broad mode invokes it.

- [ ] **Step 4: Run GREEN**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py -q
bash -n hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh
```

Expected: PASS without Docker.

- [ ] **Step 5: Run representative real inner loops**

Run one host-only explicit-path case and one firmware case:

```bash
bash hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh \
  --path hardware/Esp32Tap/firmware/esp32_rs/program_core/src/state.rs

bash hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh \
  --path hardware/Esp32Tap/firmware/esp32_rs/esp32tap/src/net/program.rs
```

Because explicit paths may only augment authoritative changes, run these in a
small temporary test repository or supply an authoritative `--base` that
contains the named change. Expected: host-only case avoids QEMU where policy
permits; firmware case reuses a current bundle and runs focused QEMU.

- [ ] **Step 6: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/fast.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py
git commit -m "test(Esp32Tap): add provenance-safe fast lane"
```

---

### Task 9: Benchmark, document, and preserve release gates

**Files:**

- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/benchmark_fast.sh`
- Create: `hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_contract.py`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/README.md`
- Modify: `hardware/Esp32Tap/firmware/esp32_rs/2026-07-30-independent-firmware-audit.md`

- [ ] **Step 1: Write failing contract tests**

Assert:

```python
def test_normal_and_deep_sweep_commands_match_pre_feature_baseline(): ...
def test_qemu_tests_still_create_one_session_per_factory_call(): ...
def test_worker_counts_and_test_timeouts_are_unchanged(): ...
def test_readme_names_supported_build_and_fast_commands(): ...
def test_benchmark_requires_load_matched_samples(): ...
```

Store the pre-feature sweep command fingerprint as a reviewed fixture, not as
a prose claim.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_contract.py -q
```

Expected: FAIL because benchmark/docs/fingerprint are absent.

- [ ] **Step 3: Implement benchmark protocol**

`benchmark_fast.sh` must:

- capture ten alternating warm baseline/candidate samples;
- capture three cold-build samples;
- record one- and five-minute load before each;
- reject pairs whose starting one-minute loads differ by more than 20%;
- compute median and p95;
- report pass only when provenance p95 <1 s, host-only p95 <5 s, representative
  firmware p95 <30 s, candidate median is at least 50% below the broad gate,
  and all ten candidate samples pass without retries.

- [ ] **Step 4: Update documentation**

Document:

- `tools/build_image.sh`;
- direct `ONLY=qemu tools/build.sh`;
- stale direct pytest failure/remediation;
- dirty-tree and `--base`/`--range` fast-lane examples;
- normal/deep sweep unchanged;
- measured results and host-load band;
- generated output no longer belongs in Git.

Update the audit's build/test evidence without claiming QEMU timing as hardware
cycle-time evidence.

- [ ] **Step 5: Run all host fast-loop tests**

```bash
python3 -m pytest \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_layout.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_inputs.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_artifact_provenance.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_image_recipe.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_build_snapshot.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_provenance_entrypoints.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_select.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_runner.py \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_contract.py -q
```

Expected: all PASS.

- [ ] **Step 6: Run benchmark and acceptance**

Run:

```bash
bash hardware/Esp32Tap/firmware/esp32_rs/tools/benchmark_fast.sh
```

Expected: every acceptance threshold passes. If host load cannot satisfy the
20% pairing band, file a bead and rerun later; do not weaken thresholds.

- [ ] **Step 7: Run release verification once**

Run:

```bash
bash hardware/Esp32Tap/firmware/esp32_rs/tools/sweep.sh
```

Run `DEEP=1` only if the final diff changes memory/request-path behavior beyond
the build/test tools. Expected: normal sweep ALL GREEN. This is the one broad
post-integration run; do not run it after every task.

- [ ] **Step 8: Commit**

```bash
git add hardware/Esp32Tap/firmware/esp32_rs/tools/benchmark_fast.sh \
  hardware/Esp32Tap/firmware/esp32_rs/tools/test_fast_contract.py \
  hardware/Esp32Tap/firmware/esp32_rs/README.md \
  hardware/Esp32Tap/firmware/esp32_rs/2026-07-30-independent-firmware-audit.md
git commit -m "docs(Esp32Tap): verify the fast inner loop"
```

---

### Task 10: Final independent review and branch handoff

**Files:**

- Review all changes from the commit before Task 1 through Task 9 HEAD.
- Do not add production behavior in this task.

- [ ] **Step 1: Run clean status and generated-artifact audit**

```bash
git status --short
git ls-files hardware/Esp32Tap/firmware/esp32_rs/build \
  hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test
git check-ignore hardware/Esp32Tap/firmware/esp32_rs/build/esp32tap.bin \
  hardware/Esp32Tap/firmware/esp32_rs/build_qemu_test/esp32tap.bin
```

Expected: no generated bundle is tracked; output paths are ignored.

- [ ] **Step 2: Request final spec and quality reviews**

Give reviewers:

- approved design path;
- this plan path;
- base/head SHAs;
- all RED/GREEN evidence;
- benchmark table;
- exact normal-sweep result;
- image identities;
- any skipped hardware-only limitations.

No Critical/Important/spec issues may remain.

- [ ] **Step 3: Fix findings through the same implementer**

Use TDD for each behavior change, focused tests first, then only the affected
integration gate. Re-review until approved.

- [ ] **Step 4: Close implementation bead and push**

```bash
bd close <implementation-bead> --reason="Fast inner loop implemented, benchmarked, release sweep green, and independently approved."
git pull --rebase
git push
bd dolt push
git status
```

Expected: Git branch is up to date with origin. If `bd dolt push` reports no
remote configured, record that exact result in the handoff.

