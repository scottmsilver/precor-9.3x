package com.precor.treadmill

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Guards wake-word microphone ownership across Activity lifecycle changes. */
class WakeWordLifecycleGuardTest {
    private val source = File(
        "src/main/java/com/precor/treadmill/MainActivity.kt",
    ).readText()

    @Test
    fun detectorStartsInForegroundAndStopsInBackground() {
        val onCreate = source.substringAfter("override fun onCreate(").substringBefore("override fun onNewIntent(")
        val onResume = source.substringAfter("override fun onResume()").substringBefore("override fun onPause()")
        val onPause = source.substringAfter("override fun onPause()").substringBefore("override fun onDestroy()")

        assertFalse("onCreate must not permanently skip initialization before permission is granted", onCreate.contains("startWakeWordPrototype()"))
        assertTrue(onResume.contains("wakeWordForeground = true"))
        assertTrue(onResume.contains("startWakeWordPrototype()"))
        assertTrue(onPause.contains("wakeWordForeground = false"))
        assertTrue(onPause.contains("wakeWordEngine?.stop()"))
        assertTrue(source.contains("state == VoiceState.IDLE && wakeWordForeground"))
        assertTrue("prototype threshold must reject observed false wakes", source.contains("threshold = 0.70f"))
    }
}
