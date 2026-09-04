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

Require a problem/root-cause explanation; a focused RED run against the broken state for bugs that fails for the intended reason; the corresponding GREEN run and broader gates; before/after screenshots for visual bugs when safely reproducible or a post-fix screenshot for visual features; native GitHub attachments rather than repository files unless the user explicitly requests otherwise; commit/PR/device context; honest limitations; and both an evidence comment and landed/delivered implementation before closure.

- [ ] **Step 2: Verify the policy text is complete and screenshots are excluded**

Stage the policy first, then run from the closure-evidence worktree:

```bash
git add CLAUDE.md
for phrase in "Issue Closure Evidence" "root cause" "intended reason" "RED" "GREEN" "GitHub attachment" "landed"; do rg -F "$phrase" CLAUDE.md; done
git diff --check
test "$(git diff --cached --name-only)" = "CLAUDE.md"
```

Expected: all policy concepts appear; no image file is staged.

- [ ] **Step 3: Commit the policy**

```bash
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
PR_BODY=$(mktemp /tmp/precor-doc-pr.XXXXXX.md)
printf '%s\n' 'Documents the required problem/root-cause, RED/GREEN, and screenshot evidence before closing issues. No evidence images are stored in the repository.' > "$PR_BODY"
git push -u origin codex/closure-evidence
PR_URL=$(gh pr create --base main --head codex/closure-evidence --title "Document evidence-first issue closure" --body-file "$PR_BODY")
PR_NUMBER=${PR_URL##*/}
gh pr merge "$PR_NUMBER" --squash
git fetch origin
```

- [ ] **Step 3: Pin authoritative final main**

Create an OS evidence directory and a clean detached worktree at `origin/main`, save its full SHA, and verify a second fetch does not move it before build. Do not modify or stash the dirty primary worktree.

```bash
EVIDENCE_ROOT=$(mktemp -d /tmp/precor-closure.XXXXXX)
FINAL_PARENT=$(mktemp -d .worktrees/final-main.XXXXXX)
FINAL_TREE="$FINAL_PARENT/worktree"
git worktree add --detach "$FINAL_TREE" origin/main
FINAL_SHA=$(git -C "$FINAL_TREE" rev-parse HEAD)
printf '%s\n' "$FINAL_SHA" > "$EVIDENCE_ROOT/final-main.sha"
git fetch origin
test "$FINAL_SHA" = "$(git rev-parse origin/main)"
```

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

```bash
APK="$FINAL_TREE/kotlin/app/build/outputs/apk/debug/app-debug.apk"
stat -c 'path=%n size=%s' "$APK" | tee "$EVIDENCE_ROOT/apk-provenance.log"
sha256sum "$APK" | tee -a "$EVIDENCE_ROOT/apk-provenance.log"
```

- [ ] **Step 3: Run focused GREEN evidence on clean final main**

From `"$FINAL_TREE/kotlin"`, run each command with `set -o pipefail`, preserve unedited output and the real exit status:

```bash
set -o pipefail
./gradlew testDebugUnitTest --tests com.precor.treadmill.ui.screens.running.RunningTimerTest 2>&1 | tee "$EVIDENCE_ROOT/issue-59-green.log"; test "${PIPESTATUS[0]}" -eq 0
./gradlew testDebugUnitTest --tests com.precor.treadmill.VoiceInputSettingsGuardTest --tests com.precor.treadmill.ui.viewmodel.VoiceInputPolicyTest --tests com.precor.treadmill.WakeWordActivationPolicyTest 2>&1 | tee "$EVIDENCE_ROOT/issue-61-green.log"; test "${PIPESTATUS[0]}" -eq 0
./gradlew testDebugUnitTest --tests com.precor.treadmill.ui.screens.running.NextChangeDisplayTest --tests com.precor.treadmill.ui.screens.running.RidgelineNextChangeClockSourceTest 2>&1 | tee "$EVIDENCE_ROOT/issue-64-green.log"; test "${PIPESTATUS[0]}" -eq 0
```

- [ ] **Step 4: Perform read-only device and treadmill preflight and capture immutable original state**

Use `DEVICE_SERIAL='adb-R9ZY90P5LZP-WMXOYu._adb-tls-connect._tcp'` and `PACKAGE='com.precor.treadmill'`. Before any mutation, save distinct `original-*` files: `dumpsys package`, resumed activity, full raw DataStore, its SHA-256 and `protoc --decode_raw` output, microphone grant/flags, and current connectivity. Extract the original server URL from the decoded DataStore. Query `api/status`, `api/session`, and `api/program` read-only and require `.motor.belt == "0"`, `.motor.mph == "0"`, `.active == false`, and `.running == false`. Repeat this same preflight immediately before the first force-stop/retarget. Abort without commands to the equipment if any assertion is unavailable or ambiguous.

```bash
adb -s "$DEVICE_SERIAL" exec-out run-as "$PACKAGE" cat files/datastore/server_prefs.preferences_pb > "$EVIDENCE_ROOT/original-server-prefs.pb"
sha256sum "$EVIDENCE_ROOT/original-server-prefs.pb" > "$EVIDENCE_ROOT/original-server-prefs.sha256"
protoc --decode_raw < "$EVIDENCE_ROOT/original-server-prefs.pb" > "$EVIDENCE_ROOT/original-server-prefs.txt"
adb -s "$DEVICE_SERIAL" shell dumpsys package "$PACKAGE" > "$EVIDENCE_ROOT/original-package.txt"
adb -s "$DEVICE_SERIAL" shell dumpsys activity activities > "$EVIDENCE_ROOT/original-activity.txt"
ORIGINAL_SERVER=$(sed -n 's/.*"\(https\{0,1\}:\/\/[^\"]*\)".*/\1/p' "$EVIDENCE_ROOT/original-server-prefs.txt" | head -1)
curl -ksS "$ORIGINAL_SERVER/api/status" | tee "$EVIDENCE_ROOT/original-status.json" | jq -e '.motor.belt == "0" and .motor.mph == "0"'
curl -ksS "$ORIGINAL_SERVER/api/session" | tee "$EVIDENCE_ROOT/original-session.json" | jq -e '.active == false'
curl -ksS "$ORIGINAL_SERVER/api/program" | tee "$EVIDENCE_ROOT/original-program.json" | jq -e '.running == false'
```

- [ ] **Step 5: Preserve rollback APK**

Pull the currently installed base APK to `"$EVIDENCE_ROOT/original-base.apk"`; the immutable DataStore source is already `original-server-prefs.pb`. Never uninstall or clear app data.

```bash
ORIGINAL_APK_PATH=$(adb -s "$DEVICE_SERIAL" shell pm path "$PACKAGE" | tr -d '\r' | sed 's/^package://')
adb -s "$DEVICE_SERIAL" pull "$ORIGINAL_APK_PATH" "$EVIDENCE_ROOT/original-base.apk"
sha256sum "$EVIDENCE_ROOT/original-base.apk" > "$EVIDENCE_ROOT/original-base.sha256"
```

- [ ] **Step 6: Install and verify the exact artifact with rollback on every mismatch**

Run serial-qualified `adb install -r`, launch the package, and verify application ID, version code/name, installed path, resumed activity, and byte-identical installed APK. On any install/package/path/version/activity/checksum failure, immediately reinstall `original-base.apk`, verify it launches, restore the original DataStore hash and microphone flags, and stop.

```bash
adb -s "$DEVICE_SERIAL" install -r "$APK"
adb -s "$DEVICE_SERIAL" shell am force-stop "$PACKAGE"
adb -s "$DEVICE_SERIAL" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1
adb -s "$DEVICE_SERIAL" shell dumpsys package "$PACKAGE" | tee "$EVIDENCE_ROOT/installed-package.txt"
INSTALLED_APK_PATH=$(adb -s "$DEVICE_SERIAL" shell pm path "$PACKAGE" | tr -d '\r' | sed 's/^package://')
adb -s "$DEVICE_SERIAL" pull "$INSTALLED_APK_PATH" "$EVIDENCE_ROOT/installed-base.apk"
test "$(sha256sum "$APK" | cut -d' ' -f1)" = "$(sha256sum "$EVIDENCE_ROOT/installed-base.apk" | cut -d' ' -f1)"
rg -q 'versionCode=1' "$EVIDENCE_ROOT/installed-package.txt"
rg -q 'versionName=1.0' "$EVIDENCE_ROOT/installed-package.txt"
adb -s "$DEVICE_SERIAL" shell dumpsys activity activities | rg "mResumedActivity.*$PACKAGE"
```

### Task 4: Produce retrospective RED/GREEN bug evidence

**Files:**
- Temporary detached worktrees only
- Temporary evidence logs only

- [ ] **Step 1: Reproduce #60 RED**

Create detached RED and GREEN worktrees at broken SHA `e186c68` and `"$FINAL_SHA"`. Materialize the identical test file from fix commit `872281b` in both and prove their hashes match:

```bash
RED60="$EVIDENCE_ROOT/red-60"; GREEN60="$EVIDENCE_ROOT/green-60"
git worktree add --detach "$RED60" e186c68
git worktree add --detach "$GREEN60" "$FINAL_SHA"
mkdir -p "$RED60/kotlin/app/src/test/java/com/precor/treadmill" "$GREEN60/kotlin/app/src/test/java/com/precor/treadmill"
git show 872281b:kotlin/app/src/test/java/com/precor/treadmill/WakeWordActivationPolicyTest.kt | tee "$RED60/kotlin/app/src/test/java/com/precor/treadmill/WakeWordActivationPolicyTest.kt" > "$GREEN60/kotlin/app/src/test/java/com/precor/treadmill/WakeWordActivationPolicyTest.kt"
test "$(sha256sum "$RED60/kotlin/app/src/test/java/com/precor/treadmill/WakeWordActivationPolicyTest.kt" | cut -d' ' -f1)" = "$(sha256sum "$GREEN60/kotlin/app/src/test/java/com/precor/treadmill/WakeWordActivationPolicyTest.kt" | cut -d' ' -f1)"
```

From `"$RED60/kotlin"`, preserve the true exit status:

```bash
set -o pipefail
./gradlew testDebugUnitTest --tests com.precor.treadmill.WakeWordActivationPolicyTest 2>&1 | tee "$EVIDENCE_ROOT/issue-60-red.log"
RED60_STATUS=${PIPESTATUS[0]}
test "$RED60_STATUS" -ne 0
```

Expected: nonzero compile failure because the debounce/rearm policy does not exist. Save unedited relevant output and exit status; label it as a compile-time RED.

- [ ] **Step 2: Run #60 GREEN on pinned main**

From `"$GREEN60/kotlin"`, run the identical focused test source and command with `tee`/`${PIPESTATUS[0]}`. Expected: success. Keep the separate current-main focused suite from Task 3 as broader current coverage.

- [ ] **Step 3: Reproduce #62 RED with the identical regression source**

Create detached RED and GREEN worktrees at `e186c68` and `"$FINAL_SHA"`. Replace `python/tests/test_server_integration.py` in both with the exact file from fix commit `f7cef1e` (a disclosed test-only patch), and prove the hashes match:

```bash
RED62="$EVIDENCE_ROOT/red-62"; GREEN62="$EVIDENCE_ROOT/green-62"
git worktree add --detach "$RED62" e186c68
git worktree add --detach "$GREEN62" "$FINAL_SHA"
git show f7cef1e:python/tests/test_server_integration.py | tee "$RED62/python/tests/test_server_integration.py" > "$GREEN62/python/tests/test_server_integration.py"
test "$(sha256sum "$RED62/python/tests/test_server_integration.py" | cut -d' ' -f1)" = "$(sha256sum "$GREEN62/python/tests/test_server_integration.py" | cut -d' ' -f1)"
```

Run from `"$RED62"` with `tee`/`${PIPESTATUS[0]}`:

```bash
set -o pipefail
pytest -q python/tests/test_server_integration.py::TestHistoryResume 2>&1 | tee "$EVIDENCE_ROOT/issue-62-red.log"
RED62_STATUS=${PIPESTATUS[0]}
test "$RED62_STATUS" -ne 0
```

Expected: nonzero assertion failures for stale interval/terminal resume behavior. Save unedited relevant output and exit status.

- [ ] **Step 4: Run #62 GREEN on pinned main**

Run the identical class command from `"$GREEN62"` with `tee`/`${PIPESTATUS[0]}`. Expected: all eight `TestHistoryResume` tests pass. The clean pinned-main full suite remains the broader gate.

- [ ] **Step 5: Remove disposable RED worktrees**

Confirm `git status --short` in each evidence worktree lists only its disclosed test patch, use `git worktree remove --force` on exact `$RED60`, `$GREEN60`, `$RED62`, and `$GREEN62` paths, then `git worktree prune`. Retain only evidence logs in the OS temporary directory.

### Task 5: Establish a hard-interlocked mock backend

**Files:**
- Temporary: worktree-specific `worktree.env`, mock SQLite DB, server log

- [ ] **Step 1: Allocate a unique port and seed mock history**

Run `scripts/setup-worktree.sh` in the pinned worktree. With a temporary `TREADMILL_DB`, create a two-interval resumable program with stale stored interval but authoritative elapsed position, plus a terminal entry at its final boundary:

```bash
(cd "$FINAL_TREE" && scripts/setup-worktree.sh)
source "$FINAL_TREE/worktree.env"
export TREADMILL_MOCK=1
export TREADMILL_DB="$EVIDENCE_ROOT/mock-treadmill.db"
PYTHONPATH="$FINAL_TREE/python" python3 - <<'PY' > "$EVIDENCE_ROOT/mock-history-ids.json"
import json
import server
resumable = {
    "name": "Evidence Resume 1:50",
    "intervals": [
        {"name": "Warmup", "duration": 120, "speed": 3.0, "incline": 0},
        {"name": "Work", "duration": 120, "speed": 5.0, "incline": 2},
    ],
}
terminal = {
    "name": "Evidence Completed",
    "intervals": [{"name": "Only", "duration": 60, "speed": 3.0, "incline": 0}],
}
r = server._add_to_history(resumable, "closure evidence")
server.db.update_history_entry(r["id"], completed=False, last_interval=0, last_elapsed=130)
t = server._add_to_history(terminal, "closure evidence")
server.db.update_history_entry(t["id"], completed=False, last_interval=0, last_elapsed=60)
print(json.dumps({"resumable": r["id"], "terminal": t["id"]}))
PY
jq -e '.resumable and .terminal' "$EVIDENCE_ROOT/mock-history-ids.json"
```

- [ ] **Step 2: Start the isolated server**

Launch `python/server.py` with the temporary DB and allocated port. Resolve the host LAN address along the tablet route, capture PID, and prove the environment and endpoints:

```bash
env TREADMILL_MOCK=1 TREADMILL_DB="$TREADMILL_DB" TREADMILL_SERVER_PORT="$TREADMILL_SERVER_PORT" python3 "$FINAL_TREE/python/server.py" > "$EVIDENCE_ROOT/mock-server.log" 2>&1 &
MOCK_PID=$!
printf '%s\n' "$MOCK_PID" > "$EVIDENCE_ROOT/mock-server.pid"
for _ in $(seq 1 30); do rg -q 'Mock mode — no Pi connection' "$EVIDENCE_ROOT/mock-server.log" && break; sleep 1; done
rg -q 'Mock mode — no Pi connection' "$EVIDENCE_ROOT/mock-server.log"
tr '\0' '\n' < "/proc/$MOCK_PID/environ" | rg '^TREADMILL_MOCK=1$'
TABLET_IP=$(adb -s "$DEVICE_SERIAL" shell ip -4 addr show wlan0 | sed -n 's/.*inet \([0-9.]*\)\/.*/\1/p' | tr -d '\r')
HOST_IP=$(ip route get "$TABLET_IP" | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -1)
MOCK_URL="http://$HOST_IP:$TREADMILL_SERVER_PORT"
curl -fsS "$MOCK_URL/api/status" | jq -e '.motor == {} or .treadmill_connected == false'
curl -fsS "$MOCK_URL/api/program" | jq -e '.running == false'
curl -fsS "$MOCK_URL/api/programs/history" | jq -e 'length == 2'
```

- [ ] **Step 3: Retarget tablet without retaining altered preferences**

Repeat the real-server stationary preflight immediately here. Force-stop the app, save a distinct `pre-retarget-server-prefs.pb`, verify it hashes identically to `original-server-prefs.pb`, then use a recoverable internal rename (not data clearing) so Setup can target `"$MOCK_URL"`:

```bash
adb -s "$DEVICE_SERIAL" exec-out run-as "$PACKAGE" cat files/datastore/server_prefs.preferences_pb > "$EVIDENCE_ROOT/pre-retarget-server-prefs.pb"
cmp "$EVIDENCE_ROOT/original-server-prefs.pb" "$EVIDENCE_ROOT/pre-retarget-server-prefs.pb"
adb -s "$DEVICE_SERIAL" shell am force-stop "$PACKAGE"
adb -s "$DEVICE_SERIAL" shell run-as "$PACKAGE" sh -c 'mv -f files/datastore/server_prefs.preferences_pb files/datastore/server_prefs.preferences_pb.pre-evidence'
adb -s "$DEVICE_SERIAL" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1
```

Use this temporary XML-center helper for every accessibility-selected tap:

```bash
ui_center() {
  local xml=$1 needle=$2
  python3 - "$xml" "$needle" <<'PY'
import re, sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
for node in root.iter("node"):
    haystack = " ".join((node.attrib.get("text", ""), node.attrib.get("content-desc", "")))
    if sys.argv[2] in haystack:
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", node.attrib["bounds"]))
        print((x1 + x2) // 2, (y1 + y2) // 2)
        raise SystemExit
raise SystemExit(f"UI node not found: {sys.argv[2]}")
PY
}
```

Dump UIAutomator XML, resolve `Server URL` with `ui_center`, tap it, move to end and send `KEYCODE_DEL` 200 times, enter `"$MOCK_URL"` with `adb shell input text`, resolve/tap `Connect`, and invoke IME Go if required. Re-dump XML and require connected lobby text. Before any mock program API, repeat the `/proc/$MOCK_PID/environ` check above. If any step after retargeting fails, execute Task 6 Step 4 cleanup immediately and post no issue comments.

### Task 6: Capture final-main tablet evidence

**Files:**
- Temporary PNG screenshots outside repository/worktrees

- [ ] **Step 1: Validate and capture #62 history behavior**

Dump UIAutomator XML and require `Evidence Resume 1:50`, `Resume · 1:50 left`, and `Evidence Completed`; because the fixture has exactly two entries, require exactly one `Resume ·` occurrence, proving the terminal entry has none. Capture outside the repository:

```bash
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-window.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-window.xml > "$EVIDENCE_ROOT/history-window.xml"
rg -F 'Resume · 1:50 left' "$EVIDENCE_ROOT/history-window.xml"
test "$(rg -o 'Resume ·' "$EVIDENCE_ROOT/history-window.xml" | wc -l)" -eq 1
adb -s "$DEVICE_SERIAL" exec-out screencap -p > "$EVIDENCE_ROOT/issue-62-history.png"
```

- [ ] **Step 2: Validate and capture #59 and #64 running behavior**

Call the mock resume endpoint for the resumable history ID, after one more `TREADMILL_MOCK=1` environment check. Confirm mock API running state. Dump UI XML and require the timer content description contains `remaining. Tap to count up` and the next-change description contains both relative and workout-mark values. Capture the shared #59/#64 screenshot. Then resolve the `Workout timer` node with `ui_center`, tap its center, re-dump XML, and require `elapsed. Tap to count down`; count-up verification is mandatory.

```bash
RESUME_ID=$(jq -r .resumable "$EVIDENCE_ROOT/mock-history-ids.json")
curl -fsS -X POST "$MOCK_URL/api/programs/history/$RESUME_ID/resume" | tee "$EVIDENCE_ROOT/mock-resume.json" | jq -e '.ok == true and .running == true'
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-running.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-running.xml > "$EVIDENCE_ROOT/running-window.xml"
rg -F 'remaining. Tap to count up' "$EVIDENCE_ROOT/running-window.xml"
rg -F 'Next change in ' "$EVIDENCE_ROOT/running-window.xml"
rg -F 'workout remaining at change' "$EVIDENCE_ROOT/running-window.xml"
adb -s "$DEVICE_SERIAL" exec-out screencap -p > "$EVIDENCE_ROOT/issues-59-64-running.png"
read TIMER_X TIMER_Y < <(ui_center "$EVIDENCE_ROOT/running-window.xml" 'Workout timer')
adb -s "$DEVICE_SERIAL" shell input tap "$TIMER_X" "$TIMER_Y"
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-count-up.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-count-up.xml > "$EVIDENCE_ROOT/count-up-window.xml"
rg -F 'elapsed. Tap to count down' "$EVIDENCE_ROOT/count-up-window.xml"
```

- [ ] **Step 3: Validate and capture #61 settings behavior**

The immutable original DataStore establishes Voice Input enabled and microphone denied with `USER_SET` on this tablet. Dump XML, resolve/tap `Settings`, re-dump, resolve/tap `Voice Input`, force-stop/relaunch, reopen Settings, and require the `Voice Input` node reports `checked="false"`. Capture Settings. Do not grant microphone permission; if a dialog appears, resolve/tap `Don't allow`. Final restoration comes from the immutable original DataStore, not another UI toggle.

```bash
adb -s "$DEVICE_SERIAL" exec-out screencap -p > "$EVIDENCE_ROOT/issue-61-settings.png"
```

- [ ] **Step 4: Restore exact tablet state and stop mock services**

This cleanup is unconditional after any retarget mutation, whether validation succeeds or fails. Force-stop the app; move the mock-created DataStore aside; restore the internally renamed original; compare its device-streamed hash to `original-server-prefs.sha256`; explicitly restore microphone denied with `USER_SET` and clear `user-fixed`; relaunch; verify the decoded server URL/Voice Input value match the immutable baseline and no connection remains on the mock server. Stop the captured PID and require the port to close. Leave the new pinned-main APK installed. Post no issue comments if restoration fails.

```bash
adb -s "$DEVICE_SERIAL" shell am force-stop "$PACKAGE"
adb -s "$DEVICE_SERIAL" shell run-as "$PACKAGE" sh -c 'mv -f files/datastore/server_prefs.preferences_pb files/datastore/server_prefs.preferences_pb.mock-evidence; mv -f files/datastore/server_prefs.preferences_pb.pre-evidence files/datastore/server_prefs.preferences_pb'
adb -s "$DEVICE_SERIAL" exec-out run-as "$PACKAGE" cat files/datastore/server_prefs.preferences_pb > "$EVIDENCE_ROOT/restored-server-prefs.pb"
cmp "$EVIDENCE_ROOT/original-server-prefs.pb" "$EVIDENCE_ROOT/restored-server-prefs.pb"
adb -s "$DEVICE_SERIAL" shell pm revoke "$PACKAGE" android.permission.RECORD_AUDIO
adb -s "$DEVICE_SERIAL" shell pm clear-permission-flags "$PACKAGE" android.permission.RECORD_AUDIO user-fixed
adb -s "$DEVICE_SERIAL" shell pm set-permission-flags "$PACKAGE" android.permission.RECORD_AUDIO user-set
adb -s "$DEVICE_SERIAL" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1
kill "$(cat "$EVIDENCE_ROOT/mock-server.pid")"
for _ in $(seq 1 20); do curl -fsS "$MOCK_URL/" >/dev/null 2>&1 || break; sleep 1; done
! curl -fsS "$MOCK_URL/" >/dev/null 2>&1
```

After hash verification, remove the exact internal `server_prefs.preferences_pb.mock-evidence` file with `run-as ... unlink`; verify it no longer exists. If a runtime microphone dialog appears at any point, choose `Don't allow`/dismiss via UIAutomator and repeat permission verification.

### Task 7: Upload native GitHub attachments and annotate issues

**Files:**
- Temporary comment bodies and images outside repository/worktrees

- [ ] **Step 1: Prepare and verify GitHub CLI 2.100.0**

Download the pinned files, verify checksum, extract, and save the absolute binary path:

```bash
gh release download v2.100.0 --repo cli/cli --pattern 'gh_2.100.0_linux_amd64.tar.gz' --pattern 'gh_2.100.0_checksums.txt' --dir "$EVIDENCE_ROOT"
(cd "$EVIDENCE_ROOT" && rg 'gh_2.100.0_linux_amd64.tar.gz' gh_2.100.0_checksums.txt | sha256sum -c -)
tar -xzf "$EVIDENCE_ROOT/gh_2.100.0_linux_amd64.tar.gz" -C "$EVIDENCE_ROOT"
GH_ATTACH="$EVIDENCE_ROOT/gh_2.100.0_linux_amd64/bin/gh"
"$GH_ATTACH" issue comment --help | rg -- '--attach'
```

- [ ] **Step 2: Compose five evidence comments**

Create `"$EVIDENCE_ROOT/issue-{59,60,61,62,64}-comment.md"`. Each body must identify problem/request, root cause or gap, exact fix/PR/final-main SHA, focused RED for #60/#62, focused GREEN and broad gate, device/mock context, belt-stationary statement, screenshot relevance, and any limitation. Do not claim a screenshot for #60.

Before composing or posting, run `git fetch origin` and require `"$FINAL_SHA" == "$(git rev-parse origin/main)"`. If it advanced, create a fresh detached worktree and repeat build, gates, install/pull checksum, focused GREENs, and affected tablet checks before posting anything.

- [ ] **Step 3: Post comments and attach relevant screenshots**

Use only the absolute verified `"$GH_ATTACH"` path. Retain the shared #59/#64 image until both comments and assets verify:

```bash
COMMENT59=$("$GH_ATTACH" issue comment 59 --repo scottmsilver/treddy --body-file "$EVIDENCE_ROOT/issue-59-comment.md" --attach "$EVIDENCE_ROOT/issues-59-64-running.png#Final-main running screen showing the countdown timer and next-change mark")
COMMENT60=$("$GH_ATTACH" issue comment 60 --repo scottmsilver/treddy --body-file "$EVIDENCE_ROOT/issue-60-comment.md")
COMMENT61=$("$GH_ATTACH" issue comment 61 --repo scottmsilver/treddy --body-file "$EVIDENCE_ROOT/issue-61-comment.md" --attach "$EVIDENCE_ROOT/issue-61-settings.png#Voice Input setting and microphone permission state on the final-main tablet build")
COMMENT62=$("$GH_ATTACH" issue comment 62 --repo scottmsilver/treddy --body-file "$EVIDENCE_ROOT/issue-62-comment.md" --attach "$EVIDENCE_ROOT/issue-62-history.png#Mock-backed history showing resumable and terminal workout behavior")
COMMENT64=$("$GH_ATTACH" issue comment 64 --repo scottmsilver/treddy --body-file "$EVIDENCE_ROOT/issue-64-comment.md" --attach "$EVIDENCE_ROOT/issues-59-64-running.png#Final-main next-change display with relative and absolute workout-clock times")
```

Post #60 without `--attach`. Verify each returned URL belongs to the intended issue. Read each comment via `gh api`, extract every `https://github.com/user-attachments/assets/...` URL, then require `curl -fsSIL` returns HTTP success and an `image/*` content type. Only after all consumers verify may screenshots be trashed. Canonicalize every PNG path with `realpath` and require it begins with `"$EVIDENCE_ROOT/"`.

- [ ] **Step 4: Verify all retrospective annotations**

Read back issue comments 59/60/61/62/64 through the API and independently check every required evidence heading and attachment URL. Re-run `git diff --name-only origin/main...codex/closure-evidence` and `git ls-files '*.png' '*.jpg' '*.jpeg'`; ensure this task introduced no tracked image and the documentation branch contains exactly `CLAUDE.md`, the design, and the plan.

### Task 8: Final repository and tracker verification

**Files:**
- Beads issue: `precor-9_3x-ah8`

- [ ] **Step 1: Verify authoritative main still matches deployed/commented evidence**

Fetch `origin/main`; compare it to the pinned deployed SHA. If it advanced after comments, repeat the build/install/validation loop and update the just-created comments so their SHA and evidence remain truthful before continuing.

- [ ] **Step 2: Close and sync the tracking bead**

Run `bd prime`, assess whether any validation limitation needs a follow-up bead, and create one if required. Then run:

```bash
bd close precor-9_3x-ah8 --reason "Final main deployed and checksum-verified on tablet; issues 59/60/61/62/64 annotated with RED/GREEN and native GitHub attachments where relevant; closure policy landed."
bd dolt push
```

If no Dolt remote is configured, report that accurately.

- [ ] **Step 3: Complete git session protocol**

From the clean `codex/closure-evidence` worktree, run `git pull --rebase`, `git push`, and `git status`, requiring it to report up to date with its origin branch. Fast-forward the primary `main` only if its existing `.beads`/`static/` changes are untouched; never stash/reset them.

- [ ] **Step 4: Remove temporary worktrees and artifacts**

After verified issue attachments and restoration, require every screenshot's `realpath` to begin with `"$EVIDENCE_ROOT/"`, remove the pinned/RED/GREEN worktrees with exact `git worktree remove` targets, and move the exact `"$EVIDENCE_ROOT"` directory to trash. Preserve the closure-evidence worktree if its PR workflow requires it.
