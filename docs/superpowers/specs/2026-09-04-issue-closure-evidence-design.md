# Issue Closure Evidence and Tablet Deployment Design

## Goal

Deploy the exact merged `main` Android build to the gym tablet, validate the fixes for GitHub issues 59, 60, 61, 62, and 64 without moving the treadmill, annotate those issues with concrete evidence, and establish the same evidence-first closure process for future work.

## Safety and isolation

- Build documentation changes in the ignored `.worktrees/closure-evidence` worktree so the user's existing `.beads` and `static/` changes remain untouched.
- Keep all ADB and GitHub writes in the primary session.
- Never start a real workout or send speed/incline/belt commands during validation.
- Use an isolated mock backend for running-screen and resume scenarios. Record the tablet's original server preference, microphone permission, and visible state, then restore them after validation.
- Treat screenshots as temporary local artifacts. Upload them directly to the applicable GitHub issue with `gh issue comment --attach`, verify the resulting GitHub attachment URL, and remove the local temporary files. No screenshot is committed to the repository.

## Exact-build deployment

The APK must be built from the same commit as `origin/main`. Record both the Git SHA and APK checksum, install it with the tablet's exact ADB serial, launch the package, and confirm the installed package update time and a healthy resumed activity. Preserve microphone permission as denied unless a specific validation step requires otherwise; restore it afterward.

## Validation evidence

Each issue receives one retrospective evidence comment containing:

1. The user-visible problem or requested behavior.
2. The diagnosed root cause or implementation gap.
3. For bugs, a focused regression run against the pre-fix parent that visibly fails for the intended reason.
4. The corresponding focused run against merged `main` that passes.
5. The merged commit and PR link.
6. A GitHub-hosted screenshot when the behavior is visual.

Validation by issue:

- **#59:** Use the mock backend to show that the running timer defaults to countdown and can switch to count-up. Capture the running-screen result.
- **#60:** Reproduce the old duplicate/rearm behavior with the focused wake activation regression and show the passing policy tests on `main`. Validate without granting the tablet microphone or synthesizing a real activation. A screenshot is not required because the behavior is event/lifecycle based.
- **#61:** Show the persistent Voice Input switch and microphone permission state in Settings. Toggle off, relaunch, confirm persistence, then restore the enabled preference while leaving microphone permission denied. Capture Settings.
- **#62:** Reproduce incorrect resume/terminal handling against the pre-fix server parent and show the passing resume integration tests on `main`. Use mock data for any tablet-visible resume confirmation; never create a real treadmill session. Capture only if the mock scenario produces a meaningful resume UI.
- **#64:** Use the mock running program to show relative time until the next interval together with the stable absolute workout-clock mark. Capture the running screen, ideally sharing the #59 screenshot when both are legible.

If a pre-fix regression cannot execute because the test's required seam did not exist, the comment must say so explicitly and show the concrete compile/assertion failure rather than implying a behavioral failure. Evidence must not be fabricated or paraphrased as command output.

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

Commit only `CLAUDE.md` and the required design/plan documentation on `codex/closure-evidence`, push it, open and merge a PR, and update local `main` by fast-forward without touching existing user changes. Issue annotations and image uploads are external evidence, not repository content. Close the tracking bead only after the tablet, GitHub issues, documentation PR, and final verification are complete.

## Failure handling

- If the tablet disconnects, retry discovery without using a different device implicitly.
- If the mock backend cannot be isolated from the real treadmill, skip the UI action and rely on automated evidence; do not widen hardware authority.
- If `gh issue comment --attach` is unsupported or upload verification fails, stop and report the blocker rather than placing images in the repository or on an unapproved host.
- If any regression or build fails unexpectedly, diagnose it before deployment or annotation.

