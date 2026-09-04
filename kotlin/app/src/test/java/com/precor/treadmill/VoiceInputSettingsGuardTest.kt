package com.precor.treadmill

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/** Guards the persisted voice opt-in across every microphone entry point. */
class VoiceInputSettingsGuardTest {
    private val preferencesSource = File(
        "src/main/java/com/precor/treadmill/data/preferences/ServerPreferences.kt",
    ).readText()
    private val navigationSource = File(
        "src/main/java/com/precor/treadmill/ui/navigation/AppNavigation.kt",
    ).readText()
    private val settingsSource = File(
        "src/main/java/com/precor/treadmill/ui/components/SettingsSheet.kt",
    ).readText()
    private val activitySource = File(
        "src/main/java/com/precor/treadmill/MainActivity.kt",
    ).readText()
    private val voiceViewModelSource = File(
        "src/main/java/com/precor/treadmill/ui/viewmodel/VoiceViewModel.kt",
    ).readText()
    @Test
    fun voiceInputPreferenceIsPersistentAndDefaultsOn() {
        assertTrue(preferencesSource.contains("booleanPreferencesKey(\"voice_input_enabled\")"))
        assertTrue(preferencesSource.contains("prefs[KEY_VOICE_INPUT_ENABLED] ?: true"))
        assertTrue(preferencesSource.contains("suspend fun setVoiceInputEnabled(enabled: Boolean)"))
        assertTrue(preferencesSource.contains("booleanPreferencesKey(\"microphone_permission_requested\")"))
        assertTrue(preferencesSource.contains("suspend fun setMicrophonePermissionRequested(requested: Boolean)"))
    }

    @Test
    fun settingsShowsVoiceOptInAndMicrophonePermissionWithRetry() {
        assertTrue(settingsSource.contains("text = \"Voice Input\""))
        assertTrue(settingsSource.contains("text = \"Microphone Permission\""))
        assertTrue(settingsSource.contains("text = \"Granted\""))
        assertTrue(settingsSource.contains("Text(\"Not granted · Grant\")"))
        assertTrue(settingsSource.contains("onRequestMicrophonePermission"))
        assertTrue(settingsSource.contains(".toggleable("))
        assertTrue(settingsSource.contains("role = Role.Switch"))
    }

    @Test
    fun disabledPreferenceStopsAndGuardsManualVoiceCapture() {
        assertTrue(navigationSource.contains("voiceViewModel.setVoiceInputEnabled(enabled)"))
        assertTrue(navigationSource.contains("if (!voiceInputEnabled) return@handleVoiceToggle"))
        assertTrue(navigationSource.contains("if (!enabled) onVoiceInputDisabled()"))
        assertTrue(voiceViewModelSource.contains("fun setVoiceInputEnabled(enabled: Boolean)"))
        assertTrue(voiceViewModelSource.contains("if (!voiceInputEnabled) return"))
        assertTrue(voiceViewModelSource.contains("teardownConnection()"))
        assertTrue(voiceViewModelSource.contains("gate.runIfActive"))
        assertTrue(voiceViewModelSource.contains("gate.runIfActive(generation) {\n                when (state)"))
        assertTrue(voiceViewModelSource.contains("if (userActivated) {\n                stopMicCapture()"))
        assertTrue(voiceViewModelSource.contains("gate.runIfActive(sessionGeneration) {\n                    geminiClient"))
    }

    @Test
    fun disabledPreferenceStopsAndGuardsWakeWordCapture() {
        assertTrue(activitySource.contains("serverPreferences.voiceInputEnabled.collect"))
        assertTrue(activitySource.contains("if (!enabled) wakeWordEngine?.stop()"))
        assertTrue(activitySource.contains("state == VoiceState.IDLE && wakeWordForeground && voiceInputEnabled"))
        assertTrue(activitySource.contains("if (voiceInputEnabled) startWakeWordPrototype()"))
        assertTrue(activitySource.contains("pendingVoiceToggleIntent"))
        assertTrue(activitySource.contains("onVoiceInputDisabled = ::disableVoiceInputImmediately"))
        val preferenceObserver = activitySource
            .substringAfter("serverPreferences.voiceInputEnabled.collect")
            .substringBefore("private fun handleVoiceTestIntent")
        assertTrue(
            preferenceObserver.indexOf("pendingVoiceToggleIntent = false") in
                0 until preferenceObserver.indexOf("if (!enabled)"),
        )
    }
}
