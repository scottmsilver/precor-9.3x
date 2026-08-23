package com.precor.treadmill.ui.screens.running

import androidx.compose.ui.geometry.Rect
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RidgelineTransitionGroupsTest {

    private fun routeWithStarts(count: Int, spacing: Double = 1.0): RidgelineRoute =
        RidgelineRoute(List(count) { RouteInterval(0.0, 3.0, spacing) })

    private fun routeWithBoundaryKeys(vararg starts: Double): RidgelineRoute {
        require(starts.isNotEmpty() && starts.first() == 0.0)
        require(starts.asList().zipWithNext().all { (first, second) -> second > first })
        val durations = starts.indices.map { index ->
            if (index < starts.lastIndex) starts[index + 1] - starts[index] else 10.0
        }
        return RidgelineRoute(durations.map { RouteInterval(0.0, 3.0, it) })
    }

    @Test
    fun `64 boundaries stay exact and 65 aggregate`() {
        val exact = collectTransitionBoundaryGroups(routeWithStarts(64), 0.0, 64.0)
        assertEquals(64, exact.size)
        assertTrue(exact.all { it.count == 1 })

        val dense = collectTransitionBoundaryGroups(routeWithStarts(65), 0.0, 65.0)
        assertTrue(dense.size <= 32)
        assertEquals(65, dense.sumOf { it.count })
        assertTrue(dense.fold(0) { total, group ->
            total + if (group.count == 1) 1 else 2
        } <= 64)
        assertTrue(dense.any(TransitionBoundaryGroup::aggregate))
    }

    @Test
    fun `groups cover visible indices once at every edge`() {
        val grouped = collectTransitionBoundaryGroups(
            route = routeWithBoundaryKeys(0.0, 10.0, 20.0, 30.0, 40.0),
            qLo = 10.0,
            ew = 20.0,
            exactLimit = 2,
            bucketCount = 2,
        )

        assertEquals(listOf(1, 2, 3), grouped.flatMap { it.firstIndex until it.endExclusive })
        assertEquals(1, grouped.first().firstIndex)
        assertEquals(3, grouped.last().lastIndex)
        assertTrue(grouped.zipWithNext().all { (first, second) ->
            first.endExclusive == second.firstIndex
        })
    }

    @Test
    fun `route start is included and route end is excluded`() {
        val route = routeWithBoundaryKeys(0.0, 10.0, 20.0)

        val groups = collectTransitionBoundaryGroups(route, qLo = 0.0, ew = route.total)

        assertEquals(listOf(0, 1, 2), groups.map { it.firstIndex })
        assertFalse(groups.any { it.endExclusive > route.count })
    }

    @Test
    fun `viewport lower and upper boundaries are included`() {
        val route = routeWithBoundaryKeys(0.0, 10.0, 20.0, 30.0)

        val groups = collectTransitionBoundaryGroups(route, qLo = 10.0, ew = 10.0)

        assertEquals(listOf(1, 2), groups.map { it.firstIndex })
    }

    @Test
    fun `boundary on internal bucket edge belongs to later bucket`() {
        val groups = collectTransitionBoundaryGroups(
            route = routeWithBoundaryKeys(0.0, 5.0, 10.0, 15.0, 20.0),
            qLo = 0.0,
            ew = 20.0,
            exactLimit = 2,
            bucketCount = 2,
        )

        assertEquals(
            listOf(0 until 2, 2 until 5),
            groups.map { it.firstIndex until it.endExclusive },
        )
    }

    @Test
    fun `forced dense grouping may produce all singleton buckets`() {
        val groups = collectTransitionBoundaryGroups(
            route = routeWithStarts(4),
            qLo = 0.0,
            ew = 4.0,
            exactLimit = 2,
            bucketCount = 4,
        )

        assertEquals(listOf(0, 1, 2, 3), groups.map { it.firstIndex })
        assertTrue(groups.all { it.count == 1 && !it.aggregate })
    }

    @Test
    fun `empty buckets are omitted while singleton and aggregate buckets remain`() {
        val groups = collectTransitionBoundaryGroups(
            route = routeWithBoundaryKeys(0.0, 0.1, 2.2, 2.3, 2.4, 3.5),
            qLo = 0.0,
            ew = 4.0,
            exactLimit = 2,
            bucketCount = 4,
        )

        assertEquals(
            listOf(0 until 2, 2 until 5, 5 until 6),
            groups.map { it.firstIndex until it.endExclusive },
        )
        assertEquals(listOf(true, true, false), groups.map { it.aggregate })
    }

    @Test
    fun `million transition grouping uses constant boundary searches`() {
        val route = routeWithStarts(1_000_000, spacing = 0.001)
        val stats = TransitionGroupingStats()

        val groups = collectTransitionBoundaryGroups(
            route = route,
            qLo = 0.0,
            ew = route.total,
            stats = stats,
        )

        assertTrue(groups.size <= 32)
        assertEquals(1_000_000, groups.sumOf { it.count })
        assertTrue(stats.boundarySearches <= 34)
        assertTrue(groups.zipWithNext().all { (first, second) ->
            first.endExclusive == second.firstIndex
        })
    }

    @Test
    fun `non-grid lower bound is used without rounding or clamping`() {
        val route = routeWithBoundaryKeys(0.0, 10.124, 10.126, 20.0)

        val groups = collectTransitionBoundaryGroups(
            route = route,
            qLo = 10.125,
            ew = 0.001,
            exactLimit = 2,
            bucketCount = 2,
        )

        assertEquals(listOf(2), groups.map { it.firstIndex })
    }

    @Test(expected = IllegalArgumentException::class)
    fun `window extent must be positive`() {
        collectTransitionBoundaryGroups(routeWithStarts(3), 0.0, 0.0)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `window extent must be finite`() {
        collectTransitionBoundaryGroups(routeWithStarts(3), 0.0, Double.POSITIVE_INFINITY)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `exact limit must leave room for bookends`() {
        collectTransitionBoundaryGroups(routeWithStarts(3), 0.0, 3.0, exactLimit = 1)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `bucket count must be positive`() {
        collectTransitionBoundaryGroups(routeWithStarts(3), 0.0, 3.0, bucketCount = 0)
    }

    @Test
    fun `transition counts use compact bounded suffixes`() {
        assertEquals("×2", formatTransitionCount(2))
        assertEquals("×999", formatTransitionCount(999))
        assertEquals("×1.0k", formatTransitionCount(1_000))
        assertEquals("×1.2M", formatTransitionCount(1_200_000))
        assertEquals("×2.1B", formatTransitionCount(Int.MAX_VALUE))
    }

    @Test
    fun `transition counts promote before rounded suffix reaches one thousand`() {
        assertEquals("×999.9k", formatTransitionCount(999_949))
        assertEquals("×1.0M", formatTransitionCount(999_950))
        assertEquals("×999.9M", formatTransitionCount(999_949_999))
        assertEquals("×1.0B", formatTransitionCount(999_950_000))
    }

    @Test(expected = IllegalArgumentException::class)
    fun `transition count requires an aggregate`() {
        formatTransitionCount(1)
    }

    @Test
    fun `bookend bracket joins the placed pill edges`() {
        val first = Rect(40f, 300f, 150f, 324f)
        val last = Rect(500f, 80f, 640f, 104f)

        val segments = placedBookendBracket(first, last, centerX = 320f)

        assertEquals(3, segments.size)
        assertEquals(first.right, segments.first().start.x, 0f)
        assertEquals(first.center.y, segments.first().start.y, 0f)
        assertEquals(last.left, segments.last().end.x, 0f)
        assertEquals(last.center.y, segments.last().end.y, 0f)
    }
}
