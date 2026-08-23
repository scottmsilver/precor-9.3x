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

## Execution order

Tasks have a strict dependency chain: **1 → 2 → 3 → 4 → 5**. Dispatch one
fresh implementer at a time and complete review before starting the next task.
Tasks 1, 3, and 4 share `RidgelineMap.kt`; they must never run in parallel or
invoke concurrent Gradle builds in this worktree.

Every shell block below starts at the integration worktree root. Gradle commands
therefore use `./kotlin/gradlew -p kotlin`, and every Git path is root-relative.

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
./kotlin/gradlew -p kotlin testDebugUnitTest --tests '*RidgelineRouteTest' --rerun-tasks
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
./kotlin/gradlew -p kotlin testDebugUnitTest \
  --tests '*RidgelineRouteTest' --rerun-tasks
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
    assertTrue(collectTransitionBoundaryGroups(routeWithStarts(64), 0.0, 600.0)
        .all { it.count == 1 })
    val dense = collectTransitionBoundaryGroups(routeWithStarts(65), 0.0, 600.0)
    assertTrue(dense.size <= 32)
    assertTrue(dense.sumOf { it.count } == 65)
    assertTrue(dense.sumOf { if (it.count == 1) 1 else 2 } <= 64)
}

@Test fun `groups cover visible indices once at every edge`() {
    val grouped = collectTransitionBoundaryGroups(
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
./kotlin/gradlew -p kotlin testDebugUnitTest \
  --tests '*RidgelineTransitionGroupsTest' --rerun-tasks
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

internal data class TransitionGroupingStats(var boundarySearches: Int = 0)

internal fun collectTransitionBoundaryGroups(
    route: RidgelineRoute,
    qLo: Double,
    ew: Double,
    exactLimit: Int = 64,
    bucketCount: Int = 32,
    stats: TransitionGroupingStats? = null,
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

Increment `stats.boundarySearches` immediately before each route boundary
lookup. Tests assert a constant upper bound (`<= bucketCount + 2`) for a route
with a million interior transitions.

Validate finite positive `ew`, `exactLimit >= 2`, and `bucketCount >= 1`.
`qLo` is the frame cache's already-quantized `packingCamLo` and must be used
unchanged; the caller guarantees `qLo >= 0`. Only `qHi` is clipped to
`route.total`. Add a test that a non-grid `qLo` is not independently rounded or
clamped, and do not enumerate interior indices in dense mode.

- [ ] **Step 4: Write count-formatting tests and verify RED**

Add assertions for `×2`, `×999`, `×1.0k`, `×1.2M`, `×2.1B`, and the promotion
edges 999,949/999,950 and 999,949,999/999,950,000. Run:

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest \
  --tests '*RidgelineTransitionGroupsTest' --rerun-tasks
```

Expected: compilation fails because `formatTransitionCount` does not exist.

- [ ] **Step 5: Implement bounded count formatting and verify GREEN**

Implement a pure formatter used by the inline badge. Promote after rounding so
it can never emit `×1000.0k` or `×1000.0M`:

```kotlin
internal fun formatTransitionCount(count: Int): String = when {
    count < 1_000 -> "×$count"
    count < 999_950 -> "×%.1fk".format(Locale.US, count / 1_000.0)
    count < 999_950_000 -> "×%.1fM".format(Locale.US, count / 1_000_000.0)
    else -> "×%.1fB".format(Locale.US, count / 1_000_000_000.0)
}
```

Add `require(count > 1)` before the `when`. Then rerun the focused test and expect
all formatter and grouping cases to pass.

- [ ] **Step 6: Run focused tests and commit**

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest \
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
    assertTrue(model.preparationCount <= 64)
    assertTrue(cache.stats.exactProjectionCalls <= 64)
    assertTrue(cache.stats.packingProjectionCalls <= 64)
    assertEquals(10_000, frame.groups.sumOf { it.count })
}

@Test fun `exact anchors move while dense membership and slots stay cached`() {
    val first = layoutAt(camLo = 100.000)
    val second = layoutAt(camLo = 100.010)
    assertEquals(
        first.groups.map { it.firstIndex until it.endExclusive },
        second.groups.map { it.firstIndex until it.endExclusive },
    )
    assertEquals(first.layout.slots, second.layout.slots)
    assertNotEquals(first.visible.map { it.candidate.anchorY }, second.visible.map { it.candidate.anchorY })
}
```

Define explicit cumulative test instrumentation:

```kotlin
internal data class TransitionFrameCacheStats(
    val membershipComputations: Int,
    val packingProjectionCalls: Int,
    val exactProjectionCalls: Int,
    val placementComputations: Int,
)
```

`TransitionLabelModel.preparationCount` increments only when a new endpoint
identity is prepared. `TransitionGroupingStats.boundarySearches` is an optional
counter passed to the pure helper. `TransitionLabelFrameCache.stats` exposes a
snapshot of its cumulative counters for JVM tests; production does not log.

Add legacy-reference comparisons at 1 and exactly 64 visible boundaries for
keys, content, traveled state, effective pill widths, priority, and slots. Add
an explicit 65-boundary production-cache switch test. Add a marker-crossing
case where the exact marker crosses a selected bookend but remains within the
same 2-pixel camera cell and snapped marker rectangle. Assert traveled styling
changes and placement invalidates exactly once. Test unchanged snapped marker,
metrics, and fixed guards. Retain the existing 61-label expectations.

- [ ] **Step 2: Run the production-pipeline tests and verify RED**

Run:

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest \
  --tests '*RidgelineLabelStabilityTest' --rerun-tasks
```

Expected: dense-bound assertions fail because all visible labels are still collected.

- [ ] **Step 3: Write badge sizing/cache tests and verify RED**

Before changing models, add failing tests for a narrow `maxPillWidth`, an ending
label whose ordinary grade/speed already consumes the width, and an
`Int.MAX_VALUE` group count. Assert one final `effectivePillW <= maxPillWidth`,
identical placement/chrome/guard widths, a badge offset after the speed run, and
no text-run overlap. Run the focused stability test and expect missing endpoint
preparation APIs.

- [ ] **Step 4: Add badge-aware prepared/projected/frame models**

Extend the model without altering ordinary labels:

```kotlin
internal data class PreparedTransitionBadge<T>(
    val text: String,
    val measured: MeasuredTransitionText<T>,
    val color: Color,
)

internal data class PreparedTransitionEndpoint<T>(
    val label: PreparedTransitionLabel<T>,
    val badge: PreparedTransitionBadge<T>?,
    val gradeOffset: Float,
    val speedOffset: Float,
    val badgeOffset: Float?,
    val effectivePillW: Float,
)

internal data class ProjectedTransitionLabel<T>(
    val endpointContent: PreparedTransitionEndpoint<T>,
    val candidate: ChipCandidate,
    val travelled: Boolean,
    val groupIndex: Int,
    val endpoint: BookendEndpoint,
)

internal enum class BookendEndpoint { SINGLE, FIRST, LAST }

internal data class TransitionLabelFrame<T>(
    val groups: List<TransitionBoundaryGroup>,
    val visible: List<ProjectedTransitionLabel<T>>,
    val prioritized: List<ProjectedTransitionLabel<T>>,
    val layout: ChipLayoutResult,
)
```

Expose model-lifetime cached `endpointAt(index, aggregateCount?)`. Normal and
first endpoints delegate to the existing prepared label unchanged. An ending
endpoint measures the badge first, constraining it to the pill's available
content width when even the badge cannot fit naturally, then reserves
`badge.width + BADGE_GAP + trailingPadding` inside `maxPillWidth`, and reflows
grade/speed runs proportionally into the remaining width using the existing
constrained-measure path. Set
`badgeOffset = speedOffset + speed.width + BADGE_GAP`; compute one
`effectivePillW <= maxPillWidth`. Use that exact width for the candidate,
placement, chrome, leaders, brackets, canvas checks, and all guards.
Use the Ridgeline amber transition accent for `PreparedTransitionBadge.color`;
include that color in the endpoint-preparation cache key/model lifetime. Badge
sizing tests must also assert the prepared amber color.

- [ ] **Step 5: Refactor the frame cache into membership and packing phases**

Compute `cameraPixel` and `packingCamLo` before collection.

- Membership cache key: model identity, camera pixel/`packingCamLo`, and `ew`.
  Its value is group ranges plus prepared first/last endpoint identities and
  badges. A subpixel frame with the same key performs no grouping, boundary
  searches, or preparation.
- Packing cache key: membership identity plus traveled cut, snapped marker,
  metrics and fixed guards, geometry/style dimensions, center/map bounds. Its
  value is priority and slot decisions. A subpixel frame with unchanged keys
  performs no packing projection or placement.
- Every call reprojects only selected exact anchors through current `geometry`;
  `exactProjectionCalls` therefore increases by the bounded selected count.

Update `layoutTransitionLabelFrame`, which is used directly by existing tests:
either route it through the same grouping/cache pipeline or construct explicit
singleton `TransitionBoundaryGroup`s for its supplied exact visible list and
populate the new `TransitionLabelFrame.groups` constructor argument. Do not
leave its constructor/callers broken.

Add counter assertions proving subpixel frames do not rerun membership,
preparation, packing projection, or placement, while exact projection does run;
camera-cell and traveled-cut crossings invalidate their respective phase once.

- [ ] **Step 6: Run exact/dense cache tests and commit**

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest \
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
    val segments = placedBookendBracket(first, last, centerX = 320f, strokeWidth = 1f)
    assertTrue(segments.any { it.touchesBoundaryOf(first) })
    assertTrue(segments.any { it.touchesBoundaryOf(last) })
    val clipped = clipSegmentsOutside(
        segments = segments,
        guards = listOf(marker, otherChip),
        canvas = Rect(0f, 0f, 700f, 400f),
        strokeWidth = 1f,
    )
    assertTrue(clipped.none { it.interiorIntersects(marker) || it.interiorIntersects(otherChip) })
}
```

Also test same-side pills, overlapping horizontal pill ranges, sub-pixel gaps,
vertically reversed slot positions, canvas-edge clipping, zero-length fragments,
overlapping guards, corners/tangency, rounded-pill bounding rectangles, and
preservation of own-pill boundary endpoints. All assertions operate on the
painted stroke envelope, not only its centerline.

- [ ] **Step 2: Run bracket tests and verify RED**

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest \
  --tests '*RidgelineTransitionGroupsTest' --rerun-tasks
```

Expected: compilation fails because bracket geometry helpers do not exist.

- [ ] **Step 3: Implement pure bracket segments and guard subtraction**

Add:

```kotlin
internal data class BookendSegment(val start: Offset, val end: Offset)

private fun outsideUnionX(first: Rect, last: Rect, centerX: Float, halfStroke: Float): Float {
    val left = min(first.left, last.left) - halfStroke
    val right = max(first.right, last.right) + halfStroke
    return if (abs(centerX - left) <= abs(centerX - right)) left else right
}

private fun bracketSpineX(first: Rect, last: Rect, centerX: Float, halfStroke: Float): Float {
    val firstIsLeft = first.left <= last.left
    val leftRect = if (firstIsLeft) first else last
    val rightRect = if (firstIsLeft) last else first
    val gapLo = leftRect.right + halfStroke
    val gapHi = rightRect.left - halfStroke
    return if (gapLo <= gapHi) centerX.coerceIn(gapLo, gapHi)
    else outsideUnionX(first, last, centerX, halfStroke)
}

internal fun placedBookendBracket(
    first: Rect,
    last: Rect,
    centerX: Float,
    strokeWidth: Float,
): List<BookendSegment> {
    val firstY = first.center.y
    val lastY = last.center.y
    val spineX = bracketSpineX(first, last, centerX, strokeWidth / 2f)
    val firstEdge = nearestHorizontalBoundary(first, spineX)
    val lastEdge = nearestHorizontalBoundary(last, spineX)
    return listOf(
        BookendSegment(Offset(firstEdge, firstY), Offset(spineX, firstY)),
        BookendSegment(Offset(spineX, firstY), Offset(spineX, lastY)),
        BookendSegment(Offset(spineX, lastY), Offset(lastEdge, lastY)),
    ).filterNot { it.start == it.end }
}
```

For disjoint horizontal ranges, this chooses the centerline-nearest point in the
stroke-inset gap. For overlapping or too-narrow ranges, it chooses the
centerline-nearest side strictly outside the pills' union, breaking ties to the
left. `nearestHorizontalBoundary` must select `left` when the spine is left of a
pill and `right` when it is right; the construction guarantees it is never
inside either pill.

Implement axis-aligned segment subtraction as interval subtraction. Inflate
marker, metrics, fixed guards, and every *other* pill rectangle by half the
bracket stroke before subtraction. Clip against the canvas rectangle inset by
the same half-stroke. Do not add the bracket's own first/last pills to the guard
list: construction keeps the stroke outside their interiors and arms terminate
at their boundaries. Remove zero-length fragments and do not use raster
sampling.

- [ ] **Step 4: Write pure render-geometry assertions and verify RED**

Before Canvas code, expose a pure `BookendDecoration` result containing the
effective first/last pill rectangles, badge baseline/offset, unclipped bracket,
and clipped drawable segments. Add failing production-level assertions that:

- z-order data places route → bracket → leaders/chips → marker/metrics;
- badge and one-pixel bracket both use the specified amber transition accent;
- the effective pill rectangles use exactly the placement width;
- badge text lies inside the ending pill and after speed text;
- marker, metrics, fixed guards, other chips, and canvas exclude the full
  painted stroke;
- a straddling group's endpoints keep independent traveled styling and its
  bracket alpha is the lower endpoint alpha.

Run the two focused classes and expect missing render-geometry APIs.

- [ ] **Step 5: Draw badge and bracket in the specified order**

Build pill rectangles from `slot.pillLeft`, `slot.pillTop`,
`endpointContent.effectivePillW`, and `CHIP_H`. Build and test the pure
`BookendDecoration`, then draw its clipped segments after the route but before
ordinary leaders/chips and before the marker/metrics at one pixel and the lower
endpoint alpha, using the same amber transition accent as the badge.

Draw the badge inside the ending pill at the prepared `badgeOffset`. Use
`effectivePillW` identically for placement, pill chrome, collision bounds,
leader edge selection, bracket attachment, canvas exclusion, and guards.
Ordinary labels retain their legacy prepared runs, widths, and draw data.

- [ ] **Step 6: Run focused Ridgeline tests and generate visual evidence**

```bash
./kotlin/gradlew -p kotlin testDebugUnitTest \
  --tests '*RidgelineTransitionGroupsTest' \
  --tests '*RidgelineLabelStabilityTest' \
  --tests '*RidgelineChipLayoutTest' \
  --tests '*RidgelineRouteTest'
```

Expected: all selected tests pass. Extend the existing SVG/filmstrip fixture
that writes `kotlin/build/ridgeline-labels.svg` with one 65-transition threshold
frame and one 1,000-transition dense frame. Copy and inspect it with:

```bash
mkdir -p build/verification/2026-08-23-dense-bookends
cp -f kotlin/build/ridgeline-labels.svg \
  build/verification/2026-08-23-dense-bookends/ridgeline-labels.svg
test -s build/verification/2026-08-23-dense-bookends/ridgeline-labels.svg
```

Confirm brackets touch both pills and badges remain readable.

- [ ] **Step 7: Commit**

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
./kotlin/gradlew -p kotlin testDebugUnitTest assembleDebug --rerun-tasks
python3 -m pytest python/tests
bash deploy/tests/run_tests.sh
git diff --check
git status --short --branch
```

Expected: Android unit tests and APK build pass; Python and deploy suites pass with no new warnings; diff check is empty; worktree is clean after any generated ignored artifacts.

- [ ] **Step 4: Verify on the Galaxy Tab**

Using `apply_patch`, create the ignored file
`build/verification/2026-08-23-dense-bookends/verify-device.sh` with the script
below, then run `chmod +x` and execute it from the worktree root. Keeping setup,
captures, and cleanup in one process preserves variables and guarantees the
`EXIT` trap runs after any failure.

```bash
#!/usr/bin/env bash
set -euo pipefail

BOOKEND_EVIDENCE=build/verification/2026-08-23-dense-bookends
BOOKEND_PACKAGE=com.precor.treadmill
BOOKEND_ACTIVITY=com.precor.treadmill/.MainActivity
BOOKEND_URL=http://127.0.0.1:44084
BOOKEND_TMP=$(mktemp -d)
BOOKEND_SERVER_PID=
BOOKEND_SERIAL=
BOOKEND_PREF_PRESENT=0
BOOKEND_WAS_RUNNING=0
BOOKEND_WINDOW_SCALE=
BOOKEND_TRANSITION_SCALE=
BOOKEND_ANIMATOR_SCALE=
BOOKEND_TIMEOUT=

mkdir -p "$BOOKEND_EVIDENCE"
echo "$BOOKEND_TMP" >"$BOOKEND_EVIDENCE/tmp-path.txt"

restore_setting() {
  local namespace=$1 key=$2 value=$3
  if [[ "$value" == null || "$value" == empty ]]; then
    adb -s "$BOOKEND_SERIAL" shell settings delete "$namespace" "$key" >/dev/null
  else
    adb -s "$BOOKEND_SERIAL" shell settings put "$namespace" "$key" "$value"
  fi
}

cleanup_bookend_device() {
  local exit_code=$?
  set +e
  {
    if [[ -n "$BOOKEND_SERIAL" ]]; then
      adb -s "$BOOKEND_SERIAL" shell am force-stop "$BOOKEND_PACKAGE"
      if [[ "$BOOKEND_PREF_PRESENT" == 1 ]]; then
        adb -s "$BOOKEND_SERIAL" shell run-as "$BOOKEND_PACKAGE" sh -c \
          'cat > files/datastore/server_prefs.preferences_pb' \
          <"$BOOKEND_EVIDENCE/server-prefs-before.pb"
        adb -s "$BOOKEND_SERIAL" exec-out run-as "$BOOKEND_PACKAGE" \
          cat files/datastore/server_prefs.preferences_pb \
          >"$BOOKEND_EVIDENCE/server-prefs-restored.pb"
        cmp "$BOOKEND_EVIDENCE/server-prefs-before.pb" \
          "$BOOKEND_EVIDENCE/server-prefs-restored.pb"
      else
        adb -s "$BOOKEND_SERIAL" shell run-as "$BOOKEND_PACKAGE" \
          rm -f files/datastore/server_prefs.preferences_pb
      fi
      [[ -n "$BOOKEND_WINDOW_SCALE" ]] && \
        restore_setting global window_animation_scale "$BOOKEND_WINDOW_SCALE"
      [[ -n "$BOOKEND_TRANSITION_SCALE" ]] && \
        restore_setting global transition_animation_scale "$BOOKEND_TRANSITION_SCALE"
      [[ -n "$BOOKEND_ANIMATOR_SCALE" ]] && \
        restore_setting global animator_duration_scale "$BOOKEND_ANIMATOR_SCALE"
      [[ -n "$BOOKEND_TIMEOUT" ]] && \
        restore_setting system screen_off_timeout "$BOOKEND_TIMEOUT"
      if [[ "$BOOKEND_WAS_RUNNING" == 1 ]]; then
        adb -s "$BOOKEND_SERIAL" shell am start -n "$BOOKEND_ACTIVITY" >/dev/null
      fi
    fi
    if [[ -n "$BOOKEND_SERVER_PID" ]]; then
      kill "$BOOKEND_SERVER_PID" 2>/dev/null
      wait "$BOOKEND_SERVER_PID" 2>/dev/null
    fi
    if [[ -n "$BOOKEND_TMP" && -d "$BOOKEND_TMP" && "$BOOKEND_TMP" == /tmp/* ]]; then
      rm -rf -- "$BOOKEND_TMP"
    else
      echo "refusing unexpected temp cleanup: $BOOKEND_TMP"
    fi
    printf 'exit_code=%s\n' "$exit_code"
    adb devices -l
  } >"$BOOKEND_EVIDENCE/restoration.log" 2>&1
  return "$exit_code"
}
trap cleanup_bookend_device EXIT
trap 'exit 130' INT TERM

env TREADMILL_MOCK=1 TREADMILL_DB="$BOOKEND_TMP/verification.db" \
  TREADMILL_SERVER_PORT=44084 python3 python/server.py \
  >"$BOOKEND_EVIDENCE/server.log" 2>&1 &
BOOKEND_SERVER_PID=$!
echo "$BOOKEND_SERVER_PID" >"$BOOKEND_EVIDENCE/server.pid"
for _ in $(seq 1 50); do
  curl --fail --silent "$BOOKEND_URL/" >"$BOOKEND_EVIDENCE/backend-ready.json" && break
  sleep 0.2
done
curl --fail --silent "$BOOKEND_URL/" >/dev/null

adb mdns services | tee "$BOOKEND_EVIDENCE/adb-mdns.log"
BOOKEND_ENDPOINT=$(awk '$1 ~ /^adb-R9ZY90P5LZP/ && $2 == "_adb-tls-connect._tcp" {print $3; exit}' \
  "$BOOKEND_EVIDENCE/adb-mdns.log")
if ! adb devices -l | grep -q 'model:SM_X115'; then
  test -n "$BOOKEND_ENDPOINT"
  adb connect "$BOOKEND_ENDPOINT" | tee "$BOOKEND_EVIDENCE/adb-connect.log"
fi
BOOKEND_SERIAL=$(adb devices -l | awk '/model:SM_X115/ {print $1; exit}')
test -n "$BOOKEND_SERIAL"
echo "$BOOKEND_SERIAL" >"$BOOKEND_EVIDENCE/adb-serial.txt"

BOOKEND_LAN_IP=$(ip route get 1.1.1.1 | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}')
test -n "$BOOKEND_LAN_IP"
BOOKEND_DEVICE_URL=http://$BOOKEND_LAN_IP:44084
echo "$BOOKEND_DEVICE_URL" >"$BOOKEND_EVIDENCE/backend-url.txt"

BOOKEND_WINDOW_SCALE=$(adb -s "$BOOKEND_SERIAL" shell settings get global window_animation_scale | tr -d '\r')
BOOKEND_TRANSITION_SCALE=$(adb -s "$BOOKEND_SERIAL" shell settings get global transition_animation_scale | tr -d '\r')
BOOKEND_ANIMATOR_SCALE=$(adb -s "$BOOKEND_SERIAL" shell settings get global animator_duration_scale | tr -d '\r')
BOOKEND_TIMEOUT=$(adb -s "$BOOKEND_SERIAL" shell settings get system screen_off_timeout | tr -d '\r')
adb -s "$BOOKEND_SERIAL" shell pidof "$BOOKEND_PACKAGE" >/dev/null && BOOKEND_WAS_RUNNING=1 || true
printf '%s\n%s\n%s\n%s\n' "$BOOKEND_WINDOW_SCALE" "$BOOKEND_TRANSITION_SCALE" \
  "$BOOKEND_ANIMATOR_SCALE" "$BOOKEND_TIMEOUT" >"$BOOKEND_EVIDENCE/device-settings-before.txt"

adb -s "$BOOKEND_SERIAL" shell am force-stop "$BOOKEND_PACKAGE"
if adb -s "$BOOKEND_SERIAL" shell run-as "$BOOKEND_PACKAGE" \
  test -f files/datastore/server_prefs.preferences_pb; then
  BOOKEND_PREF_PRESENT=1
  adb -s "$BOOKEND_SERIAL" exec-out run-as "$BOOKEND_PACKAGE" \
    cat files/datastore/server_prefs.preferences_pb \
    >"$BOOKEND_EVIDENCE/server-prefs-before.pb"
fi

adb -s "$BOOKEND_SERIAL" install -r kotlin/app/build/outputs/apk/debug/app-debug.apk \
  | tee "$BOOKEND_EVIDENCE/adb-install.log"
adb -s "$BOOKEND_SERIAL" shell run-as "$BOOKEND_PACKAGE" \
  rm -f files/datastore/server_prefs.preferences_pb
adb -s "$BOOKEND_SERIAL" shell settings put global window_animation_scale 1
adb -s "$BOOKEND_SERIAL" shell settings put global transition_animation_scale 1
adb -s "$BOOKEND_SERIAL" shell settings put global animator_duration_scale 1
adb -s "$BOOKEND_SERIAL" shell settings put system screen_off_timeout 180000
adb -s "$BOOKEND_SERIAL" shell am start -n "$BOOKEND_ACTIVITY"
sleep 5
adb -s "$BOOKEND_SERIAL" exec-out screencap -p >"$BOOKEND_EVIDENCE/setup-before.png"
adb -s "$BOOKEND_SERIAL" shell uiautomator dump /sdcard/bookend-setup-before.xml >/dev/null
adb -s "$BOOKEND_SERIAL" pull /sdcard/bookend-setup-before.xml \
  "$BOOKEND_EVIDENCE/setup-before.xml" >/dev/null
grep -q 'Server URL' "$BOOKEND_EVIDENCE/setup-before.xml"
adb -s "$BOOKEND_SERIAL" shell input tap 670 445
adb -s "$BOOKEND_SERIAL" shell input keyevent KEYCODE_MOVE_END
for _ in $(seq 1 80); do adb -s "$BOOKEND_SERIAL" shell input keyevent KEYCODE_DEL; done
adb -s "$BOOKEND_SERIAL" shell input text "$BOOKEND_DEVICE_URL"
adb -s "$BOOKEND_SERIAL" shell input tap 670 535
sleep 4
adb -s "$BOOKEND_SERIAL" exec-out screencap -p >"$BOOKEND_EVIDENCE/setup-after.png"
adb -s "$BOOKEND_SERIAL" shell uiautomator dump /sdcard/bookend-setup-after.xml >/dev/null
adb -s "$BOOKEND_SERIAL" pull /sdcard/bookend-setup-after.xml \
  "$BOOKEND_EVIDENCE/setup-after.xml" >/dev/null
! grep -q 'Server URL' "$BOOKEND_EVIDENCE/setup-after.xml"

curl --fail --silent --show-error -X POST "$BOOKEND_URL/api/profiles" \
  -H 'Content-Type: application/json' -d '{"name":"Bookend Verify"}' \
  | tee "$BOOKEND_EVIDENCE/profile-create.json"
BOOKEND_PROFILE=$(jq -r '.profile.id' "$BOOKEND_EVIDENCE/profile-create.json")
curl --fail --silent --show-error -X POST "$BOOKEND_URL/api/profile/select" \
  -H 'Content-Type: application/json' -d "{\"id\":\"$BOOKEND_PROFILE\"}" \
  | tee "$BOOKEND_EVIDENCE/profile-select.json"

run_fixture() {
  local slug=$1 count=$2 duration=$3
  curl --fail --silent --show-error -X POST "$BOOKEND_URL/api/program/stop" \
    >"$BOOKEND_EVIDENCE/$slug-stop.json"
  jq -n --arg name "$slug" --argjson count "$count" --argjson duration "$duration" \
    '{program:{name:$name,intervals:[range(0;$count) |
      {name:("I" + tostring),duration:$duration,
       speed:(2.5 + (. % 8) * 0.2),incline:(. % 16)}]},source:"manual"}' \
    >"$BOOKEND_EVIDENCE/$slug-request.json"
  curl --fail --silent --show-error -X POST "$BOOKEND_URL/api/workouts" \
    -H 'Content-Type: application/json' --data-binary @"$BOOKEND_EVIDENCE/$slug-request.json" \
    | tee "$BOOKEND_EVIDENCE/$slug-create.json"
  local workout
  workout=$(jq -r '.workout.id' "$BOOKEND_EVIDENCE/$slug-create.json")
  curl --fail --silent --show-error -X POST "$BOOKEND_URL/api/workouts/$workout/load" \
    | tee "$BOOKEND_EVIDENCE/$slug-load.json"
  curl --fail --silent --show-error -X POST "$BOOKEND_URL/api/program/start" \
    | tee "$BOOKEND_EVIDENCE/$slug-start.json"
  sleep 3
  adb -s "$BOOKEND_SERIAL" exec-out screencap -p >"$BOOKEND_EVIDENCE/$slug.png"
  adb -s "$BOOKEND_SERIAL" shell uiautomator dump "/sdcard/$slug.xml" >/dev/null
  adb -s "$BOOKEND_SERIAL" pull "/sdcard/$slug.xml" "$BOOKEND_EVIDENCE/$slug.xml" >/dev/null
}

run_fixture ordinary-8 8 75
run_fixture exact-64 64 9.5
run_fixture dense-65 65 9.375
run_fixture dense-1000 1000 0.6

adb -s "$BOOKEND_SERIAL" shell dumpsys gfxinfo "$BOOKEND_PACKAGE" reset \
  >"$BOOKEND_EVIDENCE/gfx-reset.txt"
adb -s "$BOOKEND_SERIAL" shell dumpsys SurfaceFlinger \
  >"$BOOKEND_EVIDENCE/surfaceflinger-before.txt"
adb -s "$BOOKEND_SERIAL" shell screenrecord --time-limit 20 /sdcard/bookend-motion.mp4
adb -s "$BOOKEND_SERIAL" pull /sdcard/bookend-motion.mp4 \
  "$BOOKEND_EVIDENCE/bookend-motion.mp4" >/dev/null
adb -s "$BOOKEND_SERIAL" shell dumpsys gfxinfo "$BOOKEND_PACKAGE" framestats \
  >"$BOOKEND_EVIDENCE/gfxinfo-framestats.txt"
adb -s "$BOOKEND_SERIAL" shell dumpsys SurfaceFlinger \
  >"$BOOKEND_EVIDENCE/surfaceflinger-after.txt"
```

Execute the created script without relying on the current shell's variables:

```bash
chmod +x build/verification/2026-08-23-dense-bookends/verify-device.sh
bash build/verification/2026-08-23-dense-bookends/verify-device.sh \
  >build/verification/2026-08-23-dense-bookends/device-run.log 2>&1
```

The dense video naturally crosses many 0.6-second represented boundaries and
exercises traveled-cut invalidation plus displacement around the marker/metrics
guards. Camera-grid movement remains covered by deterministic unit tests; the
app has no public fast-forward and the separate bead `precor-9_3x-pix` tracks a
long-running physical camera-pan capture.

Inspect all four screenshots and the dense video. Confirm the 8- and 64-label
routes retain ordinary labels, 65 switches to bookends, 1,000 remains bounded,
brackets stay attached to placed pills, independent leaders track route anchors,
no decoration paints over guards, and recorded presentation cadence remains
near 60 Hz.

- [ ] **Step 5: Restore the device and document evidence**

The script's `EXIT` trap restores the preference through stdin to `run-as`,
restores all animation scales and timeout, returns the app to its original
running/stopped state, stops the exact server PID, validates/removes only its
`mktemp -d` directory, and records `restoration.log`. After the script exits,
require a zero exit status, a successful byte comparison when a preference was
originally present, no live PID from `server.pid`, and the original device
settings in `device-settings-before.txt`. Append concise visual/performance and
restoration evidence to bead `precor-9_3x-rbg`.

- [ ] **Step 6: Commit any final test-only correction and push the branch**

```bash
git pull --rebase
git push
git status --short --branch
```

Expected: `feat/land-ridgeline-timeline` is clean and up to date with its remote before the previously approved final merge/cleanup workflow resumes.
