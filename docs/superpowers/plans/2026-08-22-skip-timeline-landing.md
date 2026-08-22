# Skip Timeline Landing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the preserved skip-timeline, persistence, and deploy-state protections on current `main` as one verified logical commit.

**Architecture:** Keep `ProgramState.total_elapsed` on real elapsed time by truncating a skipped interval instead of jumping the clock. Persist the reshaped plan atomically with its resume position, and protect device-owned state before the deployer's destructive rsync. Port tests before implementation so current `main` demonstrates each regression.

**Tech Stack:** Python 3.12, asyncio, pytest, SQLite, Bash, rsync, Git worktrees

---

## Source identity and file map

Source worktree: `/home/ssilver/development/precor-9.3x/.claude/worktrees/skip-truncates-timeline`

- Branch: `worktree-skip-truncates-timeline`
- Base commit: `932256165f36063ffb83273402ae86162aba0ba4`
- Tracked binary-diff SHA-256: `873345daba126847b05f0440e38f8049be51d4868b413f8b3631b5d6a3305e97`
- Untracked source: `deploy/tests/test_device_state.sh`

Landing worktree: `/home/ssilver/development/precor-9.3x/.worktrees/land-ridgeline-timeline`

Files and responsibilities:

- `python/program_engine.py`: truncate skipped intervals and suppress replayed milestones.
- `python/db.py`: persist a reshaped `program_json` and its recalculated duration with resume state.
- `python/server.py`: validate positive durations and pass the live plan into history updates.
- `python/tests/test_program_engine.py`: deterministic skip/rebase unit coverage.
- `python/tests/test_live_program.py`: wall-clock execution regression coverage.
- `python/tests/test_server_integration.py`: validation and reshaped-history integration coverage.
- `deploy/deploy.sh`: exclude device-owned files and snapshot the live SQLite DB before rsync.
- `deploy/tests/test_device_state.sh`: executable, dependency-light destructive-rsync regression harness.
- `deploy/tests/test_all_suites.sh`: include the new deploy-state harness.
- `CLAUDE.md`: document the clock and deploy-state contracts.

### Task 1: Reconfirm the isolated baseline and source snapshot

- [ ] **Step 1: Verify the landing worktree is clean**

Run:

```bash
git status --short --branch
```

Expected: branch `feat/land-ridgeline-timeline`; only committed plan documents may differ from the original baseline.

- [ ] **Step 2: Verify the source patch has not changed**

Run:

```bash
git -C /home/ssilver/development/precor-9.3x/.claude/worktrees/skip-truncates-timeline diff --binary | sha256sum
git -C /home/ssilver/development/precor-9.3x/.claude/worktrees/skip-truncates-timeline status --short
```

Expected: checksum `873345daba126847b05f0440e38f8049be51d4868b413f8b3631b5d6a3305e97` and the file set listed above. Stop and inspect if either differs.

- [ ] **Step 3: Re-run the focused clean baseline**

Run:

```bash
pytest -q python/tests/test_program_engine.py python/tests/test_live_program.py python/tests/test_server_integration.py
```

Expected: `202 passed`.

### Task 2: Prove the timeline and persistence regressions

- [ ] **Step 1: Apply only the Python tests from the source worktree**

Run from the landing worktree root:

```bash
git -C /home/ssilver/development/precor-9.3x/.claude/worktrees/skip-truncates-timeline diff --binary -- \
  python/tests/test_program_engine.py \
  python/tests/test_live_program.py \
  python/tests/test_server_integration.py | git apply --3way
```

Expected: the three test files are modified; production files remain unchanged.

- [ ] **Step 2: Run the new tests and verify red**

Run:

```bash
pytest -q python/tests/test_program_engine.py python/tests/test_live_program.py python/tests/test_server_integration.py
```

Expected: failures show that skip still jumps `total_elapsed`, the reshaped plan is not persisted, and non-positive durations remain accepted. If the new tests all pass, stop and determine whether current `main` already contains the behavior.

- [ ] **Step 3: Port the minimal runtime implementation**

Apply the source hunks for `python/program_engine.py`, `python/db.py`, and `python/server.py` with `git apply --3way`. Resolve against current `main` by preserving its newer standalone-firmware behavior and these invariants:

```python
# ProgramState.skip(): shorten, never lengthen, and retain a one-second stub.
cur["duration"] = max(1, min(cur["duration"], max(1, int(self.interval_elapsed))))

# After the plan shrinks, prevent old 25/50/75% messages from replaying.
self._mark_passed_milestones()
```

`TreadmillDB.update_history_entry(...)` must update `program_json`, `total_duration`, `last_interval`, and `last_elapsed` in the same SQL update when a program is provided. Server stop/completion updates must pass `sess.prog.program`/`prog`, and `_validate_program()` must reject `duration <= 0`.

- [ ] **Step 4: Run the focused tests and verify green**

Run:

```bash
pytest -q python/tests/test_program_engine.py python/tests/test_live_program.py python/tests/test_server_integration.py
```

Expected: all tests pass, including the newly added skip/rebase and history tests.

### Task 3: Prove and implement deploy-state preservation

- [ ] **Step 1: Copy the new regression harness and register it**

Run:

```bash
cp -f /home/ssilver/development/precor-9.3x/.claude/worktrees/skip-truncates-timeline/deploy/tests/test_device_state.sh deploy/tests/test_device_state.sh
chmod +x deploy/tests/test_device_state.sh
git -C /home/ssilver/development/precor-9.3x/.claude/worktrees/skip-truncates-timeline diff --binary -- deploy/tests/test_all_suites.sh | git apply --3way
```

Expected: one new executable test and one modified meta-suite.

- [ ] **Step 2: Run the deploy-state test and verify red**

Run:

```bash
bash deploy/tests/test_device_state.sh
```

Expected: failure because `DEVICE_STATE_EXCLUDES` and `backup_device_state` do not yet exist on the landing branch.

- [ ] **Step 3: Port the deploy implementation**

Apply the `deploy/deploy.sh` source hunk. Preserve these boundaries:

- `DEVICE_STATE_EXCLUDES` protects the DB, WAL/SHM sidecars, migration JSON, HRM config, background advice, keys, and certificates.
- `backup_device_state` uses Python's SQLite backup API, writes outside the deploy directory, runs before rsync, and fails closed unless `SKIP_BACKUP=1`.
- The ordinary rsync still uses `--delete` for stale shipped files.
- The `backup` subcommand creates an on-demand snapshot without deploying.

- [ ] **Step 4: Run the focused and aggregate deploy gates**

Run:

```bash
bash deploy/tests/test_device_state.sh
bash deploy/tests/test_all_suites.sh
```

Expected: both print `ALL TESTS PASSED`.

### Task 4: Document, verify, and commit the timeline landing

- [ ] **Step 1: Port the source documentation**

Apply only the relevant `CLAUDE.md` hunks describing real elapsed program time, skip truncation, device-owned state, and the backup command. Do not overwrite unrelated documentation added since base commit `9322561`.

- [ ] **Step 2: Run all timeline/deploy gates**

Run:

```bash
pytest -q python/tests/test_program_engine.py python/tests/test_live_program.py python/tests/test_server_integration.py
bash deploy/tests/test_all_suites.sh
git diff --check
```

Expected: Python suite passes, deploy suite prints `ALL TESTS PASSED`, and `git diff --check` is silent.

- [ ] **Step 3: Run the broader non-hardware Python gate**

Run:

```bash
pytest -q python/tests -m "not hardware"
```

Expected: all selected tests pass. Diagnose any failure before committing.

- [ ] **Step 4: Commit one logical landing**

Run:

```bash
git add CLAUDE.md deploy/deploy.sh deploy/tests/test_all_suites.sh \
  deploy/tests/test_device_state.sh python/db.py python/program_engine.py python/server.py \
  python/tests/test_live_program.py python/tests/test_program_engine.py \
  python/tests/test_server_integration.py
git commit -m "fix: keep skipped workouts on real elapsed time"
```

Expected: one commit containing the tested timeline, persistence, deploy safety, and documentation changes.

- [ ] **Step 5: Push the checkpoint before beginning Ridgeline work**

Run:

```bash
git push -u origin feat/land-ridgeline-timeline
git status --short --branch
```

Expected: the integration branch tracks its remote and has no uncommitted source changes.

