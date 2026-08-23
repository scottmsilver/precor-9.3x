# Prominent Amber Minimap Lens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Ridgeline minimap viewport lens clearly visible with an inset amber rim while preserving its glass fill, geometry, and visibility behavior.

**Architecture:** Expose the three approved rim constants through a small production-used style value, assert them in the existing JVM lens suite, and consume that value in the current Canvas draw pass. Keep the dark separator outside the lens and inset the amber stroke so the two edges do not cover one another.

**Tech Stack:** Kotlin, Jetpack Compose Canvas, JUnit 4, Gradle Android unit tests, wireless ADB on Galaxy SM-X115.

---

## File map

- Modify `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt`: add the lens rim style seam and render the inset amber stroke.
- Modify `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineViewportLensTest.kt`: assert the production color, opacity, and density-independent width.
- Write ignored verification evidence under `build/verification/2026-08-23-amber-lens/`.

### Task 1: Add and render the amber lens rim

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt:689-714,1255-1318`
- Test: `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineViewportLensTest.kt`

- [ ] **Step 1: Write the failing style test**

Use value equality for Compose's value-class `Color`:

```kotlin
@Test
fun lensRimUsesTheApprovedAmberTreatment() {
    assertEquals(RidgelineTheme.elev, minimapViewportLensStyle.rimColor)
    assertEquals(0.75f, minimapViewportLensStyle.rimAlpha, 0f)
    assertEquals(2f, minimapViewportLensStyle.rimWidthDp, 0f)
}
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest \
  --tests '*RidgelineViewportLensTest' --rerun-tasks
```

Expected: test compilation fails because `minimapViewportLensStyle` does not exist.

- [ ] **Step 3: Add the production style seam**

Near `ViewportLens`, add:

```kotlin
internal data class ViewportLensStyle(
    val rimColor: Color,
    val rimAlpha: Float,
    val rimWidthDp: Float,
)

internal val minimapViewportLensStyle = ViewportLensStyle(
    rimColor = RidgelineTheme.elev,
    rimAlpha = 0.75f,
    rimWidthDp = 2f,
)
```

- [ ] **Step 4: Replace the faint rim with the inset amber rim**

Keep the existing dark outer stroke and glass fill. Immediately after the fill,
replace the current one-pixel ivory `drawRoundRect` with:

```kotlin
val lensStyle = minimapViewportLensStyle
val rimWidth = lensStyle.rimWidthDp * dp
val rimInset = rimWidth / 2f
if (lens.width > rimWidth && lens.height > rimWidth) {
    drawRoundRect(
        color = lensStyle.rimColor,
        topLeft = Offset(lens.left + rimInset, lens.top + rimInset),
        size = Size(lens.width - rimWidth, lens.height - rimWidth),
        cornerRadius = androidx.compose.ui.geometry.CornerRadius(
            max(0f, lens.radius - rimInset),
            max(0f, lens.radius - rimInset),
        ),
        alpha = lensStyle.rimAlpha,
        style = Stroke(width = rimWidth),
    )
}
```

Leave the ivory top highlight and subdued bottom bounce after the amber rim.
Do not change `minimapViewportLens`, `hasMini`, leader lines, fill brush, or route
colors.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest \
  --tests '*RidgelineViewportLensTest' \
  --tests '*RidgelineRouteTest'
git diff --check
```

Expected: both test classes pass and diff check is empty.

- [ ] **Step 6: Commit the implementation**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt \
        kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineViewportLensTest.kt
git commit -m "feat(android): strengthen minimap lens edge"
```

### Task 2: Verify, land, and restore the device

**Files:**
- Verify: the two Task 1 files
- Evidence: `build/verification/2026-08-23-amber-lens/` (ignored)

- [ ] **Step 1: Run the complete Android gate**

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest assembleDebug
```

Expected: all Android unit tests pass and the debug APK is built.

- [ ] **Step 2: Create a failure-safe tablet verification script**

Using `apply_patch`, create the ignored file
`build/verification/2026-08-23-amber-lens/verify-lens.sh` with this executable
flow. The single process owns every mutable value used by its `EXIT` trap:

```bash
mkdir -p build/verification/2026-08-23-amber-lens
```

```bash
#!/usr/bin/env bash
set -euo pipefail

EVIDENCE=build/verification/2026-08-23-amber-lens
PACKAGE=com.precor.treadmill
ACTIVITY=com.precor.treadmill/.MainActivity
PREF=/data/user/0/$PACKAGE/files/datastore/server_prefs.preferences_pb
BASE_URL=http://127.0.0.1:44083
TMP_DIR=$(mktemp -d /tmp/amber-lens.XXXXXX)
SERVER_PID=
SERIAL=
PREF_PRESENT=0
WAS_RUNNING=0
RESTORE_FAILED=0

cleanup() {
  local original_exit=$?
  trap - EXIT
  set +e
  if [[ -n "$SERIAL" ]]; then
    adb -s "$SERIAL" shell am force-stop "$PACKAGE"
    if [[ "$PREF_PRESENT" == 1 ]]; then
      adb -s "$SERIAL" push "$EVIDENCE/server-prefs-before.pb" \
        /data/local/tmp/amber-lens-prefs.pb >/dev/null || RESTORE_FAILED=1
      adb -s "$SERIAL" shell run-as "$PACKAGE" cp -f \
        /data/local/tmp/amber-lens-prefs.pb "$PREF" || RESTORE_FAILED=1
      adb -s "$SERIAL" exec-out run-as "$PACKAGE" cat "$PREF" \
        >"$EVIDENCE/server-prefs-restored.pb" || RESTORE_FAILED=1
      cmp "$EVIDENCE/server-prefs-before.pb" \
        "$EVIDENCE/server-prefs-restored.pb" || RESTORE_FAILED=1
    else
      adb -s "$SERIAL" shell run-as "$PACKAGE" rm -f "$PREF" || RESTORE_FAILED=1
    fi
    if [[ "$WAS_RUNNING" == 1 ]]; then
      adb -s "$SERIAL" shell am start -n "$ACTIVITY" >/dev/null || RESTORE_FAILED=1
    fi
  fi
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    kill -0 "$SERVER_PID" 2>/dev/null && RESTORE_FAILED=1
  fi
  if [[ "$TMP_DIR" == /tmp/amber-lens.* && -d "$TMP_DIR" ]]; then
    rm -rf -- "$TMP_DIR"
  else
    RESTORE_FAILED=1
  fi
  printf 'original_exit=%s\nrestore_failed=%s\n' \
    "$original_exit" "$RESTORE_FAILED" >"$EVIDENCE/restoration.txt"
  if [[ "$original_exit" == 0 && "$RESTORE_FAILED" != 0 ]]; then original_exit=1; fi
  exit "$original_exit"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

mkdir -p "$EVIDENCE"
SERIAL=$(adb devices -l | awk '/model:SM_X115/ {print $1; exit}')
if [[ -z "$SERIAL" ]]; then
  adb mdns services | tee "$EVIDENCE/adb-mdns.txt"
  ENDPOINT=$(awk \
    '$1 ~ /^adb-R9ZY90P5LZP/ && $2 == "_adb-tls-connect._tcp" {print $3; exit}' \
    "$EVIDENCE/adb-mdns.txt")
  test -n "$ENDPOINT"
  adb connect "$ENDPOINT" | tee "$EVIDENCE/adb-connect.txt"
  SERIAL=$(adb devices -l | awk '/model:SM_X115/ {print $1; exit}')
fi
test -n "$SERIAL"
adb -s "$SERIAL" shell pidof "$PACKAGE" >/dev/null && WAS_RUNNING=1 || true
if adb -s "$SERIAL" shell run-as "$PACKAGE" test -f "$PREF"; then
  PREF_PRESENT=1
  adb -s "$SERIAL" exec-out run-as "$PACKAGE" cat "$PREF" \
    >"$EVIDENCE/server-prefs-before.pb"
fi

env TREADMILL_MOCK=1 TREADMILL_DB="$TMP_DIR/verification.db" \
  TREADMILL_SERVER_PORT=44083 python3 python/server.py \
  >"$EVIDENCE/server.log" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 50); do
  kill -0 "$SERVER_PID"
  curl --fail --silent "$BASE_URL/api/status" >/dev/null && break
  sleep 0.2
done
curl --fail --silent "$BASE_URL/api/status" >/dev/null

PROFILE=$(curl --fail --silent --show-error -X POST "$BASE_URL/api/profiles" \
  -H 'Content-Type: application/json' -d '{"name":"Amber Lens Verify"}' \
  | tee "$EVIDENCE/profile.json" | jq -er '.profile.id')
curl --fail --silent --show-error -X POST "$BASE_URL/api/profile/select" \
  -H 'Content-Type: application/json' -d "{\"id\":\"$PROFILE\"}" \
  | jq -e '.ok == true' >/dev/null
jq -n '{program:{name:"Glass Lens Demo",intervals:[range(0;12) |
  {name:("Hill "+tostring),duration:75,speed:(3.0+(. % 4)*0.4),
   incline:((. % 6)*2.5)}]},source:"manual"}' >"$EVIDENCE/workout-request.json"
WORKOUT=$(curl --fail --silent --show-error -X POST "$BASE_URL/api/workouts" \
  -H 'Content-Type: application/json' \
  --data-binary @"$EVIDENCE/workout-request.json" \
  | tee "$EVIDENCE/workout.json" | jq -er \
    'select(.ok == true and .workout.total_duration == 900) | .workout.id')
curl --fail --silent --show-error -X POST \
  "$BASE_URL/api/workouts/$WORKOUT/load" | jq -e '.ok == true' >/dev/null

LAN_IP=$(ip route get 1.1.1.1 | awk \
  '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')
test -n "$LAN_IP"
DEVICE_URL=http://$LAN_IP:44083
adb -s "$SERIAL" install -r kotlin/app/build/outputs/apk/debug/app-debug.apk \
  | tee "$EVIDENCE/install.txt"
adb -s "$SERIAL" shell am force-stop "$PACKAGE"
adb -s "$SERIAL" shell run-as "$PACKAGE" rm -f "$PREF"
adb -s "$SERIAL" shell input keyevent KEYCODE_WAKEUP
adb -s "$SERIAL" shell wm dismiss-keyguard
adb -s "$SERIAL" shell am start -n "$ACTIVITY" >/dev/null
sleep 3
adb -s "$SERIAL" shell input tap 670 445
adb -s "$SERIAL" shell input keyevent KEYCODE_MOVE_END
for _ in $(seq 1 80); do adb -s "$SERIAL" shell input keyevent KEYCODE_DEL; done
adb -s "$SERIAL" shell input text "$DEVICE_URL"
adb -s "$SERIAL" shell input tap 670 535
sleep 4
curl --fail --silent --show-error -X POST "$BASE_URL/api/program/start" \
  | jq -e '.running == true' >/dev/null
sleep 2
adb -s "$SERIAL" shell input tap 707 152
sleep 3
adb -s "$SERIAL" exec-out screencap -p >"$EVIDENCE/glass-lens.png"
test -s "$EVIDENCE/glass-lens.png"
```

- [ ] **Step 3: Execute and inspect the tablet capture**

```bash
chmod +x build/verification/2026-08-23-amber-lens/verify-lens.sh
bash build/verification/2026-08-23-amber-lens/verify-lens.sh
grep -q '^restore_failed=0$' \
  build/verification/2026-08-23-amber-lens/restoration.txt
```

Open `glass-lens.png`. Confirm the amber rim is distinct over both light and
dark cells, the dark separator and ivory top highlight remain visible, and lens
geometry/leader alignment is unchanged.

- [ ] **Step 4: Integrate and close the tracked task**

From the primary worktree `/home/ssilver/development/precor-9.3x`, merge and
verify explicitly:

```bash
git pull --rebase
git merge --no-ff feat/amber-minimap-lens -m "merge: strengthen minimap lens edge"
./kotlin/gradlew -p kotlin testDebugUnitTest --tests '*RidgelineViewportLensTest'
bd close precor-9_3x-odg --reason \
  "Amber minimap rim implemented, Android gates pass, and Galaxy capture approved."
bd dolt push
git add .beads/issues.jsonl .beads/interactions.jsonl .beads/last-touched
git commit -m "chore: close amber minimap lens task"
git pull --rebase
git push origin main
git status --short --branch
git rev-list --left-right --count origin/main...main
```

Expected: push succeeds, branch divergence is `0 0`, and only the pre-existing
untracked `static/` directory remains. This repository currently has no Dolt
remote, so `bd dolt push` may report that it skipped; the Git-tracked passive
export still records the close. The plan itself is committed before execution.
After the push, move only the known ignored evidence directory to the primary
worktree, require the feature worktree to be clean, and remove it normally while
preserving its branch:

```bash
mkdir -p /home/ssilver/development/precor-9.3x/build/verification
test ! -e /home/ssilver/development/precor-9.3x/build/verification/2026-08-23-amber-lens
mv -f \
  /home/ssilver/development/precor-9.3x/.worktrees/amber-minimap-lens/build/verification/2026-08-23-amber-lens \
  /home/ssilver/development/precor-9.3x/build/verification/2026-08-23-amber-lens
test -s /home/ssilver/development/precor-9.3x/build/verification/2026-08-23-amber-lens/glass-lens.png
test -z "$(git -C /home/ssilver/development/precor-9.3x/.worktrees/amber-minimap-lens status --porcelain)"
cd /home/ssilver/development/precor-9.3x
git worktree remove \
  /home/ssilver/development/precor-9.3x/.worktrees/amber-minimap-lens
```
