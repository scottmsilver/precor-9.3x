# Dense Transition Bookends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Ridgeline transition-label work bounded for arbitrarily dense workout timelines by rendering stable first/last bookends and counts without changing the underlying route.

**Architecture:** Add binary boundary lookup and prefix-phase data to `RidgelineRoute`, then introduce a pure grouping module that selects exact labels through 64 visible boundaries and at most 32 bookend groups above that threshold. Integrate those identities into the existing quantized frame cache while reprojecting anchors exactly, and render measured inline count badges plus guard-clipped brackets between placed pills.

**Tech Stack:** Kotlin, Jetpack Compose Canvas/text measurement, JUnit 4, Gradle Android unit tests, physical Galaxy Tab verification over wireless ADB.

---

## File map

- Modify `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt`: binary route lookup, prefix phase, label model badge measurement, frame-cache integration, and final drawing.
- Create `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroups.kt`: pure boundary grouping, bounded count formatting, bracket geometry, and guard clipping.
- Modify `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineRouteTest.kt`: binary lookup and phase-equivalence coverage.
- Create `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroupsTest.kt`: boundary semantics, grouping coverage, work counters, badge formatting, and bracket geometry.
- Modify `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineLabelStabilityTest.kt`: production-pipeline cache, exact-anchor, traveled-cut, collision, and workload coverage.
- Write verification artifacts only under `build/verification/2026-08-23-dense-bookends/` (ignored build output; never commit them).

### Task 1: Make route lookup and projection indexed

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt:70-180`
- Test: `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineRouteTest.kt`

- [ ] **Step 1: Write failing boundary-index tests**

Add tests covering the label universe `startOf(0 until count)`, duplicates-free lower/upper bounds, route edges, and a 100,000-interval route:

```kotlin
@Test fun `boundary searches use inclusive and exclusive semantics`() {
    val r = RidgelineRoute(listOf(
        RouteInterval(0.0, 3.0, 0.25),
        RouteInterval(2.0, 3.0, 0.75),
        RouteInterval(4.0, 3.0, 1.0),
    ))
    assertEquals(0, r.firstBoundaryAtOrAfter(-1.0))
    assertEquals(0, r.firstBoundaryAtOrAfter(0.0))
    assertEquals(1, r.firstBoundaryAfter(0.0))
    assertEquals(1, r.firstBoundaryAtOrAfter(0.25))
    assertEquals(2, r.firstBoundaryAfter(0.25))
    assertEquals(3, r.firstBoundaryAtOrAfter(r.total))
    assertEquals(3, r.firstBoundaryAfter(r.total))
}

@Test fun `indexed phase matches reference integration`() {
    val intervals = (0 until 100_000).map {
        RouteInterval((it % 16).toDouble(), 2.5 + it % 7, 0.25 + it % 5)
    }
    val r = RidgelineRoute(intervals)
    listOf(0.0, 0.25, 123.456, r.total - 0.01, r.total).forEach { p ->
        assertEquals(referencePhase(intervals, p), r.phaseAt(p), 1e-8)
    }
}
```

- [ ] **Step 2: Run the new route tests and verify RED**

Run:

```bash
cd kotlin
./gradlew testDebugUnitTest --tests '*RidgelineRouteTest' --rerun-tasks
```

Expected: compilation fails because `firstBoundaryAtOrAfter` and `firstBoundaryAfter` do not exist.

- [ ] **Step 3: Implement binary searches and prefix phase**

In `RidgelineRoute`, add a cumulative phase array and shared lower/upper-bound helpers. Preserve the public `idxAt` boundary convention (`cum[i + 1] <= pos` advances):

```kotlin
private val phaseAtBoundary = DoubleArray(count + 1).also { prefix ->
    for (i in 0 until count) {
        prefix[i + 1] = prefix[i] +
            (cum[i + 1] - cum[i]) * (turnRate(iv[i].grade) + floorRate)
    }
}

internal fun firstBoundaryAtOrAfter(time: Double): Int = lowerBound(cum, count, time)
internal fun firstBoundaryAfter(time: Double): Int = upperBound(cum, count, time)

fun idxAt(pos: Double): Int =
    (upperBound(cum, count + 1, pos) - 1).coerceIn(0, count - 1)

fun phaseAt(pos: Double): Double {
    val p = pos.coerceIn(0.0, total)
    val i = idxAt(p)
    return phaseAtBoundary[i] +
        (p - cum[i]) * (turnRate(iv[i].grade) + floorRate)
}
```

Declare `floorRate` before initializing `phaseAtBoundary`. Implement `lowerBound`/`upperBound` as ordinary iterative binary search with an exclusive `limit`; never call `List.binarySearch` because the boundary convention must remain explicit.

- [ ] **Step 4: Run route and steepness tests and verify GREEN**

Run:

```bash
./gradlew testDebugUnitTest \
  --tests '*RidgelineRouteTest' \
  --tests '*RidgelineSteepnessTest' \
  --rerun-tasks
```

Expected: all selected tests pass, including exact historical phase anchors.

- [ ] **Step 5: Inspect and commit**

```bash
git diff --check
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt \
        kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineRouteTest.kt
git commit -m "perf(android): index Ridgeline route boundaries"
```

### Task 2: Add pure exact/bookend grouping

**Files:**
- Create: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroups.kt`
- Create: `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroupsTest.kt`

- [ ] **Step 1: Write failing grouping tests**

Use injectable production defaults (`exactLimit = 64`, `bucketCount = 32`) and smaller limits in edge tests. Assert:

```kotlin
@Test fun `64 boundaries stay exact and 65 aggregate`() {
    assertTrue(groups(routeWithStarts(64), 0.0, 600.0).all { it.count == 1 })
    val dense = groups(routeWithStarts(65), 0.0, 600.0)
    assertTrue(dense.size <= 32)
    assertTrue(dense.sumOf { it.count } == 65)
    assertTrue(dense.sumOf { if (it.count == 1) 1 else 2 } <= 64)
}

@Test fun `groups cover visible indices once at every edge`() {
    val grouped = groups(
        route = routeWithBoundaryKeys(0.0, 10.0, 20.0, 30.0, 40.0),
        qLo = 10.0,
        ew = 20.0,
        exactLimit = 2,
        bucketCount = 2,
    )
    assertEquals(listOf(1, 2, 3), grouped.flatMap { it.firstIndex until it.endExclusive })
}
```

Also cover: route start included; route end excluded; `qLo`/`qHi` included; internal bucket edge belongs to later bucket; all-singleton forced dense buckets; mixed empty/singleton/aggregate buckets; a million interior transitions; and disjoint contiguous index ranges.

- [ ] **Step 2: Run the grouping tests and verify RED**

Run:

```bash
./gradlew testDebugUnitTest --tests '*RidgelineTransitionGroupsTest' --rerun-tasks
```

Expected: compilation fails because the grouping module does not exist.

- [ ] **Step 3: Implement the pure grouping types and algorithm**

Create:

```kotlin
internal data class TransitionBoundaryGroup(
    val firstIndex: Int,
    val endExclusive: Int,
) {
    val lastIndex: Int get() = endExclusive - 1
    val count: Int get() = endExclusive - firstIndex
    val aggregate: Boolean get() = count > 1
}

internal fun collectTransitionBoundaryGroups(
    route: RidgelineRoute,
    qLo: Double,
    ew: Double,
    exactLimit: Int = 64,
    bucketCount: Int = 32,
): List<TransitionBoundaryGroup> {
    val qHi = min(qLo + ew, route.total)
    val visibleFirst = route.firstBoundaryAtOrAfter(qLo)
    val visibleEnd = route.firstBoundaryAfter(qHi)
    val visibleCount = visibleEnd - visibleFirst
    if (visibleCount <= exactLimit) {
        return (visibleFirst until visibleEnd).map { TransitionBoundaryGroup(it, it + 1) }
    }
    val out = ArrayList<TransitionBoundaryGroup>(bucketCount)
    var first = visibleFirst
    for (bucket in 0 until bucketCount) {
        val end = if (bucket == bucketCount - 1) visibleEnd else {
            val bucketEnd = qLo + (bucket + 1).toDouble() * ew / bucketCount
            route.firstBoundaryAtOrAfter(bucketEnd).coerceIn(first, visibleEnd)
        }
        if (end > first) out += TransitionBoundaryGroup(first, end)
        first = end
    }
    check(first == visibleEnd)
    return out
}
```

Validate finite positive `ew`, `exactLimit >= 2`, and `bucketCount >= 1`. Clamp `qLo` consistently with existing route geometry; do not enumerate interior indices in dense mode.

- [ ] **Step 4: Add bounded count formatting tests and implementation**

Test and implement a pure formatter used by the inline badge:

```kotlin
internal fun formatTransitionCount(count: Int): String = when {
    count < 1_000 -> "×$count"
    count < 1_000_000 -> "×%.1fk".format(Locale.US, count / 1_000.0)
    count < 1_000_000_000 -> "×%.1fM".format(Locale.US, count / 1_000_000.0)
    else -> "×%.1fB".format(Locale.US, count / 1_000_000_000.0)
}
```

Expected assertions include `×2`, `×999`, `×1.0k`, `×1.2M`, and `×2.1B`; require `count > 1` for badge callers.

- [ ] **Step 5: Run focused tests and commit**

```bash
./gradlew testDebugUnitTest \
  --tests '*RidgelineTransitionGroupsTest' \
  --tests '*RidgelineRouteTest'
git diff --check
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroups.kt \
        kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroupsTest.kt
git commit -m "feat(android): group dense Ridgeline transitions"
```

### Task 3: Integrate groups, badges, and cache semantics

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt:215-320,1370-1545`
- Modify: `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineLabelStabilityTest.kt`

- [ ] **Step 1: Write failing production-pipeline tests**

Extend the test model's measurement counter and production frame cache. Cover:

```kotlin
@Test fun `dense frame prepares and projects only bookends`() {
    val model = countedModel(routeWithStarts(10_000))
    val frame = cache.layout(model, denseGeometry, markerPos, centerX, mapW, marker, metrics, topY, botY)
    assertTrue(frame.groups.size <= 32)
    assertTrue(frame.visible.size <= 64)
    assertTrue(model.preparedCount <= 64)
    assertTrue(frame.stats.worldXCalls <= 64)
    assertEquals(10_000, frame.groups.sumOf { it.range.count })
}

@Test fun `exact anchors move while dense membership and slots stay cached`() {
    val first = layoutAt(camLo = 100.000)
    val second = layoutAt(camLo = 100.010)
    assertEquals(first.groupRanges, second.groupRanges)
    assertEquals(first.layout.slots, second.layout.slots)
    assertNotEquals(first.visible.map { it.candidate.anchorY }, second.visible.map { it.candidate.anchorY })
}
```

Add a marker-crossing case where the exact marker crosses a selected bookend but remains within the same 2-pixel camera cell and snapped marker rectangle. Assert traveled styling changes, `computations` increments exactly once, and priorities/slots are deterministic. Retain the existing 61-label exact-mode expectations.

- [ ] **Step 2: Run the production-pipeline tests and verify RED**

Run:

```bash
./gradlew testDebugUnitTest --tests '*RidgelineLabelStabilityTest' --rerun-tasks
```

Expected: dense-bound assertions fail because all visible labels are still collected.

- [ ] **Step 3: Add badge-aware projected/frame models**

Extend the model without altering ordinary labels:

```kotlin
internal data class PreparedTransitionBadge<T>(
    val text: String,
    val measured: MeasuredTransitionText<T>,
)

internal data class ProjectedTransitionLabel<T>(
    val label: PreparedTransitionLabel<T>,
    val candidate: ChipCandidate,
    val travelled: Boolean,
    val groupIndex: Int,
    val endpoint: BookendEndpoint,
    val badge: PreparedTransitionBadge<T>? = null,
)

internal enum class BookendEndpoint { SINGLE, FIRST, LAST }

internal data class TransitionLabelFrame<T>(
    val groups: List<TransitionBoundaryGroup>,
    val visible: List<ProjectedTransitionLabel<T>>,
    val prioritized: List<ProjectedTransitionLabel<T>>,
    val layout: ChipLayoutResult,
)
```

Expose a model-lifetime cached `badgeFor(count)` that measures amber count text once. For a last endpoint, set candidate width to `label.pillW + BADGE_GAP + badge.width + BADGE_END_PADDING`; singles and first endpoints retain the exact old width.

- [ ] **Step 4: Refactor the frame cache around quantized membership**

Compute `cameraPixel` and `packingCamLo` before collection. Call `collectTransitionBoundaryGroups(route, packingCamLo, ew)`, prepare/project only each group's first and (when different) last index, and build packing candidates with `packingGeometry`.

Cache keys must include: model identity, camera pixel, geometry/style dimensions, group ranges, traveled cut, snapped marker rect, metrics guard, fixed guards, and map bounds. Cache only group identities, prioritization, badges, and slot decisions. On every `layout` call, reproject the selected indices through exact `geometry` and return new candidates with the cached slots. Do not cache exact anchor coordinates.

For instrumentation, add internal counters or injected projector hooks used only by JVM tests; production defaults must add no per-frame logging or allocation beyond the bounded selected set.

- [ ] **Step 5: Run exact/dense cache tests and commit**

```bash
./gradlew testDebugUnitTest \
  --tests '*RidgelineLabelStabilityTest' \
  --tests '*RidgelineTransitionGroupsTest' \
  --tests '*RidgelineChipLayoutTest'
git diff --check
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt \
        kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineLabelStabilityTest.kt
git commit -m "perf(android): bound dense transition layout"
```

### Task 4: Draw inline badges and placed-pill brackets

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroups.kt`
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt:970-1040`
- Modify: `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroupsTest.kt`
- Modify: `kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineLabelStabilityTest.kt`

- [ ] **Step 1: Write failing bracket geometry and clipping tests**

Define pure geometry assertions for heavily displaced pills on opposite sides:

```kotlin
@Test fun `bracket arms touch placed pills and avoid guards`() {
    val first = Rect(40f, 300f, 150f, 324f)
    val last = Rect(500f, 80f, 640f, 104f)
    val marker = Rect(290f, 170f, 350f, 230f)
    val otherChip = Rect(300f, 110f, 430f, 134f)
    val segments = placedBookendBracket(first, last, centerX = 320f)
    assertTrue(segments.any { it.touchesBoundaryOf(first) })
    assertTrue(segments.any { it.touchesBoundaryOf(last) })
    val clipped = clipSegmentsOutside(segments, listOf(marker, otherChip))
    assertTrue(clipped.none { it.interiorIntersects(marker) || it.interiorIntersects(otherChip) })
}
```

Also test same-side pills, vertically reversed slot positions, canvas-edge clipping, zero-length fragments, overlapping guards, and preservation of own-pill boundary endpoints.

- [ ] **Step 2: Run bracket tests and verify RED**

```bash
./gradlew testDebugUnitTest --tests '*RidgelineTransitionGroupsTest' --rerun-tasks
```

Expected: compilation fails because bracket geometry helpers do not exist.

- [ ] **Step 3: Implement pure bracket segments and guard subtraction**

Add:

```kotlin
internal data class BookendSegment(val start: Offset, val end: Offset)

internal fun placedBookendBracket(first: Rect, last: Rect, centerX: Float): List<BookendSegment> {
    val firstY = first.center.y
    val lastY = last.center.y
    val spineX = centerX.coerceIn(
        min(first.right, last.right),
        max(first.left, last.left),
    )
    val firstEdge = nearestHorizontalBoundary(first, spineX)
    val lastEdge = nearestHorizontalBoundary(last, spineX)
    return listOf(
        BookendSegment(Offset(firstEdge, firstY), Offset(spineX, firstY)),
        BookendSegment(Offset(spineX, firstY), Offset(spineX, lastY)),
        BookendSegment(Offset(spineX, lastY), Offset(lastEdge, lastY)),
    ).filterNot { it.start == it.end }
}
```

Adjust the spine calculation if tests expose an invalid `coerceIn` range; the required invariant is a deterministic centerline-near spine and arms ending exactly on pill boundaries. Implement axis-aligned segment subtraction against guard rectangles as interval subtraction, then clip to canvas bounds. Do not use raster sampling.

- [ ] **Step 4: Draw badge and bracket in the specified order**

Build pill rectangles from `slot.pillLeft`, `slot.pillTop`, `candidate.pillW`, and `CHIP_H`. For aggregate groups, compute bracket segments from the two placed pill rectangles, subtract marker/metrics/other-pill guards, and draw remaining segments after the route but before ordinary leaders and chips at one pixel and the lower endpoint alpha.

Draw the badge inside the ending pill after speed text using its measured offset. Use `candidate.pillW` for pill chrome, collision bounds, and edge selection; ordinary labels remain byte-for-byte equivalent in size/content.

- [ ] **Step 5: Run focused Ridgeline tests and generate visual evidence**

```bash
./gradlew testDebugUnitTest \
  --tests '*RidgelineTransitionGroupsTest' \
  --tests '*RidgelineLabelStabilityTest' \
  --tests '*RidgelineChipLayoutTest' \
  --tests '*RidgelineRouteTest'
```

Expected: all selected tests pass. Extend the existing SVG/filmstrip fixture with one 65-transition threshold frame and one 1,000-transition dense frame; inspect that brackets touch both pills and badges remain readable.

- [ ] **Step 6: Commit**

```bash
git diff --check
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineMap.kt \
        kotlin/app/src/main/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroups.kt \
        kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineTransitionGroupsTest.kt \
        kotlin/app/src/test/java/com/precor/treadmill/ui/screens/running/RidgelineLabelStabilityTest.kt
git commit -m "feat(android): draw dense transition bookends"
```

### Task 5: Review, full verification, and device evidence

**Files:**
- Verify only: all files above
- Evidence: `build/verification/2026-08-23-dense-bookends/`

- [ ] **Step 1: Request spec-compliance review**

Have a fresh reviewer compare the implementation commits to
`docs/superpowers/specs/2026-08-23-dense-transition-bookends-design.md`. Resolve any Critical or Important mismatch with a focused test and commit before proceeding.

- [ ] **Step 2: Request code-quality review**

Have a second fresh reviewer inspect boundary math, asymptotic behavior, Compose cache lifetime, allocation, draw order, guard clipping, and regression risk. Resolve Critical or Important findings and re-review.

- [ ] **Step 3: Run fresh automated gates**

Run from the integration worktree:

```bash
cd kotlin
./gradlew testDebugUnitTest assembleDebug --rerun-tasks
cd ..
python3 -m pytest python/tests
bash deploy/tests/run_tests.sh
git diff --check
git status --short --branch
```

Expected: Android unit tests and APK build pass; Python and deploy suites pass with no new warnings; diff check is empty; worktree is clean after any generated ignored artifacts.

- [ ] **Step 4: Verify on the Galaxy Tab**

Use the existing isolated mock-backend harness and the Galaxy Tab device `SM-X115`. Install the fresh APK, then exercise:

- an ordinary mixed-grade route with fewer than 65 visible boundaries (no visual change);
- exactly 64 and exactly 65 visible boundaries (mode threshold);
- at least 1,000 visible boundaries (bounded bookends/counts);
- marker movement across a selected bookend;
- camera movement across a packing-grid boundary;
- displaced pills around marker and metrics guards.

Capture screenshots/video, `dumpsys gfxinfo ... framestats`, and SurfaceFlinger deltas under `build/verification/2026-08-23-dense-bookends/`. Confirm brackets remain attached to their pills, separate leaders track exact route anchors, no decoration paints over guards, and presentation cadence stays near 60 Hz.

- [ ] **Step 5: Restore the device and document evidence**

Restore the original server preference byte-for-byte, animation scales, timeout, app state, and mock backend. Record restoration commands/results and append concise evidence to bead `precor-9_3x-rbg`.

- [ ] **Step 6: Commit any final test-only correction and push the branch**

```bash
git pull --rebase
git push
git status --short --branch
```

Expected: `feat/land-ridgeline-timeline` is clean and up to date with its remote before the previously approved final merge/cleanup workflow resumes.
