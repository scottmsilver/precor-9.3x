package com.precor.treadmill.ui.screens.running

import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Transition labels on the trail map must be STABLE: once a chip is on screen it stays
 * on screen until it scrolls out of the window. The bug these tests pin down: the
 * position marker's guard rect used to *delete* the chip of the bend you were running
 * toward, so the label showed for a while and then popped out exactly when it mattered.
 */
class RidgelineChipLayoutTest {

    private val mapW = 640f
    private val centerX = 320f

    private fun markerAt(x: Float, y: Float) = Rect(x - 20f, y - 20f, x + 20f, y + 20f)

    private fun layout(
        candidates: List<ChipCandidate>,
        marker: Rect,
        guard: Rect? = null,
    ) = layoutTransitionChips(
        candidates = candidates,
        centerX = centerX,
        mapW = mapW,
        markerRect = marker,
        metricsGuard = guard,
        topBound = 0f,
        botBound = 800f,
    )

    /** The reported failure: a chip must survive the marker sweeping straight through it. */
    @Test
    fun chipSurvivesTheMarkerSweepingPastIt() {
        val chip = ChipCandidate(key = 120.0, anchorX = 300f, anchorY = 400f, pillW = 70f)
        var markerY = 520f
        while (markerY >= 280f) {
            val slots = layout(listOf(chip), markerAt(298f, markerY))
            assertEquals("chip dropped with marker at y=$markerY", 1, slots.size)
            markerY -= 4f
        }
    }

    /** A chip never overlaps the marker either — it moves out of the way instead. */
    @Test
    fun chipMovesOutOfTheMarkersWayInsteadOfVanishing() {
        val chip = ChipCandidate(key = 120.0, anchorX = 300f, anchorY = 400f, pillW = 70f)
        val marker = markerAt(300f, 400f)
        val slot = layout(listOf(chip), marker).single()
        val pill = Rect(slot.pillLeft, slot.pillTop, slot.pillLeft + chip.pillW, slot.pillTop + CHIP_H)
        assertTrue("nudged pill still sits on the marker: $pill vs $marker", !pill.overlaps(marker))
        assertTrue("chip should have been nudged off its bend", slot.offBend)
    }

    /** Two bends a few seconds apart both keep their label (the old code dropped one). */
    @Test
    fun neighbouringChipsBothKeepTheirLabel() {
        val chips = listOf(
            ChipCandidate(key = 100.0, anchorX = 260f, anchorY = 400f, pillW = 70f),
            ChipCandidate(key = 130.0, anchorX = 280f, anchorY = 412f, pillW = 70f),
            ChipCandidate(key = 160.0, anchorX = 300f, anchorY = 424f, pillW = 70f),
        )
        val slots = layout(chips, markerAt(120f, 700f))
        assertEquals("every neighbouring bend keeps its chip", chips.size, slots.size)
        // ...and they don't stack on top of each other.
        val rects = slots.map { Rect(it.pillLeft, it.pillTop, it.pillLeft + 70f, it.pillTop + CHIP_H) }
        for (a in rects.indices) for (b in a + 1 until rects.size) {
            assertTrue("chips $a and $b overlap", !rects[a].overlaps(rects[b]))
        }
    }

    /** The metrics pill still wins — but by displacing the chip, not deleting it. */
    @Test
    fun chipDodgesTheMetricsPillWithoutDisappearing() {
        val chip = ChipCandidate(key = 60.0, anchorX = 120f, anchorY = 90f, pillW = 70f)
        val guard = Rect(0f, 0f, 300f, 120f)
        val slot = layout(listOf(chip), markerAt(400f, 700f), guard).single()
        val pill = Rect(slot.pillLeft, slot.pillTop, slot.pillLeft + chip.pillW, slot.pillTop + CHIP_H)
        assertTrue("chip overlaps the metrics pill: $pill", !pill.overlaps(guard))
    }

    /** Nudging must not push a pill off the top or bottom of the canvas. */
    @Test
    fun nudgedChipStaysOnCanvas() {
        val chip = ChipCandidate(key = 60.0, anchorX = 300f, anchorY = 12f, pillW = 70f)
        val slot = layout(listOf(chip), markerAt(300f, 12f)).single()
        assertTrue("pill pushed above the canvas: ${slot.pillTop}", slot.pillTop >= 0f)
        assertTrue("pill pushed below the canvas", slot.pillTop + CHIP_H <= 800f)
    }

    /**
     * The whole-run property: with a realistic cluster of bends and the metrics pill in
     * the corner, no label may drop out at ANY point as the marker climbs the map. This
     * is the invariant the reported bug broke — a chip that is up must stay up.
     */
    @Test
    fun noLabelDisappearsAnywhereAlongTheRun() {
        // Eight bends spread over the map, switchbacking left/right like a real route.
        val chips = (0 until 8).map { n ->
            ChipCandidate(
                key = n * 75.0,
                anchorX = if (n % 2 == 0) 200f else 440f,
                anchorY = 700f - n * 80f,
                pillW = 72f,
            )
        }
        val guard = Rect(0f, 0f, 260f, 130f) // metrics pill, top-left
        var markerY = 760f
        while (markerY >= 40f) {
            val slots = layout(chips, markerAt(320f, markerY), guard)
            assertEquals(
                "a label dropped out with the marker at y=$markerY " +
                    "(placed: ${slots.map { it.key }})",
                chips.size,
                slots.size,
            )
            markerY -= 5f
        }
    }

    /** Placement is a function of geometry only, so equal input frames render identically. */
    @Test
    fun placementIsDeterministic() {
        val chips = listOf(
            ChipCandidate(key = 100.0, anchorX = 260f, anchorY = 400f, pillW = 70f),
            ChipCandidate(key = 130.0, anchorX = 280f, anchorY = 412f, pillW = 70f),
        )
        val a = layout(chips, markerAt(300f, 500f))
        val b = layout(chips, markerAt(300f, 500f))
        assertEquals(a, b)
    }

    /** The bounded ordinary nudge search must hand off to a whole-canvas search. */
    @Test
    fun fallbackSearchFindsSpaceBeyondTheOrdinaryNudgeRange() {
        val chip = ChipCandidate(key = 60.0, anchorX = 300f, anchorY = 100f, pillW = 70f)
        val guard = Rect(0f, 0f, mapW, 690f)

        val slot = layout(listOf(chip), markerAt(500f, 760f), guard).single()
        val pill = Rect(slot.pillLeft, slot.pillTop, slot.pillLeft + chip.pillW, slot.pillTop + CHIP_H)

        assertTrue("fallback still overlaps the guard: $pill", !pill.overlaps(guard))
        assertTrue("fallback did not move beyond the ordinary nudge range", slot.pillTop > 690f)
    }

    /** Even impossible packing is fail-visible: overlap is preferable to disappearance. */
    @Test
    fun physicallyUnsatisfiableCanvasStillRetainsEveryLabel() {
        val chips = (0 until 6).map { n ->
            ChipCandidate(key = n.toDouble(), anchorX = 50f, anchorY = 50f, pillW = 92f)
        }
        val slots = layoutTransitionChips(
            candidates = chips,
            centerX = 50f,
            mapW = 100f,
            markerRect = Rect(0f, 0f, 100f, 100f),
            metricsGuard = Rect(0f, 0f, 100f, 100f),
            topBound = 0f,
            botBound = 100f,
        )

        assertEquals("last-resort placement silently dropped labels", chips.size, slots.size)
        assertTrue(slots.all { it.pillLeft >= 0f && it.pillTop >= 0f && it.pillTop + CHIP_H <= 100f })
        assertTrue("impossible packing was not identified as last-resort overlap", slots.all { it.overlapFallback })
    }

    /** Travelled labels dim as a unit; upcoming labels retain full-strength text. */
    @Test
    fun pastChipTextUsesTheSameDimmedAlphaAsItsChrome() {
        val pastAlpha = transitionChipAlpha(travelled = true)
        assertTrue("past text alpha is too low for reliable contrast", pastAlpha >= 0.65f)
        assertTrue("past label must remain visibly dimmer", pastAlpha < 1f)
        assertEquals(1f, transitionChipAlpha(travelled = false), 0f)
    }

    /** Blocker-edge fallback finds a free slot that the old 32px grid skipped. */
    @Test
    fun blockerEdgeFallbackFindsUnalignedFreeRectangle() {
        val chip = ChipCandidate(key = 1.0, anchorX = 100f, anchorY = 50f, pillW = 50f)
        val guards = listOf(
            Rect(0f, 0f, 200f, 34f),
            Rect(0f, 66f, 200f, 100f),
            Rect(0f, 34f, 79f, 66f),
            Rect(137f, 34f, 200f, 66f),
        )
        val result = layoutTransitionChipsDetailed(
            candidates = listOf(chip),
            centerX = 100f,
            mapW = 200f,
            markerRect = Rect.Zero,
            metricsGuard = null,
            topBound = 0f,
            botBound = 100f,
            fixedGuards = guards,
        )
        val slot = result.slots.single()
        val pill = Rect(slot.pillLeft, slot.pillTop, slot.pillLeft + chip.pillW, slot.pillTop + CHIP_H)

        assertTrue("edge fallback overlapped a guard: $pill", guards.none { it.overlaps(pill) })
        assertTrue("free edge slot was incorrectly marked overlapping", !slot.overlapFallback)
        assertEquals(83f, slot.pillLeft, 0f)
        assertEquals(38f, slot.pillTop, 0f)
    }

    /** Model measurement constrains both pill geometry and ellipsized text width. */
    @Test
    fun oversizedPreparedPillIsHorizontallyContained() {
        val route = RidgelineRoute(listOf(RouteInterval(grade = 99.0, speed = 99.0, durSec = 60.0)))
        val model = prepareTransitionLabelModel(
            route = route,
            maxPillWidth = 92f,
            gradeColorFor = { Color.White },
            speedColorFor = { Color.White },
            measure = { text, _, maxWidth ->
                val natural = text.length * 80f
                MeasuredTransitionText(text, minOf(natural, maxWidth ?: natural))
            },
        )
        val label = model.labels.single()

        assertTrue(label.pillW <= 92f)
        assertTrue(label.grade.width + label.speed.width + 20f <= label.pillW)
    }

    /** Narrow-canvas integer rounding cannot push either text run outside its pill. */
    @Test
    fun narrowPreparedPillContainsRoundedTextLayouts() {
        val route = RidgelineRoute(listOf(RouteInterval(grade = 99.0, speed = 99.0, durSec = 60.0)))
        val model = prepareTransitionLabelModel(
            route = route,
            maxPillWidth = 15f,
            gradeColorFor = { Color.White },
            speedColorFor = { Color.White },
            measure = { text, _, maxWidth ->
                val natural = text.length * 20f
                MeasuredTransitionText(text, maxWidth?.let { kotlin.math.ceil(it).toFloat() } ?: natural)
            },
        )
        val label = model.labels.single()

        assertTrue(label.pillW <= 15f)
        assertTrue(label.gradeOffset >= 0f)
        assertTrue(label.gradeOffset + label.grade.width <= label.pillW)
        assertTrue(label.speedOffset + label.speed.width <= label.pillW)
    }

    /** Finish/minimap transition text is prepared once per route, never per pulse frame. */
    @Test
    fun staticTransitionChromeIsPreparedForEveryBoundary() {
        val route = RidgelineRoute(
            listOf(
                RouteInterval(grade = 1.0, speed = 2.5, durSec = 10.0),
                RouteInterval(grade = 3.0, speed = 4.5, durSec = 20.0),
                RouteInterval(grade = 5.0, speed = 6.5, durSec = 30.0),
            ),
        )
        var measures = 0
        val labels = prepareRidgelineStaticLabels(
            route = route,
            finishColor = Color.White,
            lastTimeColor = Color.Gray,
            nextTimeColor = Color.Green,
            gradeColorFor = { Color.Yellow },
            speedColorFor = { Color.Cyan },
            measure = { kind, text, color ->
                measures++
                Triple(kind, text, color)
            },
        )

        assertEquals("FINISH · 19 ft", labels.finish.second)
        assertEquals(route.count, labels.transitions.size)
        assertEquals("0:10", labels.transitions[1].lastTime.second)
        assertEquals("3.0%", labels.transitions[1].grade.second)
        assertEquals("4.5", labels.transitions[1].speed.second)
        assertEquals(1 + route.count * 4, measures)
    }
}
