package com.precor.treadmill.ui.screens.running

import android.view.MotionEvent
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression: the stepper zones' press tracking drives REAL belt commands via
 * hold-to-repeat, so the press state must clear on EVERY way a finger can stop
 * pressing. Codex review found repeats survived ACTION_POINTER_UP (a second
 * finger down while the first lifts) and DOWN was accepted while disabled.
 */
class StepperPressStateTest {

    @Test
    fun `down starts a press only when enabled`() {
        assertTrue(pressAfterMotionEvent(current = false, actionMasked = MotionEvent.ACTION_DOWN, enabled = true))
        assertFalse(pressAfterMotionEvent(current = false, actionMasked = MotionEvent.ACTION_DOWN, enabled = false))
    }

    @Test
    fun `up and cancel always clear the press`() {
        assertFalse(pressAfterMotionEvent(current = true, actionMasked = MotionEvent.ACTION_UP, enabled = true))
        assertFalse(pressAfterMotionEvent(current = true, actionMasked = MotionEvent.ACTION_CANCEL, enabled = true))
        // ...even if the control was disabled mid-hold
        assertFalse(pressAfterMotionEvent(current = true, actionMasked = MotionEvent.ACTION_UP, enabled = false))
    }

    @Test
    fun `pointer-up (multi-touch) clears the press`() {
        // Second finger resting on screen, holding finger lifts -> ACTION_POINTER_UP.
        // Must stop the repeat: continuing would keep driving the belt with no finger
        // on the button.
        assertFalse(pressAfterMotionEvent(current = true, actionMasked = MotionEvent.ACTION_POINTER_UP, enabled = true))
    }

    @Test
    fun `move events do not change the press state`() {
        assertTrue(pressAfterMotionEvent(current = true, actionMasked = MotionEvent.ACTION_MOVE, enabled = true))
        assertFalse(pressAfterMotionEvent(current = false, actionMasked = MotionEvent.ACTION_MOVE, enabled = true))
    }
}
