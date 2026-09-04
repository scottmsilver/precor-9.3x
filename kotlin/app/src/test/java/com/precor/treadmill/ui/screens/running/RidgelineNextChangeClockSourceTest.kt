package com.precor.treadmill.ui.screens.running

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Guards the clock domains and accessibility metadata passed into the next-change row. */
class RidgelineNextChangeClockSourceTest {
    private val hudSource = File(
        "src/main/java/com/precor/treadmill/ui/screens/running/RidgelineHud.kt",
    ).readText()
    private val viewModelSource = File(
        "src/main/java/com/precor/treadmill/ui/viewmodel/TreadmillViewModel.kt",
    ).readText()
    private val nextChangeCall = hudSource
        .substringAfter(") formatNextChange(")
        .substringBefore(") else null")

    @Test
    fun `next change receives the interpolated session clock and program countdown sources`() {
        assertTrue(viewModelSource.contains("val displayElapsed: Double"))
        assertTrue(viewModelSource.contains("displayElapsed = displayElapsed"))
        assertTrue(nextChangeCall.contains("sessionElapsed = sess.displayElapsed"))
        assertFalse(nextChangeCall.contains("sessionElapsed = sess.elapsed,"))
        assertTrue(nextChangeCall.contains("programElapsed = timerProgramPosition"))
        assertFalse(nextChangeCall.contains("programElapsed = pgm.totalElapsed"))
        assertTrue(nextChangeCall.contains("programDuration = pgm.totalDuration"))
        assertTrue(nextChangeCall.contains("nextChangeProgramPosition = route.endOf(pgm.currentInterval)"))
        assertFalse(nextChangeCall.contains("route.posAtProgram(pgm.currentInterval, pgm.intervalElapsed)"))
        assertTrue(nextChangeCall.contains("timeMark = timerMode.nextChangeTimeMark()"))
    }

    @Test
    fun `next change row exposes its semantic description`() {
        assertTrue(hudSource.contains("contentDescription = next.accessibilityDescription"))
    }
}
