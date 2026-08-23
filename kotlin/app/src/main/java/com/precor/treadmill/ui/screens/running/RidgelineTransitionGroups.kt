package com.precor.treadmill.ui.screens.running

import java.util.Locale
import kotlin.math.min

internal data class TransitionBoundaryGroup(
    val firstIndex: Int,
    val endExclusive: Int,
) {
    init {
        require(firstIndex >= 0)
        require(endExclusive > firstIndex)
    }

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
    require(ew.isFinite() && ew > 0.0) { "Window extent must be finite and positive" }
    require(exactLimit >= 2) { "Exact limit must leave room for aggregate bookends" }
    require(bucketCount >= 1) { "Bucket count must be positive" }

    fun firstBoundaryAtOrAfter(time: Double): Int {
        stats?.let { it.boundarySearches++ }
        return route.firstBoundaryAtOrAfter(time)
    }

    fun firstBoundaryAfter(time: Double): Int {
        stats?.let { it.boundarySearches++ }
        return route.firstBoundaryAfter(time)
    }

    val qHi = min(qLo + ew, route.total)
    val visibleFirst = firstBoundaryAtOrAfter(qLo)
    val visibleEnd = firstBoundaryAfter(qHi)
    val visibleCount = visibleEnd - visibleFirst
    if (visibleCount <= exactLimit) {
        return (visibleFirst until visibleEnd).map { TransitionBoundaryGroup(it, it + 1) }
    }

    val out = ArrayList<TransitionBoundaryGroup>(bucketCount)
    var first = visibleFirst
    for (bucket in 0 until bucketCount) {
        val end = if (bucket == bucketCount - 1) {
            visibleEnd
        } else {
            val bucketEnd = qLo + (bucket + 1).toDouble() * ew / bucketCount
            firstBoundaryAtOrAfter(bucketEnd).coerceIn(first, visibleEnd)
        }
        if (end > first) out += TransitionBoundaryGroup(first, end)
        first = end
    }
    check(first == visibleEnd)
    return out
}

internal fun formatTransitionCount(count: Int): String {
    require(count > 1) { "Transition count is only shown for aggregate groups" }
    return when {
        count < 1_000 -> "×$count"
        count < 999_950 -> "×%.1fk".format(Locale.US, count / 1_000.0)
        count < 999_950_000 -> "×%.1fM".format(Locale.US, count / 1_000_000.0)
        else -> "×%.1fB".format(Locale.US, count / 1_000_000_000.0)
    }
}
