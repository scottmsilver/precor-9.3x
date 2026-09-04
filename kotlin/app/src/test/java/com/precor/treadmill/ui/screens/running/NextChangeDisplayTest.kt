package com.precor.treadmill.ui.screens.running

import org.junit.Assert.assertEquals
import org.junit.Test

class NextChangeDisplayTest {
    @Test
    fun `timer mode selects the matching next-change mark`() {
        assertEquals(WorkoutTimeMark.ELAPSED, TimerMode.COUNT_UP.nextChangeTimeMark())
        assertEquals(WorkoutTimeMark.REMAINING, TimerMode.COUNT_DOWN.nextChangeTimeMark())
    }

    @Test
    fun `elapsed mark is the session timer reading when the interval changes`() {
        val display = formatNextChange(
            nextChangeProgramPosition = 993.1,
            clock = NextChangeClock(
                sessionElapsed = 709.2,
                programElapsed = 709.2,
                programDuration = 1_800.0,
            ),
            timeMark = WorkoutTimeMark.ELAPSED,
        )

        assertEquals("4:43 (16:33)", display.text)
        assertEquals(
            "Next change in 4:43; workout elapsed at change 16:33",
            display.accessibilityDescription,
        )
    }

    @Test
    fun `resumed program elapsed does not leak into the session count-up mark`() {
        val display = formatNextChange(
            nextChangeProgramPosition = 660.0,
            clock = NextChangeClock(
                sessionElapsed = 10.0,
                programElapsed = 610.0,
                programDuration = 1_800.0,
            ),
            timeMark = WorkoutTimeMark.ELAPSED,
        )

        assertEquals("0:50 (1:00)", display.text)
    }

    @Test
    fun `countdown mark follows program progress rather than session count-up`() {
        val display = formatNextChange(
            nextChangeProgramPosition = 360.0,
            clock = NextChangeClock(
                sessionElapsed = 900.0,
                programElapsed = 300.0,
                programDuration = 1_800.0,
            ),
            timeMark = WorkoutTimeMark.REMAINING,
        )

        assertEquals("1:00 (24:00)", display.text)
        assertEquals(
            "Next change in 1:00; workout remaining at change 24:00",
            display.accessibilityDescription,
        )
    }

    @Test
    fun `clock model keeps Previous-style divergent timer sources distinct`() {
        val clock = NextChangeClock(
            sessionElapsed = 900.0,
            programElapsed = 300.0,
            programDuration = 1_800.0,
        )

        assertEquals(960.0, clock.atChange(360.0, WorkoutTimeMark.ELAPSED), 0.0)
        assertEquals(1_440.0, clock.atChange(360.0, WorkoutTimeMark.REMAINING), 0.0)
    }

    @Test
    fun `advancing matched clocks keep boundary marks stable between server ticks`() {
        val staleServerSecondsUntilChange = 60.0
        val boundary = 200.0 + staleServerSecondsUntilChange
        val before = NextChangeClock(
            sessionElapsed = 100.0,
            programElapsed = 200.0,
            programDuration = 600.0,
        )
        val oneSecondLater = NextChangeClock(
            sessionElapsed = 101.0,
            programElapsed = 201.0,
            programDuration = 600.0,
        )

        assertEquals(
            "1:00 (2:40)",
            formatNextChange(boundary, before, WorkoutTimeMark.ELAPSED).text,
        )
        assertEquals(
            "0:59 (2:40)",
            formatNextChange(boundary, oneSecondLater, WorkoutTimeMark.ELAPSED).text,
        )
        assertEquals(
            "1:00 (5:40)",
            formatNextChange(boundary, before, WorkoutTimeMark.REMAINING).text,
        )
        assertEquals(
            "0:59 (5:40)",
            formatNextChange(boundary, oneSecondLater, WorkoutTimeMark.REMAINING).text,
        )
    }

    @Test
    fun `fractional ticks are combined before formatting whole seconds`() {
        val display = formatNextChange(
            nextChangeProgramPosition = 60.1,
            clock = NextChangeClock(
                sessionElapsed = 0.2,
                programElapsed = 0.2,
                programDuration = 120.0,
            ),
            timeMark = WorkoutTimeMark.ELAPSED,
        )

        assertEquals("0:59 (1:00)", display.text)
    }

    @Test
    fun `times clamp at workout boundaries`() {
        assertEquals(
            "0:00 (0:00)",
            formatNextChange(
                nextChangeProgramPosition = 1_799.4,
                clock = NextChangeClock(
                    sessionElapsed = 1_800.4,
                    programElapsed = 1_800.4,
                    programDuration = 1_800.0,
                ),
                timeMark = WorkoutTimeMark.REMAINING,
            ).text,
        )
        assertEquals(
            "0:00 (0:00)",
            formatNextChange(
                nextChangeProgramPosition = -1.4,
                clock = NextChangeClock(
                    sessionElapsed = -0.4,
                    programElapsed = -0.4,
                    programDuration = 1_800.0,
                ),
                timeMark = WorkoutTimeMark.ELAPSED,
            ).text,
        )
    }
}
