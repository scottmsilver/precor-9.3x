package com.precor.treadmill.ui.screens.running

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RidgelineViewportLensTest {

    @Test
    fun lensUsesTheExistingViewportAndStripGeometry() {
        val lens = minimapViewportLens(
            leaderX = 701f,
            vTop = 112f,
            vBot = 158f,
            stripW = 12f,
        )

        assertEquals(701f, lens.left, 0f)
        assertEquals(112f, lens.top, 0f)
        assertEquals(24f, lens.width, 0f)
        assertEquals(46f, lens.height, 0f)
        assertEquals(7f, lens.radius, 0f)
    }

    @Test
    fun lensKeepsADegenerateViewportVisible() {
        val lens = minimapViewportLens(
            leaderX = 701f,
            vTop = 158f,
            vBot = 112f,
            stripW = 12f,
        )

        assertEquals(10f, lens.height, 0f)
    }

    @Test
    fun lensSanitizesNonFiniteDrawInputs() {
        val lens = minimapViewportLens(
            leaderX = Float.NaN,
            vTop = Float.POSITIVE_INFINITY,
            vBot = Float.NEGATIVE_INFINITY,
            stripW = Float.NaN,
        )

        assertTrue(lens.left.isFinite())
        assertTrue(lens.top.isFinite())
        assertTrue(lens.width.isFinite() && lens.width >= 0f)
        assertTrue(lens.height.isFinite() && lens.height >= 10f)
        assertTrue(lens.radius.isFinite() && lens.radius >= 0f)
    }

    @Test
    fun lensSanitizesOverflowFromFiniteViewportEdges() {
        val lens = minimapViewportLens(
            leaderX = 0f,
            vTop = -Float.MAX_VALUE,
            vBot = Float.MAX_VALUE,
            stripW = 12f,
        )

        assertTrue(lens.height.isFinite())
        assertEquals(10f, lens.height, 0f)
    }
}
