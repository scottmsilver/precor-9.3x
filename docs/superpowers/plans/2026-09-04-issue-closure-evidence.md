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
set -e
git add CLAUDE.md
for phrase in "Issue Closure Evidence" "root cause" "intended reason" "RED" "GREEN" "screenshot" "GitHub attachment" "unless the user explicitly requests" "device" "limitation" "evidence comment" "landed"; do rg -F "$phrase" CLAUDE.md >/dev/null || exit 1; done
git diff --cached --check
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

Start one persistent Bash PTY (`bash --noprofile --norc`, followed by `set -euo pipefail`) and execute Tasks 2 Step 3 through Task 8 in that same shell so variables and functions remain available. Create an OS evidence directory and a clean detached worktree at `origin/main`, save its full SHA, and verify a second fetch does not move it before build. Do not modify or stash the dirty primary worktree.

```bash
EVIDENCE_ROOT=$(mktemp -d /tmp/precor-closure.XXXXXX)
PRIMARY_ROOT=/home/ssilver/development/precor-9.3x
FINAL_PARENT=$(mktemp -d "$PRIMARY_ROOT/.worktrees/final-main.XXXXXX")
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

Use `DEVICE_SERIAL='adb-R9ZY90P5LZP-WMXOYu._adb-tls-connect._tcp'` and `PACKAGE='com.precor.treadmill'`. Before any mutation, save distinct `original-*` files: `dumpsys package`, resumed activity, UI XML, full raw DataStore, its SHA-256 and `protoc --decode_raw` output, Voice Input value, microphone grant/flags, and current connectivity. Extract the original server URL from the decoded DataStore. The observed API has real motor feedback at `.motor.belt`/`.motor.mph`; top-level `.speed` is a configured target and is not evidence of belt motion. Query `api/status`, `api/session`, and `api/program` read-only with pipe failures preserved and require connected motor feedback of zero, emulated speed zero, inactive session/program, and an idle UI with no workout timer or Pause control. Repeat this same preflight immediately before the first force-stop/retarget. Abort without commands to the equipment if any assertion is unavailable or ambiguous.

```bash
adb -s "$DEVICE_SERIAL" exec-out run-as "$PACKAGE" cat files/datastore/server_prefs.preferences_pb > "$EVIDENCE_ROOT/original-server-prefs.pb"
sha256sum "$EVIDENCE_ROOT/original-server-prefs.pb" > "$EVIDENCE_ROOT/original-server-prefs.sha256"
protoc --decode_raw < "$EVIDENCE_ROOT/original-server-prefs.pb" > "$EVIDENCE_ROOT/original-server-prefs.txt"
adb -s "$DEVICE_SERIAL" shell dumpsys package "$PACKAGE" > "$EVIDENCE_ROOT/original-package.txt"
adb -s "$DEVICE_SERIAL" shell dumpsys activity activities > "$EVIDENCE_ROOT/original-activity.txt"
adb -s "$DEVICE_SERIAL" shell dumpsys connectivity > "$EVIDENCE_ROOT/original-connectivity.txt"
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/original-window.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/original-window.xml > "$EVIDENCE_ROOT/original-window.xml"
ORIGINAL_SERVER=$(sed -n 's/.*"\(https\{0,1\}:\/\/[^\"]*\)".*/\1/p' "$EVIDENCE_ROOT/original-server-prefs.txt" | head -1)
ORIGINAL_VOICE_INPUT=$(awk '/"voice_input_enabled"/{getline; getline; print ($2 == 1 ? "true" : "false"); exit}' "$EVIDENCE_ROOT/original-server-prefs.txt")
ORIGINAL_MIC_LINE=$(rg 'android.permission.RECORD_AUDIO: granted=' "$EVIDENCE_ROOT/original-package.txt" | sed 's/^[[:space:]]*//')
printf '%s\n' "$ORIGINAL_VOICE_INPUT" > "$EVIDENCE_ROOT/original-voice-input.txt"
printf '%s\n' "$ORIGINAL_MIC_LINE" > "$EVIDENCE_ROOT/original-mic-line.txt"
set -o pipefail
curl -kfsS "$ORIGINAL_SERVER/api/status" | tee "$EVIDENCE_ROOT/original-status.json" | jq -e '.treadmill_connected == true and .motor.belt == "0" and .motor.mph == "0" and .emu_speed_mph == 0'
curl -kfsS "$ORIGINAL_SERVER/api/session" | tee "$EVIDENCE_ROOT/original-session.json" | jq -e '.active == false'
curl -kfsS "$ORIGINAL_SERVER/api/program" | tee "$EVIDENCE_ROOT/original-program.json" | jq -e '.running == false'
! rg -q 'Workout timer|Pause workout' "$EVIDENCE_ROOT/original-window.xml"
```

- [ ] **Step 5: Preserve rollback APK**

Pull the currently installed base APK to `"$EVIDENCE_ROOT/original-base.apk"`; the immutable DataStore source is already `original-server-prefs.pb`. Never uninstall or clear app data.

```bash
ORIGINAL_APK_PATH=$(adb -s "$DEVICE_SERIAL" shell pm path "$PACKAGE" | tr -d '\r' | sed 's/^package://')
adb -s "$DEVICE_SERIAL" pull "$ORIGINAL_APK_PATH" "$EVIDENCE_ROOT/original-base.apk"
sha256sum "$EVIDENCE_ROOT/original-base.apk" > "$EVIDENCE_ROOT/original-base.sha256"
```

- [ ] **Step 6: Install and verify the exact artifact with rollback on every mismatch**

Define `restore_mic` from the freshly captured `ORIGINAL_MIC_LINE`: clear `user-set`/`user-fixed`, grant or revoke according to `granted=`, then restore each captured mutable flag and compare the resulting grant/user flag signature. Define `rollback_apk` to reinstall `original-base.apk`, stream the immutable DataStore back through `run-as`, call `restore_mic`, launch, pull the rollback APK, verify its checksum, and exit nonzero. Run serial-qualified `adb install -r`, launch the package, and verify application ID, version code/name, installed path, resumed activity, and byte-identical installed APK inside a checked block; invoke `rollback_apk` on any failure.

```bash
restore_mic() {
  adb -s "$DEVICE_SERIAL" shell pm clear-permission-flags "$PACKAGE" android.permission.RECORD_AUDIO user-set user-fixed
  if [[ "$ORIGINAL_MIC_LINE" == *'granted=true'* ]]; then adb -s "$DEVICE_SERIAL" shell pm grant "$PACKAGE" android.permission.RECORD_AUDIO; else adb -s "$DEVICE_SERIAL" shell pm revoke "$PACKAGE" android.permission.RECORD_AUDIO; fi
  [[ "$ORIGINAL_MIC_LINE" == *'USER_SET'* ]] && adb -s "$DEVICE_SERIAL" shell pm set-permission-flags "$PACKAGE" android.permission.RECORD_AUDIO user-set || true
  [[ "$ORIGINAL_MIC_LINE" == *'USER_FIXED'* ]] && adb -s "$DEVICE_SERIAL" shell pm set-permission-flags "$PACKAGE" android.permission.RECORD_AUDIO user-fixed || true
}
rollback_apk() {
  adb -s "$DEVICE_SERIAL" install -r "$EVIDENCE_ROOT/original-base.apk"
  adb -s "$DEVICE_SERIAL" shell am force-stop "$PACKAGE"
  adb -s "$DEVICE_SERIAL" shell run-as "$PACKAGE" sh -c 'cat > files/datastore/server_prefs.preferences_pb' < "$EVIDENCE_ROOT/original-server-prefs.pb"
  restore_mic
  adb -s "$DEVICE_SERIAL" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1
  local rollback_path
  rollback_path=$(adb -s "$DEVICE_SERIAL" shell pm path "$PACKAGE" | tr -d '\r' | sed 's/^package://')
  adb -s "$DEVICE_SERIAL" pull "$rollback_path" "$EVIDENCE_ROOT/rollback-verified.apk"
  cmp "$EVIDENCE_ROOT/original-base.apk" "$EVIDENCE_ROOT/rollback-verified.apk"
  return 1
}
if ! {
  adb -s "$DEVICE_SERIAL" install -r "$APK" &&
  adb -s "$DEVICE_SERIAL" shell am force-stop "$PACKAGE" &&
  adb -s "$DEVICE_SERIAL" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1 &&
  adb -s "$DEVICE_SERIAL" shell dumpsys package "$PACKAGE" > "$EVIDENCE_ROOT/installed-package.txt" &&
  rg -q 'versionCode=1' "$EVIDENCE_ROOT/installed-package.txt" &&
  rg -q 'versionName=1.0' "$EVIDENCE_ROOT/installed-package.txt" &&
  INSTALLED_APK_PATH=$(adb -s "$DEVICE_SERIAL" shell pm path "$PACKAGE" | tr -d '\r' | sed 's/^package://') &&
  adb -s "$DEVICE_SERIAL" pull "$INSTALLED_APK_PATH" "$EVIDENCE_ROOT/installed-base.apk" &&
  cmp "$APK" "$EVIDENCE_ROOT/installed-base.apk" &&
  for _ in $(seq 1 20); do adb -s "$DEVICE_SERIAL" shell dumpsys activity activities | rg -q "(mResumedActivity|ResumedActivity).*${PACKAGE}" && break; sleep 1; done &&
  adb -s "$DEVICE_SERIAL" shell dumpsys activity activities | rg "(mResumedActivity|ResumedActivity).*${PACKAGE}";
}; then rollback_apk; exit 1; fi
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
set +e
set -o pipefail
./gradlew testDebugUnitTest --tests com.precor.treadmill.WakeWordActivationPolicyTest 2>&1 | tee "$EVIDENCE_ROOT/issue-60-red.log"
RED60_STATUS=${PIPESTATUS[0]}
set -e
printf '%s\n' "$RED60_STATUS" > "$EVIDENCE_ROOT/issue-60-red.status"
test "$RED60_STATUS" -ne 0
rg -q "Unresolved reference.*WakeWordActivationPolicy" "$EVIDENCE_ROOT/issue-60-red.log"
```

Expected: nonzero compile failure because the debounce/rearm policy does not exist. Save unedited relevant output and exit status; label it as a compile-time RED.

- [ ] **Step 2: Run #60 GREEN on pinned main**

From `"$GREEN60/kotlin"`, run the identical focused test source and command. Keep the separate current-main focused suite from Task 3 as broader current coverage.

```bash
(cd "$GREEN60/kotlin" && set -o pipefail; ./gradlew testDebugUnitTest --tests com.precor.treadmill.WakeWordActivationPolicyTest 2>&1 | tee "$EVIDENCE_ROOT/issue-60-green.log"; printf '%s\n' "${PIPESTATUS[0]}" > "$EVIDENCE_ROOT/issue-60-green.status"; test "$(cat "$EVIDENCE_ROOT/issue-60-green.status")" -eq 0)
```

- [ ] **Step 3: Reproduce #62 RED with the identical regression source**

Create detached RED and GREEN worktrees at `e186c68` and `"$FINAL_SHA"`. Replace `python/tests/test_server_integration.py` in both with the exact file from fix commit `f7cef1e` (a disclosed test-only patch), and prove the hashes match:

```bash
RED62="$EVIDENCE_ROOT/red-62"; GREEN62="$EVIDENCE_ROOT/green-62"
git worktree add --detach "$RED62" e186c68
git worktree add --detach "$GREEN62" "$FINAL_SHA"
git show f7cef1e:python/tests/test_server_integration.py | tee "$RED62/python/tests/test_server_integration.py" > "$GREEN62/python/tests/test_server_integration.py"
test "$(sha256sum "$RED62/python/tests/test_server_integration.py" | cut -d' ' -f1)" = "$(sha256sum "$GREEN62/python/tests/test_server_integration.py" | cut -d' ' -f1)"
```

Run from `"$RED62"` with `tee`/`${PIPESTATUS[0]}`. Change directory outside the expected-failure pipeline so inherited fail-fast mode cannot terminate before the status is captured:

```bash
set +e
cd "$RED62"
set -o pipefail
pytest -q python/tests/test_server_integration.py::TestHistoryResume 2>&1 | tee "$EVIDENCE_ROOT/issue-62-red.log"
RED62_STATUS=${PIPESTATUS[0]}
set -e
printf '%s\n' "$RED62_STATUS" > "$EVIDENCE_ROOT/issue-62-red.status"
test "$RED62_STATUS" -ne 0
rg -q 'test_resume_derives_interval_from_saved_elapsed_time|test_terminal_history_position_is_not_resumable|test_stop_at_terminal_position_persists_completed' "$EVIDENCE_ROOT/issue-62-red.log"
```

Expected: nonzero assertion failures for stale interval/terminal resume behavior. Save unedited relevant output and exit status.

- [ ] **Step 4: Run #62 GREEN on pinned main**

Run the identical class command from `"$GREEN62"`; require all nine tests pass. The clean pinned-main full suite remains the broader gate.

```bash
(cd "$GREEN62" && set -o pipefail; pytest -q python/tests/test_server_integration.py::TestHistoryResume 2>&1 | tee "$EVIDENCE_ROOT/issue-62-green.log"; printf '%s\n' "${PIPESTATUS[0]}" > "$EVIDENCE_ROOT/issue-62-green.status"; test "$(cat "$EVIDENCE_ROOT/issue-62-green.status")" -eq 0; rg -q '9 passed' "$EVIDENCE_ROOT/issue-62-green.log")
```

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
import os
from db import TreadmillDB
db = TreadmillDB(os.environ["TREADMILL_DB"])
profile = db.create_profile("Closure Evidence")
db.set_active_profile_id(profile["id"])
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
r = db.add_to_history(profile["id"], resumable, prompt="closure evidence")
db.update_history_entry(r["id"], completed=False, last_interval=0, last_elapsed=130)
t = db.add_to_history(profile["id"], terminal, prompt="closure evidence")
db.update_history_entry(t["id"], completed=False, last_interval=0, last_elapsed=60)
print(json.dumps({"resumable": r["id"], "terminal": t["id"]}))
db._read.close()
db._write.close()
PY
jq -e '.resumable and .terminal' "$EVIDENCE_ROOT/mock-history-ids.json"
```

- [ ] **Step 2: Start the isolated server**

Launch `python/server.py` with the temporary DB and allocated port. Resolve the host LAN address along the tablet route, capture PID, and prove the environment and endpoints:

```bash
(cd "$FINAL_TREE" && env TREADMILL_MOCK=1 TREADMILL_DB="$TREADMILL_DB" TREADMILL_SERVER_PORT="$TREADMILL_SERVER_PORT" python3 python/server.py) > "$EVIDENCE_ROOT/mock-server.log" 2>&1 &
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

Repeat the real-server stationary preflight immediately here. Force-stop the app, save a distinct `pre-retarget-server-prefs.pb`, verify it hashes identically to `original-server-prefs.pb`, and make a recoverable internal copy. Do not use the Setup screen for this evidence run: mDNS can auto-connect between a UI dump and a tap, making coordinate automation unsafe. Instead, generate the same AndroidX Preferences protobuf directly, first proving the encoder recreates the immutable original byte-for-byte, then changing only `server_url` to `"$MOCK_URL"`:

```bash
adb -s "$DEVICE_SERIAL" exec-out run-as "$PACKAGE" cat files/datastore/server_prefs.preferences_pb > "$EVIDENCE_ROOT/pre-retarget-server-prefs.pb"
cmp "$EVIDENCE_ROOT/original-server-prefs.pb" "$EVIDENCE_ROOT/pre-retarget-server-prefs.pb"
adb -s "$DEVICE_SERIAL" shell am force-stop "$PACKAGE"
adb -s "$DEVICE_SERIAL" shell "run-as $PACKAGE cp -f files/datastore/server_prefs.preferences_pb files/datastore/server_prefs.preferences_pb.pre-evidence"
python3 - "$ORIGINAL_SERVER" "$MOCK_URL" "$EVIDENCE_ROOT/original-encoded.pb" "$EVIDENCE_ROOT/mock-server-prefs.pb" <<'PY'
import sys
def v(n):
    out=bytearray()
    while n > 127: out.append((n & 127) | 128); n >>= 7
    out.append(n); return bytes(out)
def field(tag, payload): return bytes([tag]) + v(len(payload)) + payload
def entry(key, value): return field(0x0a, field(0x0a, key.encode()) + field(0x12, value))
def prefs(url):
    return (entry("server_url", field(0x2a, url.encode())) +
            entry("voice_input_enabled", b"\x08\x01") +
            entry("microphone_permission_requested", b"\x08\x01"))
open(sys.argv[3], "wb").write(prefs(sys.argv[1]))
open(sys.argv[4], "wb").write(prefs(sys.argv[2]))
PY
cmp "$EVIDENCE_ROOT/original-server-prefs.pb" "$EVIDENCE_ROOT/original-encoded.pb"
adb -s "$DEVICE_SERIAL" push "$EVIDENCE_ROOT/mock-server-prefs.pb" /data/local/tmp/treddy-mock-server-prefs.pb
adb -s "$DEVICE_SERIAL" shell "run-as $PACKAGE cp -f /data/local/tmp/treddy-mock-server-prefs.pb files/datastore/server_prefs.preferences_pb"
adb -s "$DEVICE_SERIAL" shell rm -f /data/local/tmp/treddy-mock-server-prefs.pb
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

Decode and require the installed temporary DataStore URL is exactly `"$MOCK_URL"`. Launch, require the profile/lobby UI, and require `mock-server.log` to contain the tablet WebSocket acceptance/connection emitted after that launch. Before any mock program API, repeat the `/proc/$MOCK_PID/environ` check above. If any step after retargeting fails, execute Task 6 Step 4 cleanup immediately and post no issue comments.

```bash
adb -s "$DEVICE_SERIAL" exec-out run-as "$PACKAGE" cat files/datastore/server_prefs.preferences_pb > "$EVIDENCE_ROOT/installed-mock-server-prefs.pb"
cmp "$EVIDENCE_ROOT/mock-server-prefs.pb" "$EVIDENCE_ROOT/installed-mock-server-prefs.pb"
protoc --decode_raw < "$EVIDENCE_ROOT/installed-mock-server-prefs.pb" > "$EVIDENCE_ROOT/mock-server-prefs.txt"
TEMP_SERVER=$(sed -n 's/.*"\(https\{0,1\}:\/\/[^\"]*\)".*/\1/p' "$EVIDENCE_ROOT/mock-server-prefs.txt" | head -1)
test "$TEMP_SERVER" = "$MOCK_URL"
: > "$EVIDENCE_ROOT/mock-server-post-target.log"
tail -n 0 -F "$EVIDENCE_ROOT/mock-server.log" > "$EVIDENCE_ROOT/mock-server-post-target.log" &
MOCK_TAIL_PID=$!
adb -s "$DEVICE_SERIAL" shell am start -W -n "$PACKAGE/.MainActivity"
for _ in $(seq 1 20); do rg -q 'WebSocket.*(accept|connect)|connection open' "$EVIDENCE_ROOT/mock-server-post-target.log" && break; sleep 1; done
kill "$MOCK_TAIL_PID" 2>/dev/null || true
rg -q 'WebSocket.*(accept|connect)|connection open' "$EVIDENCE_ROOT/mock-server-post-target.log"
```

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

Recheck `/proc/$MOCK_PID/environ`, then stop only the mock program through its API, require inactive mock session, reveal/tap `Exit to home`, and require Lobby. The immutable baseline provides the original Voice Input value. Dump XML, resolve/tap `Settings`, re-dump, resolve/tap the `Voice Input` switch, force-stop/relaunch, reopen Settings, and require the switch has the opposite checked state plus visible `Microphone Permission` text. Capture Settings. Do not grant microphone permission; if a dialog appears, resolve/tap `Don't allow`. Final restoration comes from the immutable original DataStore, not another UI toggle.

```bash
tr '\0' '\n' < "/proc/$MOCK_PID/environ" | rg '^TREADMILL_MOCK=1$'
curl -fsS -X POST "$MOCK_URL/api/program/stop" | jq -e '.running == false'
curl -fsS "$MOCK_URL/api/session" | jq -e '.active == false'
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-exit.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-exit.xml > "$EVIDENCE_ROOT/exit-window.xml"
if ! rg -q 'Exit to home' "$EVIDENCE_ROOT/exit-window.xml"; then
  adb -s "$DEVICE_SERIAL" shell input tap 40 40
  adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-exit.xml
  adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-exit.xml > "$EVIDENCE_ROOT/exit-window.xml"
fi
read EXIT_X EXIT_Y < <(ui_center "$EVIDENCE_ROOT/exit-window.xml" 'Exit to home')
adb -s "$DEVICE_SERIAL" shell input tap "$EXIT_X" "$EXIT_Y"
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-lobby.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-lobby.xml > "$EVIDENCE_ROOT/lobby-window.xml"
rg -q 'Start Program|RECENT PROGRAMS' "$EVIDENCE_ROOT/lobby-window.xml"
read SETTINGS_X SETTINGS_Y < <(ui_center "$EVIDENCE_ROOT/lobby-window.xml" 'Settings')
adb -s "$DEVICE_SERIAL" shell input tap "$SETTINGS_X" "$SETTINGS_Y"
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-settings.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-settings.xml > "$EVIDENCE_ROOT/settings-before.xml"
rg -F 'Microphone Permission' "$EVIDENCE_ROOT/settings-before.xml"
read VOICE_X VOICE_Y < <(ui_center "$EVIDENCE_ROOT/settings-before.xml" 'Voice Input')
adb -s "$DEVICE_SERIAL" shell input tap "$VOICE_X" "$VOICE_Y"
for _ in $(seq 1 20); do
  adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-settings-toggle.xml >/dev/null
  adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-settings-toggle.xml > "$EVIDENCE_ROOT/settings-toggle.xml"
  VOICE_CHECKED=$(python3 - "$EVIDENCE_ROOT/settings-toggle.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
for node in ET.parse(sys.argv[1]).getroot().iter("node"):
    if node.attrib.get("checkable") == "true" and any(child.attrib.get("text") == "Voice Input" for child in node.iter("node")):
        print(node.attrib["checked"]); break
PY
)
  if [ "$ORIGINAL_VOICE_INPUT" = true ]; then [ "$VOICE_CHECKED" = false ] && break; else [ "$VOICE_CHECKED" = true ] && break; fi
  sleep 1
done
if [ "$ORIGINAL_VOICE_INPUT" = true ]; then [ "$VOICE_CHECKED" = false ]; else [ "$VOICE_CHECKED" = true ]; fi
adb -s "$DEVICE_SERIAL" shell am force-stop "$PACKAGE"
adb -s "$DEVICE_SERIAL" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-lobby-after-relaunch.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-lobby-after-relaunch.xml > "$EVIDENCE_ROOT/lobby-after-relaunch.xml"
read SETTINGS_X SETTINGS_Y < <(ui_center "$EVIDENCE_ROOT/lobby-after-relaunch.xml" 'Settings')
adb -s "$DEVICE_SERIAL" shell input tap "$SETTINGS_X" "$SETTINGS_Y"
adb -s "$DEVICE_SERIAL" shell uiautomator dump /sdcard/closure-settings-after.xml
adb -s "$DEVICE_SERIAL" exec-out cat /sdcard/closure-settings-after.xml > "$EVIDENCE_ROOT/settings-after.xml"
VOICE_CHECKED=$(python3 - "$EVIDENCE_ROOT/settings-after.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
for node in ET.parse(sys.argv[1]).getroot().iter("node"):
    if node.attrib.get("checkable") == "true" and any(child.attrib.get("text") == "Voice Input" for child in node.iter("node")):
        print(node.attrib["checked"]); break
PY
)
if [ "$ORIGINAL_VOICE_INPUT" = true ]; then [ "$VOICE_CHECKED" = false ]; else [ "$VOICE_CHECKED" = true ]; fi
rg -F 'Microphone Permission' "$EVIDENCE_ROOT/settings-after.xml"
adb -s "$DEVICE_SERIAL" exec-out screencap -p > "$EVIDENCE_ROOT/issue-61-settings.png"
```

- [ ] **Step 4: Restore exact tablet state and stop mock services**

This cleanup is unconditional after any retarget mutation, whether validation succeeds or fails. Force-stop the app; move the mock-created DataStore aside; restore the internally renamed original; compare its device-streamed hash to `original-server-prefs.sha256`; explicitly restore microphone denied with `USER_SET` and clear `user-fixed`; relaunch; verify the decoded server URL/Voice Input value match the immutable baseline and no connection remains on the mock server. Stop the captured PID and require the port to close. Leave the new pinned-main APK installed. Post no issue comments if restoration fails.

```bash
adb -s "$DEVICE_SERIAL" shell am force-stop "$PACKAGE"
adb -s "$DEVICE_SERIAL" shell "run-as $PACKAGE mv -f files/datastore/server_prefs.preferences_pb files/datastore/server_prefs.preferences_pb.mock-evidence"
adb -s "$DEVICE_SERIAL" shell "run-as $PACKAGE mv -f files/datastore/server_prefs.preferences_pb.pre-evidence files/datastore/server_prefs.preferences_pb"
adb -s "$DEVICE_SERIAL" exec-out run-as "$PACKAGE" cat files/datastore/server_prefs.preferences_pb > "$EVIDENCE_ROOT/restored-server-prefs.pb"
cmp "$EVIDENCE_ROOT/original-server-prefs.pb" "$EVIDENCE_ROOT/restored-server-prefs.pb"
restore_mic
adb -s "$DEVICE_SERIAL" shell monkey -p "$PACKAGE" -c android.intent.category.LAUNCHER 1
adb -s "$DEVICE_SERIAL" shell dumpsys package "$PACKAGE" | rg 'android.permission.RECORD_AUDIO: granted=' | sed 's/^[[:space:]]*//' > "$EVIDENCE_ROOT/restored-mic-line.txt"
test "$(sed -E 's/USER_SENSITIVE_[A-Z_|]*//g; s/[| ]+/ /g' "$EVIDENCE_ROOT/original-mic-line.txt")" = "$(sed -E 's/USER_SENSITIVE_[A-Z_|]*//g; s/[| ]+/ /g' "$EVIDENCE_ROOT/restored-mic-line.txt")"
protoc --decode_raw < "$EVIDENCE_ROOT/restored-server-prefs.pb" > "$EVIDENCE_ROOT/restored-server-prefs.txt"
cmp "$EVIDENCE_ROOT/original-server-prefs.txt" "$EVIDENCE_ROOT/restored-server-prefs.txt"
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

Create `"$EVIDENCE_ROOT/issue-{59,60,61,62,64}-comment.md"` with `printf`, using these exact facts and links:

- #59: problem was count-up-only timer; gap was direct session-elapsed rendering with no timer mode; fix `890c4f8`, PR `https://github.com/scottmsilver/treddy/pull/67`; include #59 focused GREEN and final Android gate.
- #60: problem was repeated/false wake activation; root cause was callbacks could reactivate without a listening restart/rearm policy; fix `872281b` plus integration hardening `fa8a99d`, PR `https://github.com/scottmsilver/treddy/pull/66`; include compile-time RED status/output showing the absent policy, identical-source GREEN, and final Android gate. No screenshot.
- #61: problem was no persistent way to disable voice capture; gap spanned settings, permission recovery, and stale asynchronous work; implementation `b777f33` plus `fa8a99d`, PR `https://github.com/scottmsilver/treddy/pull/68`; include focused GREEN, persistence result, permission restoration, and final Android gate.
- #62: problem was wrong remaining time and terminal sessions offered as resumable; root cause was trusting separately persisted interval state instead of authoritative elapsed time and not rejecting the terminal boundary; fix `f7cef1e`, PR `https://github.com/scottmsilver/treddy/pull/65`; include behavioral RED status and the unedited failing test-name/result lines, identical-source nine-test GREEN, full 137-test server gate, and mock-tablet history result.
- #64: request was an absolute workout-clock mark beside relative next-change time; gap was no mark, and combined review also found detached server remainder could flicker; implementation `0ec58d4`, PR `https://github.com/scottmsilver/treddy/pull/69`; include focused GREEN, final Android gate, and stable mock-tablet result.

Every body uses headings `Problem`, `Root cause / gap`, `RED` (bugs only), `GREEN`, `Device validation`, and `Delivery`; names `"$FINAL_SHA"`; says screenshots were produced on a `TREADMILL_MOCK=1` backend with no Pi connection and the real belt remained stationary; and inserts relevant unedited log tails inside fenced code blocks. Use `tail -n` only to select contiguous unedited output; do not rewrite command output.

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

Post #60 without `--attach`. Capture IDs and verify issue ownership, required headings, attachments, and rendered content:

```bash
for pair in "59:$COMMENT59" "60:$COMMENT60" "61:$COMMENT61" "62:$COMMENT62" "64:$COMMENT64"; do
  issue=${pair%%:*}; url=${pair#*:}
  case "$url" in "https://github.com/scottmsilver/treddy/issues/$issue#issuecomment-"*) ;; *) exit 1;; esac
  id=${url##*issuecomment-}
  printf '%s\n' "$id" > "$EVIDENCE_ROOT/issue-$issue-comment.id"
  "$GH_ATTACH" api "repos/scottmsilver/treddy/issues/comments/$id" > "$EVIDENCE_ROOT/issue-$issue-comment.json"
  test "$(jq -r .issue_url "$EVIDENCE_ROOT/issue-$issue-comment.json")" = "https://api.github.com/repos/scottmsilver/treddy/issues/$issue"
  for heading in 'Problem' 'Root cause / gap' 'GREEN' 'Device validation' 'Delivery'; do jq -er .body "$EVIDENCE_ROOT/issue-$issue-comment.json" | rg -F "$heading" >/dev/null; done
done
for issue in 59 60 61 62 64; do
  body=$(jq -r .body "$EVIDENCE_ROOT/issue-$issue-comment.json")
  printf '%s' "$body" | rg -F "$FINAL_SHA" >/dev/null
  printf '%s' "$body" | rg -F 'TREADMILL_MOCK=1' >/dev/null
  printf '%s' "$body" | rg -i 'belt remained stationary' >/dev/null
  case "$issue" in
    59) pr=67 ;;
    60) pr=66; printf '%s' "$body" | rg -F 'RED' >/dev/null ;;
    61) pr=68 ;;
    62) pr=65; printf '%s' "$body" | rg -F 'RED' >/dev/null ;;
    64) pr=69 ;;
  esac
  printf '%s' "$body" | rg -F "https://github.com/scottmsilver/treddy/pull/$pr" >/dev/null
done
for issue in 59 61 62 64; do
  jq -r .body "$EVIDENCE_ROOT/issue-$issue-comment.json" | rg -o 'https://github.com/user-attachments/assets/[^ )]+' > "$EVIDENCE_ROOT/issue-$issue-assets.txt"
  test -s "$EVIDENCE_ROOT/issue-$issue-assets.txt"
  while read -r asset; do curl -fsSIL "$asset" | tee "$EVIDENCE_ROOT/issue-$issue-asset-headers.txt" | rg -i '^content-type: image/'; done < "$EVIDENCE_ROOT/issue-$issue-assets.txt"
done
for image in "$EVIDENCE_ROOT/issue-62-history.png" "$EVIDENCE_ROOT/issue-61-settings.png" "$EVIDENCE_ROOT/issues-59-64-running.png"; do case "$(realpath "$image")" in "$EVIDENCE_ROOT"/*) ;; *) exit 1;; esac; done
```

- [ ] **Step 4: Verify all retrospective annotations**

Read back issue comments 59/60/61/62/64 through the API and independently check every required evidence heading and attachment URL. Re-run `git diff --name-only origin/main...codex/closure-evidence` and `git ls-files '*.png' '*.jpg' '*.jpeg'`; ensure this task introduced no tracked image and the documentation branch contains exactly `CLAUDE.md`, the design, and the plan.

### Task 8: Final repository and tracker verification

**Files:**
- Beads issue: `precor-9_3x-ah8`

- [ ] **Step 1: Verify authoritative main still matches deployed/commented evidence**

Fetch `origin/main`; compare it to the pinned deployed SHA. If it advanced after comments, repeat Tasks 3–6 in a fresh clean detached worktree, regenerate all five body files, and update each just-created comment with the pinned `"$GH_ATTACH" issue comment <issue> --edit-last --body-file ...` command (plus the new `--attach` file for 59/61/62/64). Verify the returned comment IDs equal the saved IDs and repeat all ownership/attachment checks before continuing.

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
