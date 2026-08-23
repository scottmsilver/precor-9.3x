package com.precor.treadmill.ui.viewmodel

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Guards the single-use Gemini ephemeral-token reconnect contract. */
class VoiceReconnectGuardTest {
    private val source = File(
        "src/main/java/com/precor/treadmill/ui/viewmodel/VoiceViewModel.kt",
    ).readText()
    private val connectBody = source
        .substringAfter("private fun connectBackground()")
        .substringBefore("/** Callbacks for the always-on background connection. */")

    @Test
    fun everyConnectionClosesItsPredecessorAndFetchesAFreshToken() {
        val disconnect = connectBody.indexOf("geminiClient?.disconnect()")
        val fetch = connectBody.indexOf("api.getConfig()")
        val create = connectBody.indexOf("GeminiLiveClient(")

        assertTrue("superseded Gemini client must be disconnected", disconnect >= 0)
        assertTrue("config token must be fetched for every connection", fetch >= 0)
        assertTrue("disconnect and token fetch must happen before client creation", disconnect < create && fetch < create)
        assertFalse("single-use tokens must never be guarded by a config cache", connectBody.contains("if (config == null)"))
    }
}
