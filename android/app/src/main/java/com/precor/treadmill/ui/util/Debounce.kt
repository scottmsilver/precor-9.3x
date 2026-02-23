package com.precor.treadmill.ui.util

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Trailing debounce: coalesces rapid calls, fires once after [delayMs] of quiet.
 * Returns a function that, when called, schedules the action after the delay.
 * Subsequent calls within the delay window cancel the previous scheduled action.
 */
class TrailingDebounce<T>(
    private val delayMs: Long,
    private val scope: CoroutineScope,
    private val action: suspend (T) -> Unit,
) {
    private var job: Job? = null

    fun invoke(value: T) {
        job?.cancel()
        job = scope.launch {
            delay(delayMs)
            action(value)
        }
    }
}

/**
 * Leading-edge guard: fires immediately, then ignores calls for [delayMs].
 * Useful for preventing double-taps on action buttons.
 */
class LeadingGuard(
    private val delayMs: Long,
) {
    private var blockedUntil = 0L

    fun tryExecute(action: () -> Unit): Boolean {
        val now = System.currentTimeMillis()
        if (now < blockedUntil) return false
        blockedUntil = now + delayMs
        action()
        return true
    }

    suspend fun tryExecuteSuspend(action: suspend () -> Unit): Boolean {
        val now = System.currentTimeMillis()
        if (now < blockedUntil) return false
        blockedUntil = now + delayMs
        action()
        return true
    }
}
