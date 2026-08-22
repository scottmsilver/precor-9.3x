# Sequential Worktree Landing Design

## Goal

Land the useful changes from four dirty worktrees without losing work where the
three Ridgeline patches overlap. Keep each behavior reviewable in its own commit,
verify it before adding the next layer, and finish with a real-device check on the
Galaxy Tab.

The source worktrees are:

- `skip-truncates-timeline`
- `ridgeline-sticky-labels`
- `hud-steepness`
- `minimap-glass-lens`

## Integration strategy

Integrate into a dedicated branch based on current `main`. Treat the dirty
worktrees as source material and leave them intact until their corresponding
commit has passed verification and reached the remote.

Land the changes in this order:

1. **Skip truncates timeline.** This backend and deployment change is independent
   of the Ridgeline UI. Port it to current `main`, resolving the changes made by
   the newer standalone-firmware commit without weakening either behavior.
2. **Sticky Ridgeline labels.** This is the largest `RidgelineMap.kt` change. Land
   its extracted geometry and deterministic chip-placement model first so later
   visual changes target the final structure.
3. **HUD steepness.** Port grade-driven switchback density, amplitude, smoothing,
   thread color, and glow onto the sticky-label geometry. Preserve both patches'
   tests and avoid duplicating route calculations.
4. **Minimap glass lens.** Apply the small, isolated viewport-lens treatment last,
   after the surrounding map drawing code has stabilized.

Each stage produces one logical commit. A failure at one stage is fixed before
the next patch is introduced, keeping regressions attributable to a single
change.

## Behavioral boundaries

The timeline patch makes the displayed workout clock remain real elapsed time
when an interval is skipped. It truncates the interval being left and shifts
later planned boundaries earlier. Persistence, server broadcasts, deployment
checks, and documentation must describe the same reshaped plan.

Sticky labels keep every visible interval-transition label present until its
boundary leaves the viewport. Collisions move a label and add a leader line;
they do not silently drop it. Placement remains deterministic and avoids the
position marker, metrics panel, canvas edges, and other labels.

Steepness remains visually monotonic: increasing grade tightens and narrows the
switchbacks independently of speed, while the route thread warms and glows at
higher grades without losing contrast. Local smoothing must not erase short,
steep intervals.

The minimap lens is a presentation-only overlay for the current viewport. It
must not change route geometry, camera position, touch behavior, or label
placement.

## Verification

After the timeline commit:

- Run focused program-engine, live-program, server-integration, and deployment
  tests changed by the patch.
- Run the relevant Python suite or repository sweep gate to catch interactions
  with current `main`.

After each Ridgeline commit:

- Run the focused Kotlin unit tests for route geometry, chip placement, label
  stability, and any minimap assertions introduced by the source patch.
- Run the Android unit-test gate covering the running screen.
- Build an installable debug APK before device verification.

For the final device check, discover the Galaxy Tab through `adb mdns services`,
connect to the advertised wireless-debugging endpoint, install the final APK,
and exercise a mixed-grade, multi-interval workout. Confirm that labels remain
visible through marker collisions and camera movement, steepness differences are
clear, the minimap lens tracks the viewport, and skip keeps the clock aligned
with the reshaped route. Capture screenshots or screen recording as evidence.

## Completion and cleanup

Merge the verified integration branch to `main`, push code and beads state, and
confirm `main` matches `origin/main`. Only then remove the four source worktrees.
Their branches remain until the landed commits are verified on the remote, so
the original work is recoverable throughout integration.
