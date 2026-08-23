package com.precor.treadmill.ui.screens.running

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Replays a real multi-interval program through the map's real geometry, second by
 * second, and asserts the thing the user asked for: **a transition label that is on
 * screen does not disappear while you keep running.**
 *
 * The per-frame placement rules are unit-tested in [RidgelineChipLayoutTest]; this walks
 * a whole workout so camera pans, switchback sweep and chip crowding all interact the way
 * they do on the treadmill. It also writes `build/ridgeline-labels.svg`, a filmstrip of
 * the run, so the result can be eyeballed without a device.
 */
class RidgelineLabelStabilityTest {

    // The same 12-interval workout used for the on-device check: short segments, mixed
    // grades, 16 minutes — long enough that the camera windows and pans.
    private val plan = listOf(
        Triple(3.0, 1.0, 120.0), Triple(4.5, 3.0, 90.0), Triple(3.2, 1.5, 60.0),
        Triple(5.0, 6.0, 75.0), Triple(3.0, 2.0, 45.0), Triple(4.2, 8.5, 90.0),
        Triple(3.0, 1.0, 60.0), Triple(5.5, 4.0, 120.0), Triple(3.4, 2.5, 50.0),
        Triple(4.8, 7.0, 80.0), Triple(3.0, 0.5, 100.0), Triple(4.0, 5.5, 70.0),
    )
    private val route = RidgelineRoute(
        plan.map { (speed, grade, dur) -> RouteInterval(grade = grade, speed = speed, durSec = dur) },
    )

    // Canvas geometry mirroring drawRidgeline() on a landscape tablet panel.
    private val w = 1280f
    private val h = 800f
    private val dp = 2f
    private val stripW = 12f * dp
    private val stripX = w - 16f * dp - stripW / 2f
    private val mapW = stripX - stripW / 2f - 14f * dp
    private val topY = 74f / 800f * h
    private val botY = h - 50f * dp
    private val centerX = mapW * 0.50f
    private val ampBase = mapW * 0.40f
    private val metricsGuard = Rect(24f - 24f, 24f - 24f, 24f + 320f + 24f, 24f + 180f + 24f)

    private fun modelFor(targetRoute: RidgelineRoute) = prepareTransitionLabelModel(
        route = targetRoute,
        maxPillWidth = mapW - 8f,
        gradeColorFor = { Color.White },
        speedColorFor = { Color.White },
        measure = { text, _, maxWidth ->
            // Representative proportional-glyph widths: deliberately variable so tests
            // exercise real pill geometry rather than one hard-coded width.
            val natural = 7f + text.sumOf { (it.code % 7 + 5).toDouble() }.toFloat()
            MeasuredTransitionText(text, minOf(natural, maxWidth ?: natural))
        },
    )
    private val labelModel = modelFor(route)

    private data class Frame(
        val markerPos: Double,
        val camLo: Double,
        val marker: Pair<Float, Float>,
        val anchors: Map<Double, Pair<Float, Float>>,
        val widths: Map<Double, Float>,
        val slots: List<ChipSlot>,
    )

    /** One rendered frame at [markerPos], with the camera at [camLo]. */
    private fun frameAt(markerPos: Double, camLo: Double): Frame {
        val geom = RidgelineGeometry(route, centerX, ampBase, camLo, POS_WINDOW, topY, botY)
        val mx = geom.worldX(markerPos)
        val my = geom.screenY(markerPos)
        val frame = layoutTransitionLabelFrame(
            model = labelModel,
            geometry = geom,
            markerPos = markerPos,
            centerX = centerX,
            mapW = mapW,
            markerRect = Rect(mx - 20f, my - 20f, mx + 20f, my + 20f),
            metricsGuard = metricsGuard,
            topBound = topY,
            botBound = botY,
        )
        val anchors = frame.visible.associate { it.label.key to (it.candidate.anchorX to it.candidate.anchorY) }
        val widths = frame.visible.associate { it.label.key to it.label.pillW }
        return Frame(markerPos, camLo, mx to my, anchors, widths, frame.layout.slots)
    }

    /** Walk the whole program at 1s resolution, camera following as it does live. */
    private fun runFrames(): List<Frame> {
        val frames = ArrayList<Frame>()
        var camLo = computeTargetLo(route, 0.0)
        var t = 0.0
        while (t <= route.total) {
            // The live camera eases toward its target over ~1s; sample the eased value so
            // mid-pan frames (where chips are sliding) are covered too.
            val target = computeTargetLo(route, t)
            camLo += (target - camLo) * 0.25
            frames.add(frameAt(t, camLo))
            t += 1.0
        }
        return frames
    }

    @Test
    fun everyVisibleTransitionKeepsItsLabelForTheWholeRun() {
        val frames = runFrames()
        val misses = mutableListOf<String>()
        for (f in frames) {
            val placed = f.slots.map { it.key }.toSet()
            for (bs in f.anchors.keys) {
                if (bs !in placed) misses.add("t=%.0fs: boundary %.0fs in window but unlabelled".format(f.markerPos, bs))
            }
        }
        assertTrue(
            "${misses.size} frame(s) dropped a visible transition label:\n" +
                misses.take(15).joinToString("\n"),
            misses.isEmpty(),
        )
    }

    /** No label may blink: once shown, it stays shown until it scrolls out of the window. */
    @Test
    fun noLabelBlinksOffAndBackOn() {
        val frames = runFrames()
        val wasShown = HashSet<Double>()
        val blinks = mutableListOf<String>()
        for (f in frames) {
            val placed = f.slots.map { it.key }.toSet()
            for (bs in f.anchors.keys) {
                if (bs in placed) wasShown.add(bs)
                else if (bs in wasShown) blinks.add("t=%.0fs: boundary %.0fs vanished while still on screen".format(f.markerPos, bs))
            }
            // Boundaries that scrolled out are allowed to come back fresh later.
            wasShown.retainAll(f.anchors.keys)
        }
        assertTrue(
            "${blinks.size} label disappearance(s) mid-run:\n" + blinks.take(15).joinToString("\n"),
            blinks.isEmpty(),
        )
    }

    /** Placed pills never overlap each other, the marker, or the metrics pill. */
    @Test
    fun placedLabelsNeverOverlap() {
        val clashes = mutableListOf<String>()
        for (f in runFrames()) {
            val rects = f.slots.map {
                Rect(it.pillLeft, it.pillTop, it.pillLeft + f.widths.getValue(it.key), it.pillTop + CHIP_H)
            }
            val markerRect = Rect(
                f.marker.first - 20f,
                f.marker.second - 20f,
                f.marker.first + 20f,
                f.marker.second + 20f,
            )
            for (a in rects.indices) {
                if (rects[a].overlaps(metricsGuard)) clashes.add("t=%.0fs: chip over the metrics pill".format(f.markerPos))
                if (rects[a].overlaps(markerRect)) clashes.add("t=%.0fs: chip over the marker".format(f.markerPos))
                for (b in a + 1 until rects.size) {
                    if (rects[a].overlaps(rects[b])) clashes.add("t=%.0fs: chips overlap".format(f.markerPos))
                }
            }
        }
        assertTrue("${clashes.size} overlap(s):\n" + clashes.take(10).joinToString("\n"), clashes.isEmpty())
    }

    /** Production collection must not truncate dense, supported programs at 40 labels. */
    @Test
    fun allFortyOneVisibleBoundariesReachLayout() {
        val denseRoute = RidgelineRoute(
            (0 until 41).map { n ->
                RouteInterval(grade = (n % 10).toDouble(), speed = 3.0, durSec = 10.0)
            },
        )
        val denseGeometry = RidgelineGeometry(
            denseRoute, centerX, ampBase, camLo = 0.0, ew = POS_WINDOW, topY, botY,
        )
        val frame = layoutTransitionLabelFrame(
            model = modelFor(denseRoute),
            geometry = denseGeometry,
            markerPos = 0.0,
            centerX = centerX,
            mapW = mapW,
            markerRect = Rect(centerX - 20f, botY - 20f, centerX + 20f, botY + 20f),
            metricsGuard = null,
            topBound = topY,
            botBound = botY,
        )

        assertEquals(41, frame.visible.size)
        assertEquals(
            "production collection/layout truncated visible boundaries",
            frame.visible.size,
            frame.layout.slots.size,
        )
    }

    @Test
    fun denseFramesProjectOnlyBookendsAndBadgeEveryAggregateEnd() {
        val denseRoute = RidgelineRoute(
            (0 until 1_000).map { n ->
                RouteInterval(grade = (n % 10).toDouble(), speed = 3.0, durSec = 1.0)
            },
        )
        val frame = layoutTransitionLabelFrame(
            model = modelFor(denseRoute),
            geometry = RidgelineGeometry(
                denseRoute, centerX, ampBase, camLo = 0.0, ew = denseRoute.total, topY, botY,
            ),
            markerPos = 0.0,
            centerX = centerX,
            mapW = mapW,
            markerRect = Rect(centerX - 20f, botY - 20f, centerX + 20f, botY + 20f),
            metricsGuard = null,
            topBound = topY,
            botBound = botY,
        )

        assertTrue(frame.groups.size <= 32)
        assertEquals(1_000, frame.groups.sumOf { it.count })
        assertTrue(frame.visible.size <= 64)
        assertEquals(
            frame.groups.count { it.aggregate },
            frame.visible.count { it.endpointContent.badge != null },
        )
    }

    /** A long route prepares no TextLayouts until its small visible slice is requested. */
    @Test
    fun longRouteMeasuresOnlyVisibleLabelsOnFirstFrame() {
        val longRoute = RidgelineRoute(
            (0 until 1_000).map { n ->
                // The server contract clamps generated intervals to at least 10s.
                RouteInterval(grade = (n % 17).toDouble(), speed = 2.5 + (n % 11) * 0.1, durSec = 10.0)
            },
        )
        var measurements = 0
        val model = prepareTransitionLabelModel(
            route = longRoute,
            maxPillWidth = mapW - 8f,
            gradeColorFor = { Color.White },
            speedColorFor = { Color.White },
            measure = { text, _, maxWidth ->
                measurements++
                val natural = 7f + text.length * 7f
                MeasuredTransitionText(text, minOf(natural, maxWidth ?: natural))
            },
        )
        assertEquals("model creation eagerly measured the entire route", 0, measurements)

        val geometry = RidgelineGeometry(
            longRoute, centerX, ampBase, camLo = 4_000.0, ew = POS_WINDOW, topY, botY,
        )
        val visible = collectVisibleTransitionCandidates(model, geometry, markerPos = 4_300.0)

        assertEquals(61, visible.size)
        assertTrue("first viewport measured $measurements layouts", measurements <= 122)
        println("long-route first viewport: visible=${visible.size} measurements=$measurements")
        assertEquals(
            (401..459).map { it * 10.0 },
            visibleInteriorBoundaries(longRoute, 4_000.0, 4_600.0),
        )
    }

    /** Smooth projection continues while 2px-quantized packing is reused. */
    @Test
    fun subpixelRunningFramesReusePackingButKeepExactAnchors() {
        val movingRoute = RidgelineRoute(
            (0 until 80).map { n ->
                RouteInterval((n % 12).toDouble(), 3.0 + n % 5 * 0.1, 10.0)
            },
        )
        val model = modelFor(movingRoute)
        val cache = TransitionLabelFrameCache<String>()
        var priorAnchorY: Float? = null
        var sawSmoothAnchorMotion = false
        var priorSlots: List<ChipSlot>? = null
        var priorComputations = 0
        repeat(100) { frameIndex ->
            val camLo = 100.0 + frameIndex * 0.001
            val markerPos = 350.0 + frameIndex * 0.001
            val geometry = RidgelineGeometry(movingRoute, centerX, ampBase, camLo, POS_WINDOW, topY, botY)
            val mx = geometry.worldX(markerPos)
            val my = geometry.screenY(markerPos)
            val frame = cache.layout(
                model, geometry, markerPos, centerX, mapW,
                Rect(mx - 20f, my - 20f, mx + 20f, my + 20f), metricsGuard, topY, botY,
            )
            val projected = frame.visible[10]
            assertEquals(geometry.screenY(projected.label.key), projected.candidate.anchorY, 0f)
            if (cache.computations == priorComputations) assertEquals(priorSlots, frame.layout.slots)
            priorAnchorY?.let { if (it != projected.candidate.anchorY) sawSmoothAnchorMotion = true }
            priorAnchorY = projected.candidate.anchorY
            priorSlots = frame.layout.slots
            priorComputations = cache.computations
        }
        assertTrue("exact leader anchors froze with cached packing", sawSmoothAnchorMotion)
        assertTrue("subpixel frames repacked ${cache.computations} times", cache.computations <= 3)

        val beforeThreshold = cache.computations
        val shifted = RidgelineGeometry(movingRoute, centerX, ampBase, 103.0, POS_WINDOW, topY, botY)
        val shiftedMarker = 353.0
        val sx = shifted.worldX(shiftedMarker)
        val sy = shifted.screenY(shiftedMarker)
        cache.layout(
            model, shifted, shiftedMarker, centerX, mapW,
            Rect(sx - 20f, sy - 20f, sx + 20f, sy + 20f), metricsGuard, topY, botY,
        )
        assertEquals("crossing the packing grid did not invalidate", beforeThreshold + 1, cache.computations)
        println("subpixel packing: frames=101 computations=${cache.computations}")
    }

    /** Normal 10s intervals remain cheap while marker and camera both move. */
    @Test
    fun sixtyOneVisibleTransitionsStayWithinRunningFrameBudget() {
        val normalRoute = RidgelineRoute(
            (0 until 90).map { n ->
                RouteInterval((n % 13).toDouble(), 2.5 + (n % 9) * 0.1, 10.0)
            },
        )
        val normalModel = modelFor(normalRoute)
        val cache = TransitionLabelFrameCache<String>()
        var first: TransitionLabelFrame<String>? = null
        val started = System.nanoTime()
        repeat(120) { frameIndex ->
            val camLo = 100.0 + frameIndex * 0.01
            val markerPos = 350.0 + frameIndex * 0.01
            val geometry = RidgelineGeometry(normalRoute, centerX, ampBase, camLo, POS_WINDOW, topY, botY)
            val mx = geometry.worldX(markerPos)
            val my = geometry.screenY(markerPos)
            val frame = cache.layout(
                normalModel, geometry, markerPos, centerX, mapW,
                Rect(mx - 20f, my - 20f, mx + 20f, my + 20f), metricsGuard, topY, botY,
            )
            if (first == null) first = frame
            assertEquals(frame.visible.size, frame.layout.slots.size)
            val byKey = frame.prioritized.associateBy { it.label.key }
            val rects = frame.layout.slots.map { slot ->
                val projected = byKey.getValue(slot.key)
                slot to Rect(
                    slot.pillLeft, slot.pillTop,
                    slot.pillLeft + projected.endpointContent.effectivePillW, slot.pillTop + CHIP_H,
                )
            }
            for (a in rects.indices) if (!rects[a].first.overlapFallback) {
                assertTrue(!rects[a].second.overlaps(metricsGuard))
                assertTrue(!rects[a].second.overlaps(Rect(mx - 20f, my - 20f, mx + 20f, my + 20f)))
                for (b in a + 1 until rects.size) if (!rects[b].first.overlapFallback) {
                    assertTrue(!rects[a].second.overlaps(rects[b].second))
                }
            }
        }
        val elapsedMs = (System.nanoTime() - started) / 1_000_000
        assertEquals(61, first!!.visible.size)
        assertTrue("normal first-frame probes=${first!!.layout.stats.probes}", first!!.layout.stats.probes < 20_000)
        assertTrue("120 moving frames repacked ${cache.computations} times", cache.computations <= 12)
        println(
            "normal moving: frames=120 computations=${cache.computations} " +
                "probes=${first!!.layout.stats.probes} checks=${first!!.layout.stats.collisionChecks} elapsedMs=$elapsedMs",
        )
    }

    /** Dense supported routes use the same prepared-model and frame pipeline as drawing. */
    @Test
    fun sixHundredOneVisibleTransitionsHaveBoundedDeterministicLayoutWork() {
        val denseRoute = RidgelineRoute(
            (0 until 601).map { n ->
                RouteInterval(grade = (n % 17).toDouble(), speed = 2.5 + (n % 11) * 0.1, durSec = 1.0)
            },
        )
        val denseGeometry = RidgelineGeometry(
            denseRoute, centerX, ampBase, camLo = 0.0, ew = POS_WINDOW, topY, botY,
        )
        val model = modelFor(denseRoute)
        val markerPos = 300.0
        val marker = Rect(
            denseGeometry.worldX(markerPos) - 20f,
            denseGeometry.screenY(markerPos) - 20f,
            denseGeometry.worldX(markerPos) + 20f,
            denseGeometry.screenY(markerPos) + 20f,
        )
        val frameCache = TransitionLabelFrameCache<String>()
        fun run() = frameCache.layout(
            model = model,
            geometry = denseGeometry,
            markerPos = markerPos,
            centerX = centerX,
            mapW = mapW,
            markerRect = marker,
            metricsGuard = metricsGuard,
            topBound = topY,
            botBound = botY,
        )

        val started = System.nanoTime()
        val first = run()
        val second = run()
        val elapsedMs = (System.nanoTime() - started) / 1_000_000
        assertTrue(first.visible.size <= 64)
        assertEquals(first.visible.size, first.layout.slots.size)
        assertEquals(601, first.groups.sumOf { it.count })
        assertEquals(first.prioritized.map { it.label.key }, second.prioritized.map { it.label.key })
        assertEquals(first.layout.slots, second.layout.slots)
        assertEquals("pulse-only redraw recomputed dense layout", 1, frameCache.computations)
        assertTrue("placement probes were unbounded: ${first.layout.stats.probes}", first.layout.stats.probes < 50_000)
        assertTrue(
            "spatial collision checks were unbounded: ${first.layout.stats.collisionChecks}",
            first.layout.stats.collisionChecks < 500_000,
        )
        assertTrue("dense production layout took ${elapsedMs}ms", elapsedMs < 250)
        println(
            "dense layout: labels=601 probes=${first.layout.stats.probes} " +
                "checks=${first.layout.stats.collisionChecks} fallbacks=${first.layout.stats.overlapFallbacks} " +
                "elapsedMs=$elapsedMs",
        )
        val avoidableMarkerClashes = first.layout.slots.zip(first.prioritized).count { (slot, projected) ->
            !slot.overlapFallback &&
                Rect(
                    slot.pillLeft, slot.pillTop,
                    slot.pillLeft + projected.endpointContent.effectivePillW, slot.pillTop + CHIP_H,
                )
                    .overlaps(marker)
        }
        assertEquals("shared pipeline ignored marker guard for packable slots", 0, avoidableMarkerClashes)
        val denseRects = first.layout.slots.zip(first.prioritized).map { (slot, projected) ->
            slot to Rect(
                slot.pillLeft,
                slot.pillTop,
                slot.pillLeft + projected.endpointContent.effectivePillW,
                slot.pillTop + CHIP_H,
            )
        }
        assertTrue(
            "shared pipeline ignored metrics guard for a packable slot",
            denseRects.none { (slot, rect) -> !slot.overlapFallback && rect.overlaps(metricsGuard) },
        )
        for (a in denseRects.indices) for (b in a + 1 until denseRects.size) {
            if (!denseRects[a].first.overlapFallback && !denseRects[b].first.overlapFallback) {
                assertTrue("packable dense slots $a and $b overlap", !denseRects[a].second.overlaps(denseRects[b].second))
            }
        }
        assertTrue("representative widths were accidentally constant", model.labels.map { it.pillW }.distinct().size > 3)

        val movedMarkerPos = markerPos + 1.0
        val movedMarker = Rect(
            denseGeometry.worldX(movedMarkerPos) - 20f,
            denseGeometry.screenY(movedMarkerPos) - 20f,
            denseGeometry.worldX(movedMarkerPos) + 20f,
            denseGeometry.screenY(movedMarkerPos) + 20f,
        )
        frameCache.layout(
            model, denseGeometry, movedMarkerPos, centerX, mapW, movedMarker,
            metricsGuard, topY, botY,
        )
        assertEquals("progress change did not invalidate layout", 2, frameCache.computations)
    }

    /** Prepared labels belong to the route model, even when a dense viewport is repacked. */
    @Test
    fun denseRecomputationDoesNotRemeasurePreparedLabels() {
        val denseRoute = RidgelineRoute(
            (0 until 601).map { n ->
                RouteInterval(grade = (n % 17).toDouble(), speed = 2.5 + (n % 11) * 0.1, durSec = 1.0)
            },
        )
        var measurements = 0
        val model = prepareTransitionLabelModel(
            route = denseRoute,
            maxPillWidth = mapW - 8f,
            gradeColorFor = { Color(0xff000000.toInt() or it) },
            speedColorFor = { Color(0xff100000.toInt() or it) },
            measure = { text, _, maxWidth ->
                measurements++
                val natural = 7f + text.length * 7f
                MeasuredTransitionText(text, minOf(natural, maxWidth ?: natural))
            },
        )
        val frameCache = TransitionLabelFrameCache<String>()

        fun layout(camLo: Double, markerPos: Double): TransitionLabelFrame<String> {
            val geometry = RidgelineGeometry(
                denseRoute, centerX, ampBase, camLo, POS_WINDOW, topY, botY,
            )
            val marker = Rect(
                geometry.worldX(markerPos) - 20f,
                geometry.screenY(markerPos) - 20f,
                geometry.worldX(markerPos) + 20f,
                geometry.screenY(markerPos) + 20f,
            )
            return frameCache.layout(
                model, geometry, markerPos, centerX, mapW, marker,
                metricsGuard, topY, botY,
            )
        }

        val first = layout(camLo = 0.0, markerPos = 300.0)
        val firstMeasurements = measurements
        val second = layout(camLo = 2.0, markerPos = 302.0)

        assertTrue(first.visible.size <= 64)
        assertTrue(second.visible.size <= 64)
        assertEquals(601, first.groups.sumOf { it.count })
        assertEquals(599, second.groups.sumOf { it.count })
        assertEquals("moved dense viewport did not recompute layout", 2, frameCache.computations)
        assertTrue(
            "moving dense viewport prepared unbounded text: first=$firstMeasurements second=$measurements",
            measurements - firstMeasurements <= 192,
        )
        println(
            "dense prepared cache: firstMeasurements=$firstMeasurements " +
                "secondMeasurements=$measurements",
        )
    }

    /** Renders a filmstrip so the run can be reviewed by eye (not an assertion). */
    @Test
    fun writesFilmstripForVisualReview() {
        val frames = runFrames()
        val picks = (0 until 12).map { frames[(it * (frames.size - 1)) / 11] }
        val cols = 4
        val scale = 0.42f
        val cw = w * scale
        val ch = h * scale
        val sb = StringBuilder()
        sb.append("""<svg xmlns="http://www.w3.org/2000/svg" width="${cw * cols + 40}" """)
        sb.append(""" height="${ch * 3 + 60}" style="background:#0b0f12">""")
        picks.forEachIndexed { n, f ->
            val ox = 10 + (n % cols) * cw
            val oy = 10 + (n / cols) * (ch + 16)
            sb.append("""<g transform="translate($ox,$oy) scale($scale)">""")
            sb.append("""<rect width="$w" height="$h" fill="#11171b" stroke="#243038"/>""")
            // route thread
            val pts = (0..160).map { k ->
                val p = f.camLo + (k / 160.0) * POS_WINDOW
                val g = RidgelineGeometry(route, centerX, ampBase, f.camLo, POS_WINDOW, topY, botY)
                if (p > route.total) null else "${g.worldX(p)},${g.screenY(p)}"
            }.filterNotNull().joinToString(" ")
            sb.append("""<polyline points="$pts" fill="none" stroke="#e8e4df" stroke-width="4" opacity="0.85"/>""")
            // chips
            for (s in f.slots) {
                val a = f.anchors[s.key]!!
                if (s.offBend) {
                    val pillW = f.widths.getValue(s.key)
                    val edge = if (s.pillLeft > a.first) s.pillLeft else s.pillLeft + pillW
                    sb.append("""<line x1="${a.first}" y1="${a.second}" x2="$edge" y2="${s.pillTop + CHIP_H / 2}" stroke="#e8e4df" stroke-width="2" opacity="0.4"/>""")
                }
                val past = s.key < f.markerPos
                val col = if (past) "#6f7c85" else "#9fd0a8"
                val pillW = f.widths.getValue(s.key)
                sb.append("""<rect x="${s.pillLeft}" y="${s.pillTop}" width="$pillW" height="$CHIP_H" rx="6" fill="#070b0e" stroke="$col" stroke-width="2" opacity="${transitionChipAlpha(past)}"/>""")
                sb.append("""<text x="${s.pillLeft + 7}" y="${s.pillTop + 17}" font-family="monospace" font-size="14" fill="$col">${"%.1f%%".format(route.gradeAt(s.key + 1))}</text>""")
                sb.append("""<circle cx="${a.first}" cy="${a.second}" r="5" fill="#e8e4df"/>""")
            }
            sb.append("""<circle cx="${f.marker.first}" cy="${f.marker.second}" r="11" fill="#7fd18c"/>""")
            sb.append("""<rect x="${metricsGuard.left}" y="${metricsGuard.top}" width="${metricsGuard.width}" height="${metricsGuard.height}" fill="none" stroke="#3a4650" stroke-dasharray="8 8"/>""")
            sb.append("""<text x="20" y="${h - 20}" font-family="monospace" font-size="26" fill="#8fa0ab">t=${f.markerPos.toInt()}s  labels=${f.slots.size}/${f.anchors.size}</text>""")
            sb.append("</g>")
        }
        sb.append("</svg>")
        val out = File("build/ridgeline-labels.svg")
        out.parentFile?.mkdirs()
        out.writeText(sb.toString())
        println("filmstrip -> ${out.absolutePath}")
    }
}
