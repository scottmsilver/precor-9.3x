package com.precor.treadmill.ui.screens.running

import com.precor.treadmill.ui.util.fmtDur
import kotlin.math.ceil

internal enum class TimerMode {
    COUNT_DOWN,
    COUNT_UP;

    fun toggled(): TimerMode = if (this == COUNT_DOWN) COUNT_UP else COUNT_DOWN
}

internal data class RunningTimer(
    val mode: TimerMode,
    val text: String,
) {
    val contentDescription: String
        get() = when (mode) {
            TimerMode.COUNT_DOWN -> "Workout timer, $text remaining. Tap to count up"
            TimerMode.COUNT_UP -> "Workout timer, $text elapsed. Tap to count down"
        }
}

internal fun timerModeForProgramTransition(
    wasRunning: Boolean,
    isRunning: Boolean,
    currentMode: TimerMode,
): TimerMode = if (!wasRunning && isRunning) TimerMode.COUNT_DOWN else currentMode

internal fun countdownProgramPosition(
    advancing: Boolean,
    completed: Boolean,
    serverPosition: Double,
    animatedPosition: Double,
): Double = if (!advancing || completed) serverPosition else animatedPosition

internal fun runningTimer(
    countUpElapsedSeconds: Double,
    programElapsedSeconds: Double,
    totalDurationSeconds: Double,
    mode: TimerMode = TimerMode.COUNT_DOWN,
): RunningTimer {
    val displaySeconds: Number = when (mode) {
        TimerMode.COUNT_DOWN -> ceil(
            (totalDurationSeconds - programElapsedSeconds).coerceAtLeast(0.0),
        ).toInt()
        TimerMode.COUNT_UP -> countUpElapsedSeconds
    }
    return RunningTimer(mode = mode, text = fmtDur(displaySeconds))
}
