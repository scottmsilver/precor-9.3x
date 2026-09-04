package com.precor.treadmill

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.ui.navigation.AppNavigation
import com.precor.treadmill.ui.theme.PrecorTreadmillTheme
import com.precor.treadmill.ui.viewmodel.VoiceViewModel
import com.precor.treadmill.ui.viewmodel.VoiceState
import com.rementia.openwakeword.lib.WakeWordEngine
import com.rementia.openwakeword.lib.model.WakeWordModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.koin.androidx.viewmodel.ext.android.viewModel
import org.koin.android.ext.android.inject

class MainActivity : ComponentActivity() {
    companion object {
        private const val TAG = "MainActivity"
        const val ACTION_VOICE_TEST = "com.precor.treadmill.VOICE_TEST"
        const val ACTION_VOICE_TOGGLE = "com.precor.treadmill.VOICE_TOGGLE"
    }

    private val voiceViewModel: VoiceViewModel by viewModel()
    private val serverPreferences: ServerPreferences by inject()
    private var wakeWordEngine: WakeWordEngine? = null
    private var wakeWordDetectionJob: Job? = null
    private var wakeWordStateJob: Job? = null
    private var wakeWordForeground = false
    private var voiceInputEnabled = false
    private var voiceInputPreferenceLoaded = false
    private var pendingVoiceToggleIntent = false
    private val wakeWordActivationPolicy = WakeWordActivationPolicy()

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        // Hide system navigation bar — sticky immersive so it auto-hides after swipe
        val insetsController = WindowCompat.getInsetsController(window, window.decorView)
        insetsController.hide(WindowInsetsCompat.Type.systemBars())
        insetsController.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE

        setContent {
            PrecorTreadmillTheme {
                AppNavigation(onVoiceInputDisabled = ::disableVoiceInputImmediately)
            }
        }

        observeVoiceInputPreference()

        handleVoiceTestIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleVoiceTestIntent(intent)
    }

    private fun observeVoiceInputPreference() {
        lifecycleScope.launch {
            serverPreferences.voiceInputEnabled.collect { enabled ->
                voiceInputEnabled = enabled
                voiceInputPreferenceLoaded = true
                val activatePendingVoice = pendingVoiceToggleIntent
                pendingVoiceToggleIntent = false
                voiceViewModel.setVoiceInputEnabled(enabled)
                if (!enabled) wakeWordEngine?.stop()
                else {
                    if (activatePendingVoice) voiceViewModel.toggle()
                    if (wakeWordForeground) startWakeWordPrototype()
                }
            }
        }
    }

    private fun disableVoiceInputImmediately() {
        voiceInputEnabled = false
        pendingVoiceToggleIntent = false
        wakeWordEngine?.stop()
    }

    private fun handleVoiceTestIntent(intent: Intent) {
        when (intent.action) {
            ACTION_VOICE_TEST -> {
                val cmd = intent.getStringExtra("cmd") ?: return
                Log.d(TAG, "Voice test command: $cmd")
                voiceViewModel.sendTestCommand(cmd)
            }
            ACTION_VOICE_TOGGLE -> {
                Log.d(TAG, "Voice toggle (mic mode)")
                if (!voiceInputPreferenceLoaded) pendingVoiceToggleIntent = true
                else if (voiceInputEnabled) voiceViewModel.toggle()
            }
            else -> return
        }
    }

    /**
     * OpenWakeWord integration using the wrapper's AudioRecord and the bundled
     * full-precision Hey Treddy classifier. Voice activation recreates AudioCapture
     * after the detector releases the microphone.
     */
    private fun startWakeWordPrototype() {
        if (!voiceInputEnabled) return
        wakeWordEngine?.let { engine ->
            if (voiceViewModel.voiceState.value == VoiceState.IDLE) {
                startWakeWordListening(engine)
            }
            return
        }

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            Log.w(TAG, "Wake-word prototype disabled: RECORD_AUDIO not granted")
            return
        }

        try {
            val engine = WakeWordEngine(
                context = applicationContext,
                models = listOf(
                    WakeWordModel(
                        name = "Hey Treddy",
                        modelPath = "hey_treddy.onnx",
                        threshold = WakeWordActivationPolicy.MINIMUM_SCORE,
                    )
                ),
                detectionCooldownMs = 3_000L,
            )
            wakeWordEngine = engine

            wakeWordDetectionJob = lifecycleScope.launch {
                engine.detections.collect { detection ->
                    if (!wakeWordActivationPolicy.shouldActivate(
                            score = detection.score,
                            nowMs = SystemClock.elapsedRealtime(),
                        )
                    ) {
                        Log.d(TAG, "WAKE_WORD_SUPPRESSED score=${detection.score}")
                        return@collect
                    }
                    Log.i(
                        TAG,
                        "WAKE_WORD_DETECTED name=${detection.model.name} score=${detection.score}",
                    )
                    // Give the wrapper's AudioRecord time to release before Gemini's
                    // existing AudioCapture claims the microphone.
                    engine.stop()
                    delay(300)
                    if (
                        voiceInputEnabled && wakeWordForeground &&
                        voiceViewModel.voiceState.value == VoiceState.IDLE
                    ) {
                        voiceViewModel.activateAfterWakeWord()
                    }
                }
            }

            wakeWordStateJob = lifecycleScope.launch {
                voiceViewModel.voiceState.collect { state ->
                    if (state == VoiceState.IDLE && wakeWordForeground && voiceInputEnabled) {
                        Log.i(TAG, "WAKE_WORD_LISTENING phrase=hey_treddy")
                        startWakeWordListening(engine)
                    } else {
                        engine.stop()
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Wake-word prototype failed to initialize", e)
        }
    }

    private fun startWakeWordListening(engine: WakeWordEngine) {
        wakeWordActivationPolicy.onListeningStarted(SystemClock.elapsedRealtime())
        engine.start()
    }

    override fun onResume() {
        super.onResume()
        wakeWordForeground = true
        if (voiceInputEnabled) startWakeWordPrototype()
    }

    override fun onPause() {
        wakeWordForeground = false
        wakeWordEngine?.stop()
        super.onPause()
    }

    override fun onDestroy() {
        wakeWordDetectionJob?.cancel()
        wakeWordStateJob?.cancel()
        wakeWordEngine?.release()
        super.onDestroy()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            // Re-hide nav bar when focus returns (e.g. after dialog or app switch)
            val insetsController = WindowCompat.getInsetsController(window, window.decorView)
            insetsController.hide(WindowInsetsCompat.Type.systemBars())
            insetsController.systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }
}
