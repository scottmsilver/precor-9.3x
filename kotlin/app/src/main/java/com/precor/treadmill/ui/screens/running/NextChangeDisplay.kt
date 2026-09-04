package com.precor.treadmill.ui.screens.running

import com.precor.treadmill.ui.util.fmtDur

/** Which workout-clock reading to show at the upcoming interval boundary. */
internal enum class WorkoutTimeMark {
    ELAPSED,
    REMAINING,
}

internal fun TimerMode.nextChangeTimeMark(): WorkoutTimeMark = when (this) {
    TimerMode.COUNT_UP -> WorkoutTimeMark.ELAPSED
    TimerMode.COUNT_DOWN -> WorkoutTimeMark.REMAINING
}

/** The independent clock sources used by the running timer's two display modes. */
internal data class NextChangeClock(
    val sessionElapsed: Double,
    val programElapsed: Double,
    val programDuration: Double,
) {
    fun secondsUntil(nextChangeProgramPosition: Double): Double =
        (nextChangeProgramPosition - programElapsed).coerceAtLeast(0.0)

    fun atChange(nextChangeProgramPosition: Double, timeMark: WorkoutTimeMark): Double {
        val timeLeft = secondsUntil(nextChangeProgramPosition)
        return when (timeMark) {
            WorkoutTimeMark.ELAPSED -> sessionElapsed.coerceAtLeast(0.0) + timeLeft
            WorkoutTimeMark.REMAINING -> (
                programDuration.coerceAtLeast(0.0) -
                    programElapsed.coerceAtLeast(0.0) -
                    timeLeft
                ).coerceAtLeast(0.0)
        }
    }
}

/** Visual and screen-reader representations of a next-change clock pair. */
internal data class NextChangeDisplay(
    val text: String,
    val accessibilityDescription: String,
)

/**
 * Format the time until the next interval boundary and the workout-clock reading at that point.
 * Arithmetic stays in fractional seconds until both values are independently formatted.
 */
internal fun formatNextChange(
    nextChangeProgramPosition: Double,
    clock: NextChangeClock,
    timeMark: WorkoutTimeMark,
): NextChangeDisplay {
    val timeLeft = clock.secondsUntil(nextChangeProgramPosition)
    val timeLeftText = fmtDur(timeLeft)
    val timerAtChangeText = fmtDur(clock.atChange(nextChangeProgramPosition, timeMark))
    val markLabel = when (timeMark) {
        WorkoutTimeMark.ELAPSED -> "elapsed"
        WorkoutTimeMark.REMAINING -> "remaining"
    }
    return NextChangeDisplay(
        text = "$timeLeftText ($timerAtChangeText)",
        accessibilityDescription =
            "Next change in $timeLeftText; workout $markLabel at change $timerAtChangeText",
    )
}
