package com.precor.treadmill.ui.screens.running

import org.junit.Assert.assertEquals
import org.junit.Test

class RunningTimerTest {
    @Test
    fun `timer defaults to count down`() {
        assertEquals(
            RunningTimer(mode = TimerMode.COUNT_DOWN, text = "8:30"),
            runningTimer(
                countUpElapsedSeconds = 12.0,
                programElapsedSeconds = 90.0,
                totalDurationSeconds = 600.0,
            ),
        )
    }

    @Test
    fun `count up mode shows elapsed time`() {
        assertEquals(
            RunningTimer(mode = TimerMode.COUNT_UP, text = "1:30"),
            runningTimer(
                countUpElapsedSeconds = 90.0,
                programElapsedSeconds = 300.0,
                totalDurationSeconds = 600.0,
                mode = TimerMode.COUNT_UP,
            ),
        )
    }

    @Test
    fun `count down clamps at zero after duration`() {
        assertEquals(
            RunningTimer(mode = TimerMode.COUNT_DOWN, text = "0:00"),
            runningTimer(
                countUpElapsedSeconds = 601.0,
                programElapsedSeconds = 601.0,
                totalDurationSeconds = 600.0,
            ),
        )
    }

    @Test
    fun `count down rounds a positive fractional remainder up`() {
        assertEquals(
            RunningTimer(mode = TimerMode.COUNT_DOWN, text = "10:00"),
            runningTimer(
                countUpElapsedSeconds = 0.1,
                programElapsedSeconds = 0.1,
                totalDurationSeconds = 600.0,
            ),
        )
    }

    @Test
    fun `timer resets for a new program but survives pause and resume`() {
        assertEquals(
            TimerMode.COUNT_UP,
            timerModeForProgramTransition(
                wasRunning = true,
                isRunning = true,
                currentMode = TimerMode.COUNT_UP,
            ),
        )
        assertEquals(
            TimerMode.COUNT_DOWN,
            timerModeForProgramTransition(
                wasRunning = false,
                isRunning = true,
                currentMode = TimerMode.COUNT_UP,
            ),
        )
    }

    @Test
    fun `paused and completed countdowns use exact server position`() {
        assertEquals(
            599.25,
            countdownProgramPosition(
                advancing = false,
                completed = false,
                serverPosition = 599.25,
                animatedPosition = 598.9,
            ),
            0.0,
        )
        assertEquals(
            600.0,
            countdownProgramPosition(
                advancing = false,
                completed = true,
                serverPosition = 600.0,
                animatedPosition = 599.9,
            ),
            0.0,
        )
    }
}
