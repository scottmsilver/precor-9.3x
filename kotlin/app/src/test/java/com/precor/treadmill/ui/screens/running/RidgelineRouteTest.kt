package com.precor.treadmill.ui.screens.running

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression suite for HUD marker/route sync.
 *
 * History: the HUD once mapped the program's TIME fraction linearly onto a
 * DISTANCE-domain route (`route.total * timelinePos`). With mixed-speed intervals
 * those domains disagree, so the dot wasn't at a route bend when the incline
 * changed — most visibly right after a skip. The route now lives in the TIME
 * domain (position = planned seconds): posAtProgram() is the program clock itself,
 * making boundary sync structural. distAt() keeps the honest (nonlinear) miles
 * mapping for the organic detail.
 */
class RidgelineRouteTest {

    // interval 0: 600s @ 3mph (0.5 mi);  interval 1: 600s @ 6mph (1.0 mi)
    private val route = RidgelineRoute(
        listOf(
            RouteInterval(grade = 2.0, speed = 3.0, durSec = 600.0),
            RouteInterval(grade = 8.0, speed = 6.0, durSec = 600.0),
        ),
    )

    @Test
    fun `interval boundary lands exactly on route boundary`() {
        // The moment interval 1 begins (the incline change) the marker must sit at
        // the segment boundary.
        assertEquals(route.startOf(1), route.posAtProgram(1, 0.0), 1e-9)
        assertEquals(600.0, route.startOf(1), 1e-9)
    }

    @Test
    fun `mid-interval position is the program clock`() {
        assertEquals(900.0, route.posAtProgram(1, 300.0), 1e-9)
        assertEquals(300.0, route.posAtProgram(0, 300.0), 1e-9)
    }

    @Test
    fun `elapsed overshoot clamps to the interval end`() {
        // A stale intervalElapsed larger than the interval must not spill past
        // the segment boundary.
        assertEquals(600.0, route.posAtProgram(0, 9999.0), 1e-9)
    }

    @Test
    fun `out-of-range interval index clamps to route ends`() {
        assertEquals(route.total, route.posAtProgram(99, 0.0), 1e-9)
        assertEquals(0.0, route.posAtProgram(-1, 0.0), 1e-9)
    }

    @Test
    fun `distAt maps time to miles piecewise, not linearly`() {
        // Why the layout must be time-sized: half the TIME is not half the DISTANCE
        // once speeds differ. distAt keeps the honest piecewise mapping.
        assertEquals(0.5, route.distAt(600.0), 1e-9)   // end of the 3mph interval
        assertEquals(1.5, route.distAt(1200.0), 1e-9)  // full route
        assertEquals(1.0, route.distAt(900.0), 1e-9)   // 300s into the 6mph interval
        // Time midpoint (600s) is at 1/3 of the distance — the old linear mapping
        // would have placed it at 0.75 mi, a quarter mile past the bend.
        assertTrue(kotlin.math.abs(route.distAt(600.0) - route.totalMi / 2.0) > 0.2)
    }

    @Test
    fun `vertAt integrates grade over planned miles`() {
        // 0.5 mi @ 2% = 52.8 ft;  + 1.0 mi @ 8% = 422.4 ft.
        assertEquals(52.8, route.vertAt(600.0), 1e-6)
        assertEquals(52.8 + 422.4, route.vertAt(1200.0), 1e-6)
        // Flat prefix of an interval accrues proportionally.
        assertEquals(26.4, route.vertAt(300.0), 1e-6)
    }

    /**
     * The reported bug, end to end: run a mixed-speed program, skip ahead, then walk
     * through every remaining incline change — the marker must sit exactly on a route
     * bend (segment boundary) at the instant each interval begins.
     */
    @Test
    fun `after a skip, every incline change lands the marker on a route bend`() {
        // A realistic interval workout: warmup, hard/easy repeats, cooldown —
        // durations (s) and speeds (mph) all different so time ≠ distance.
        data class Planned(val duration: Double, val speed: Double, val grade: Double)
        val plan = listOf(
            Planned(300.0, 3.0, 1.0),   // warmup walk
            Planned(240.0, 7.5, 4.0),   // hard
            Planned(120.0, 3.5, 1.0),   // recover
            Planned(240.0, 8.0, 6.0),   // harder
            Planned(120.0, 3.5, 1.0),   // recover
            Planned(300.0, 5.5, 2.0),   // cooldown
        )
        val r = RidgelineRoute(plan.map { RouteInterval(it.grade, it.speed, it.duration) })

        // "Skipped ahead": jump straight to interval 3. The server then reports
        // (currentInterval=3, intervalElapsed=0) — the incline just changed.
        assertEquals(r.startOf(3), r.posAtProgram(3, 0.0), 1e-9)

        // ...and from there, every subsequent interval start is exactly a bend
        // whose grade is the new interval's grade.
        for (i in 3 until plan.size) {
            val atBend = r.posAtProgram(i, 0.0)
            assertEquals("interval $i start must be a segment boundary", r.startOf(i), atBend, 1e-9)
            assertEquals(plan[i].grade, r.gradeIdx(r.idxAt(atBend)), 1e-9)
        }

        // Tripwire against regressing to a distance-domain layout: the time fraction
        // and distance fraction of interval 3's start genuinely differ here, so any
        // linear time→distance mapping would miss the bend.
        val timeFrac = r.startOf(3) / r.total
        val distFrac = r.distAt(r.startOf(3)) / r.totalMi
        assertTrue(
            "test program too uniform to catch domain mixups",
            kotlin.math.abs(timeFrac - distFrac) > 0.05,
        )
    }

    @Test
    fun `short routes get a switchback sweep floor`() {
        // A 2-minute nearly-flat program accumulates almost no natural phase; the
        // floor guarantees at least ~one full S-curve so it doesn't draw as a stub.
        val tiny = RidgelineRoute(
            listOf(
                RouteInterval(grade = 0.0, speed = 2.0, durSec = 10.0),
                RouteInterval(grade = 1.0, speed = 9.0, durSec = 25.0),
                RouteInterval(grade = 0.5, speed = 1.0, durSec = 75.0),
                RouteInterval(grade = 0.0, speed = 1.0, durSec = 10.0),
            ),
        )
        assertTrue(
            "total phase ${tiny.phaseAt(tiny.total)} < floor",
            tiny.phaseAt(tiny.total) >= RidgelineRoute.MIN_TOTAL_PHASE - 1e-6,
        )
        // Long routes are untouched by the floor (natural phase already large).
        assertTrue(route.phaseAt(route.total) > RidgelineRoute.MIN_TOTAL_PHASE)
    }
}
