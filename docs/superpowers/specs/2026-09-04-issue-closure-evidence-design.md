# Issue Closure Evidence and Tablet Deployment Design

## Goal

Deploy the exact merged `main` Android build to the gym tablet, validate the fixes for GitHub issues 59, 60, 61, 62, and 64 without moving the treadmill, annotate those issues with concrete evidence, and establish the same evidence-first closure process for future work.

## Safety and isolation

- Build documentation changes in the ignored `.worktrees/closure-evidence` worktree so the user's existing `.beads` and `static/` changes remain untouched.
- Keep all ADB and GitHub writes in the primary session.
- Never start a real workout or send speed/incline/belt commands during validation.
- Use an isolated mock backend for running-screen and resume scenarios. Record the tablet's original server preference, microphone permission, and visible state, then restore them after validation.
- Treat screenshots as temporary local artifacts stored in an OS temporary directory outside every repository/worktree. The system `gh` 2.45.0 predates attachments, so download the pinned official `cli/cli` v2.100.0 `gh_2.100.0_linux_amd64.tar.gz` and `gh_2.100.0_checksums.txt` into that temporary directory, verify the tarball against the published checksum, and use that binary's `gh issue comment --attach` support. Verify the comment is on the intended issue, its body contains a `github.com/user-attachments/` URL, and the rendered image URL returns successfully before deleting the local file. No screenshot is staged, tracked, or committed.

## Exact-build deployment

First land the `CLAUDE.md` process change. Then fetch authoritative `origin/main`, pin its SHA in a clean detached worktree, and build the APK there. Record the Git SHA and local APK checksum. Before installation, preserve the prior installed APK and app preferences in an OS temporary directory for rollback. Install with the tablet's exact ADB serial without uninstalling or clearing application data, confirm package/version/path and a healthy resumed activity, pull the installed base APK, and require its checksum to match the pinned local artifact. Re-fetch `origin/main` before completion; if it advanced, rebuild and reinstall the new pinned head. On installation or launch failure, restore the preserved APK where feasible and stop rather than clearing device data. A successful run intentionally leaves only the new final-main APK installed.

## Validation evidence

Each issue receives one retrospective evidence comment containing:

1. The user-visible problem or requested behavior.
2. The diagnosed root cause or implementation gap.
3. For bugs, a focused regression run against the pre-fix parent that visibly fails for the intended reason.
4. The corresponding focused run against merged `main` that passes.
5. The merged commit and PR link.
6. A GitHub-hosted screenshot when the behavior is visual.

Validation by issue:

- **#59 (feature):** fix commit `890c4f8`, base `e186c68`. No RED run is required. From `kotlin/`, GREEN: `./gradlew testDebugUnitTest --tests com.precor.treadmill.ui.screens.running.RunningTimerTest`; broader gate: `./gradlew testDebugUnitTest assembleDebug`. Use the mock backend to show that the running timer defaults to countdown and can switch to count-up. Capture the running-screen result.
- **#60 (bug):** fix commit `872281b`, broken parent `e186c68`. In a disposable detached worktree at the broken parent, apply only the focused `WakeWordActivationPolicyTest` test source from the fix and, from `kotlin/`, run `./gradlew testDebugUnitTest --tests com.precor.treadmill.WakeWordActivationPolicyTest`; the intended RED is the concrete absence of the debounce/rearm policy (a compile failure, labeled as such). Run the identical test source/command on final `main` for GREEN, plus `./gradlew testDebugUnitTest assembleDebug` from `kotlin/`. Validate without granting the tablet microphone or synthesizing a real activation. No screenshot is required because the behavior is event/lifecycle based.
- **#61 (feature):** implementation through `b777f33`, integration fix `fa8a99d`, final squash on `main`. No RED run is required. From `kotlin/`, run the focused settings, voice-input, and wake-policy tests, plus `./gradlew testDebugUnitTest assembleDebug`. Show the persistent Voice Input switch and microphone permission state in Settings. Record the original switch value, change it to the opposite value, relaunch and confirm that value persisted, then restore and verify the exact original Voice Input preference and microphone permission. Capture Settings.
- **#62 (bug):** fix commit `f7cef1e`, broken parent `e186c68`. From each worktree root, apply only the changed `TestHistoryResume` regression source to a disposable detached worktree at the broken parent and run `pytest -q python/tests/test_server_integration.py::TestHistoryResume`; record the intended assertion failure(s) and exit status. Run the identical test source/command on final `main` for GREEN, then `pytest -q python/tests/test_server_integration.py`; also run the Android full gate from `kotlin/`. On the final-main tablet connected only to the mock backend, seed one resumable in-progress history record and one terminal record: verify the resumable item offers Resume with the correct remaining time and the terminal item does not offer Resume. Capture the post-fix History UI if both results are meaningfully visible; otherwise document why no useful screenshot exists.
- **#64 (feature):** implementation commit `0ec58d4` stacked on #59 and landed with #59. No RED run is required. From `kotlin/`, GREEN: `./gradlew testDebugUnitTest --tests com.precor.treadmill.ui.screens.running.NextChangeDisplayTest --tests com.precor.treadmill.ui.screens.running.RidgelineNextChangeClockSourceTest`, plus `./gradlew testDebugUnitTest assembleDebug`. Use the mock running program to show relative time until the next interval together with the stable absolute workout-clock mark. Capture the running screen, ideally sharing the #59 screenshot when both are legible.

The RED and GREEN runs use the same focused test source; the broken tree may receive only a disclosed test-only patch. Commands, source SHA, exit status, and relevant unedited output are recorded. If a pre-fix regression cannot execute because the test's required seam did not exist, the comment must say so explicitly and show the concrete compile/assertion failure rather than implying a behavioral failure. Evidence must not be fabricated or paraphrased as command output.

## Mock-backend interlock and restoration

Before force-stopping or retargeting the app, perform a read-only preflight against the currently configured real endpoint and visible tablet state. Require reported belt speed zero, no active real session/program, and an idle tablet UI; if any condition is nonzero, active, unavailable, or ambiguous, abort without sending an equipment command.

Run `python/server.py` with `TREADMILL_MOCK=1` on a worktree-specific port and verify its log states `Mock mode — no Pi connection`. The server then instantiates `MockTreadmillClient` instead of `TreadmillClient`, which is the hard transport interlock: no treadmill Unix socket, BLE, USB, or GPIO transport is opened. Before launching a running or resume screen, force-stop the app, back up its complete DataStore preferences through `run-as`, and temporarily remove only that preference file so Setup can target the mock host. Confirm the app is connected to the mock endpoint and the server process environment still contains `TREADMILL_MOCK=1` before invoking mock program APIs.

Record the original server preference, Voice Input preference, microphone permission flags, visible activity, and connectivity state. After screenshots, force-stop the app, restore the exact DataStore file and permission state, relaunch the original visible activity where practical, and verify the app is no longer connected to the mock server. Stop the mock server. Every visual issue comment must state that the backend was mocked and the belt remained stationary.

## Project process update

Add an "Issue Closure Evidence" section to `CLAUDE.md` requiring agents to:

- explain the problem and root cause before closure;
- for bugs, add a focused regression and record a RED run against the broken state, failing for the intended reason;
- record the GREEN run and relevant broader quality gates;
- attach before/after screenshots for visual bugs when both states can be reproduced safely, or a post-fix screenshot for visual feature requests;
- upload issue images as GitHub attachments rather than committing them unless the user explicitly requests repository-backed evidence;
- include commit/PR/device context and state any validation limitation honestly;
- close only after the evidence comment is present and the implementation is landed or otherwise delivered as agreed.

## Git and delivery

Commit only `CLAUDE.md` and the required design/plan documentation on `codex/closure-evidence`, push it, open and merge a PR, and then pin/build/deploy authoritative final `main`. Never stash, reset, overwrite, or relocate the user's existing main-worktree changes. Fast-forward local `main` only if Git can do so without touching those paths; otherwise leave it unchanged and report the blocker. Before committing, verify no image extension or screenshot artifact is staged or tracked. Issue annotations and image uploads are external evidence, not repository content. Close the tracking bead only after the tablet, GitHub issues, documentation PR, and final verification are complete.

## Failure handling

- If the tablet disconnects, retry discovery without using a different device implicitly.
- If the mock backend cannot be isolated from the real treadmill, skip the UI action and rely on automated evidence; do not widen hardware authority.
- If `gh issue comment --attach` is unsupported or upload verification fails, stop and report the blocker rather than placing images in the repository or on an unapproved host.
- If any regression or build fails unexpectedly, diagnose it before deployment or annotation.
