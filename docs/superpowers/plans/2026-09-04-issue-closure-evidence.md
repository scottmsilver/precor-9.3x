# Issue Closure Evidence and Tablet Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy authoritative merged `main` to the gym tablet, validate issues 59/60/61/62/64 safely, attach evidence directly to their GitHub issues, and establish an evidence-first closure policy in `CLAUDE.md`.

**Architecture:** Documentation changes live on `codex/closure-evidence` and land before the final APK is pinned. Device validation uses a serial-qualified ADB workflow and a `TREADMILL_MOCK=1` server backed by temporary data; screenshots and logs live only in an OS temporary directory and are uploaded with a checksum-verified GitHub CLI 2.100.0 binary. Broken-state regressions run in disposable detached worktrees with test-only patches, while final validation runs against the authoritative merged-main SHA.

**Tech Stack:** Git worktrees, beads (`bd`), Gradle/Kotlin/JUnit, pytest/FastAPI, Android Debug Bridge/UIAutomator, GitHub CLI 2.100.0 attachments, shell tooling.

---

### Task 1: Add the evidence-first closure policy

**Files:**
- Modify: `CLAUDE.md`
- Reference: `docs/superpowers/specs/2026-09-04-issue-closure-evidence-design.md`

- [ ] **Step 1: Add an `Issue Closure Evidence` section to `CLAUDE.md`**

Require a problem/root-cause explanation; a focused RED run against the broken state for bugs; the corresponding GREEN run and broader gates; before/after screenshots for visual bugs when safely reproducible or a post-fix screenshot for visual features; native GitHub attachments by default; commit/PR/device context; honest limitations; and evidence before closure.

- [ ] **Step 2: Verify the policy text is complete and screenshots are excluded**

Run from the closure-evidence worktree:

```bash
rg -n "Issue Closure Evidence|root cause|RED|GREEN|screenshot|GitHub attachment|close" CLAUDE.md
git diff --check
git diff --name-only --cached
```

Expected: all policy concepts appear; no image file is staged.

- [ ] **Step 3: Commit the policy**

```bash
git add CLAUDE.md
git commit -m "docs: require evidence before closing issues"
```

### Task 2: Land documentation before pinning the deploy commit

**Files:**
- Modify through GitHub: documentation PR only

- [ ] **Step 1: Rebase without rewriting user state and verify the branch**

```bash
git pull --rebase origin main
git diff --check origin/main...HEAD
git diff --name-only origin/main...HEAD
```

Expected: only `CLAUDE.md` and the approved spec/plan documents differ.

- [ ] **Step 2: Push, open, and merge the documentation PR**

```bash
git push -u origin codex/closure-evidence
gh pr create --base main --head codex/closure-evidence --title "Document evidence-first issue closure" --body-file <temporary-body-file>
gh pr merge <pr-number> --squash
git fetch origin
```

- [ ] **Step 3: Pin authoritative final main**

Create a clean detached worktree at `origin/main`, save its full SHA, and verify a second fetch does not move it before build. Do not modify or stash the dirty primary worktree.

### Task 3: Build and prove APK provenance

**Files:**
- Build output: `kotlin/app/build/outputs/apk/debug/app-debug.apk` in the pinned worktree
- Temporary backup/evidence directory: created with `mktemp -d` outside the repository

- [ ] **Step 1: Run final-main automated gates**

From the pinned worktree root, run in parallel:

```bash
(cd kotlin && ./gradlew testDebugUnitTest assembleDebug)
pytest -q python/tests/test_server_integration.py
```

Expected: Android build succeeds; 137 server integration tests pass.

- [ ] **Step 2: Record artifact provenance**

Record pinned Git SHA, APK path, size, and `sha256sum` into an OS-temporary evidence log.

- [ ] **Step 3: Perform read-only device and treadmill preflight**

Using only the exact tablet serial, record current package metadata, microphone permission flags, resumed activity, and DataStore file. Query the configured real server status read-only and require belt speed zero plus no active session/program. Abort without sending commands if idle state cannot be established.

- [ ] **Step 4: Preserve rollback state**

Pull the currently installed base APK and export the complete app DataStore/preferences with `run-as` into the OS-temporary directory. Never uninstall or clear app data.

- [ ] **Step 5: Install and verify the exact artifact**

Run serial-qualified `adb install -r`, launch the package, confirm a resumed activity, obtain `pm path`, pull the installed base APK, and require its SHA-256 to equal the locally built APK checksum. Restore the previous APK only if installation/launch validation fails.

### Task 4: Produce retrospective RED/GREEN bug evidence

**Files:**
- Temporary detached worktrees only
- Temporary evidence logs only

- [ ] **Step 1: Reproduce #60 RED**

Create a detached worktree at broken SHA `e186c68`. Apply only `WakeWordActivationPolicyTest.kt` from fix commit `872281b`. From that worktree's `kotlin/`, run:

```bash
./gradlew testDebugUnitTest --tests com.precor.treadmill.WakeWordActivationPolicyTest
```

Expected: nonzero compile failure because the debounce/rearm policy does not exist. Save unedited relevant output and exit status; label it as a compile-time RED.

- [ ] **Step 2: Run #60 GREEN on pinned main**

From pinned `kotlin/`, run the identical focused test command. Expected: success.

- [ ] **Step 3: Reproduce #62 RED with the identical regression source**

Create a detached worktree at `e186c68`. Extract the final `TestHistoryResume` class from fix commit `f7cef1e` into the broken tree's `python/tests/test_server_integration.py` as a disclosed test-only patch. Run from its root:

```bash
pytest -q python/tests/test_server_integration.py::TestHistoryResume
```

Expected: nonzero assertion failures for stale interval/terminal resume behavior. Save unedited relevant output and exit status.

- [ ] **Step 4: Run #62 GREEN on pinned main**

Run the identical class command on pinned main. Expected: all nine `TestHistoryResume` tests pass.

- [ ] **Step 5: Remove disposable RED worktrees**

Use `git worktree remove` on the exact temporary paths, then `git worktree prune`. Retain only evidence logs in the OS temporary directory.

### Task 5: Establish a hard-interlocked mock backend

**Files:**
- Temporary: worktree-specific `worktree.env`, mock SQLite DB, server log

- [ ] **Step 1: Allocate a unique port and seed mock history**

Run `scripts/setup-worktree.sh` in the pinned worktree. With a temporary `TREADMILL_DB`, use a short Python setup invocation to import the server in `TREADMILL_MOCK=1`, create a two-interval resumable program with stale stored interval but authoritative elapsed position, and create a terminal program entry at its final boundary. Record the generated history IDs.

- [ ] **Step 2: Start the isolated server**

Launch `python/server.py` with `TREADMILL_MOCK=1`, the temporary DB, and the allocated port. Require its log to contain `Mock mode — no Pi connection`; verify `/api/status`, `/api/program`, and `/api/programs/history` respond on the host LAN address.

- [ ] **Step 3: Retarget tablet without retaining altered preferences**

Force-stop the app, back up its DataStore again, temporarily remove only `server_prefs.preferences_pb` using `run-as`, launch Setup, enter the mock LAN URL using UIAutomator/text input, and verify the app connects to the mock. Confirm the mock process environment still contains `TREADMILL_MOCK=1` before invoking any program API.

### Task 6: Capture final-main tablet evidence

**Files:**
- Temporary PNG screenshots outside repository/worktrees

- [ ] **Step 1: Validate and capture #62 history behavior**

On the lobby/history UI, use UIAutomator to confirm the resumable entry says `Resume` with the correct remaining duration and the terminal entry has no Resume action. Capture a screenshot if both are meaningfully visible.

- [ ] **Step 2: Validate and capture #59 and #64 running behavior**

Call the mock resume endpoint for the resumable history ID. Confirm via mock API and UIAutomator that the running screen is active, the default timer is countdown, and the next-change label contains both relative and absolute workout-clock values. Capture a screenshot. Tap the timer only if needed to verify count-up toggling; do not invoke any real-device control.

- [ ] **Step 3: Validate and capture #61 settings behavior**

Record the switch's original value. Open Settings by accessibility selector, switch Voice Input to the opposite value, force-stop/relaunch, verify persistence, restore the original value, and verify microphone permission remains at its original state. Capture the meaningful Settings state.

- [ ] **Step 4: Restore exact tablet state and stop mock services**

Force-stop the app; restore the original DataStore/preferences and permission flags; relaunch the prior visible activity where practical; prove the app no longer connects to the mock endpoint. Stop the mock server and confirm its port is closed. Leave the new pinned-main APK installed.

### Task 7: Upload native GitHub attachments and annotate issues

**Files:**
- Temporary comment bodies and images outside repository/worktrees

- [ ] **Step 1: Prepare and verify GitHub CLI 2.100.0**

Download `gh_2.100.0_linux_amd64.tar.gz` and its checksum file from `cli/cli` release `v2.100.0` into the OS temporary directory. Verify the checksum and confirm `issue comment --help` contains `--attach`.

- [ ] **Step 2: Compose five evidence comments**

Each body must identify problem/request, root cause or gap, exact fix/PR/final-main SHA, focused RED for #60/#62, focused GREEN and broad gate, device/mock context, belt-stationary statement, screenshot relevance, and any limitation. Do not claim a screenshot for #60.

- [ ] **Step 3: Post comments and attach relevant screenshots**

Use the verified temporary binary:

```bash
gh issue comment <number> --body-file <body> --attach '<png>#<descriptive alt text>'
```

Post #60 without `--attach`. Verify each returned comment belongs to the intended issue. For attached comments, require a `github.com/user-attachments/` URL and fetch the rendered attachment successfully before deleting the local PNG.

- [ ] **Step 4: Verify all retrospective annotations**

Read back issue comments 59/60/61/62/64 through the API and check every required evidence heading and attachment URL.

### Task 8: Final repository and tracker verification

**Files:**
- Beads issue: `precor-9_3x-ah8`

- [ ] **Step 1: Verify authoritative main has not advanced**

Fetch `origin/main`; compare it to the pinned deployed SHA. If advanced, rebuild, reinstall, and repeat artifact checksum verification before continuing.

- [ ] **Step 2: Close and sync the tracking bead**

Close `precor-9_3x-ah8` with links/context and run `bd dolt push`. If no Dolt remote is configured, report that accurately.

- [ ] **Step 3: Complete git session protocol**

From a clean tracking worktree, run `git pull --rebase`, `git push`, and `git status`. Fast-forward the primary `main` only if its existing `.beads`/`static/` changes are untouched; never stash/reset them.

- [ ] **Step 4: Remove temporary worktrees and artifacts**

After verified issue attachments and restoration, remove the pinned/dead RED worktrees and OS-temporary APK backups/screenshots/logs using exact validated paths. Preserve the closure-evidence worktree if its PR workflow requires it.

