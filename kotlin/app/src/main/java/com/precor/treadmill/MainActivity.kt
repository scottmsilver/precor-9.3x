package com.precor.treadmill

import android.Manifest
import android.content.pm.PackageManager
import android.content.Intent
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.util.Log
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
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
import java.util.Locale

class MainActivity : ComponentActivity() {
    companion object {
        private const val TAG = "MainActivity"
        const val ACTION_VOICE_TEST = "com.precor.treadmill.VOICE_TEST"
        const val ACTION_VOICE_TOGGLE = "com.precor.treadmill.VOICE_TOGGLE"
        const val ACTION_WAKE_WORD_SELF_TEST = "com.precor.treadmill.WAKE_WORD_SELF_TEST"
    }

    private val voiceViewModel: VoiceViewModel by viewModel()
    private var wakeWordEngine: WakeWordEngine? = null
    private var wakeWordDetectionJob: Job? = null
    private var wakeWordStateJob: Job? = null
    private var wakeTestTts: TextToSpeech? = null

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
                AppNavigation()
            }
        }

        handleVoiceTestIntent(intent)
        startWakeWordPrototype()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleVoiceTestIntent(intent)
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
                voiceViewModel.toggle()
            }
            ACTION_WAKE_WORD_SELF_TEST -> playWakeWordSelfTest()
            else -> return
        }
    }

    private fun playWakeWordSelfTest() {
        Log.i(TAG, "WAKE_WORD_SELF_TEST phrase=hello_world")
        wakeTestTts?.shutdown()
        wakeTestTts = TextToSpeech(applicationContext) { status ->
            if (status != TextToSpeech.SUCCESS) {
                Log.e(TAG, "Wake-word self-test TTS failed to initialize: $status")
                return@TextToSpeech
            }
            wakeTestTts?.language = Locale.US
            wakeTestTts?.speak(
                "hello world",
                TextToSpeech.QUEUE_FLUSH,
                null,
                "wake-word-self-test",
            )
        }
    }

    /**
     * Throwaway openWakeWord integration spike. It deliberately uses the wrapper's
     * own AudioRecord and the bundled "hello world" demo model. If this proves
     * reliable on the treadmill tablet, the production pass will feed the existing
     * AudioCapture stream into the detector and replace the classifier with Hey Treddy.
     */
    private fun startWakeWordPrototype() {
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
                        name = "Hello World",
                        modelPath = "hello_world.onnx",
                        threshold = 0.03f,
                    )
                ),
                detectionCooldownMs = 3_000L,
            )
            wakeWordEngine = engine

            wakeWordDetectionJob = lifecycleScope.launch {
                engine.detections.collect { detection ->
                    Log.i(
                        TAG,
                        "WAKE_WORD_DETECTED name=${detection.model.name} score=${detection.score}",
                    )
                    Toast.makeText(
                        this@MainActivity,
                        "Wake word ${String.format("%.3f", detection.score)}",
                        Toast.LENGTH_SHORT,
                    ).show()

                    // Give the wrapper's AudioRecord time to release before Gemini's
                    // existing AudioCapture claims the microphone.
                    engine.stop()
                    delay(300)
                    if (voiceViewModel.voiceState.value == VoiceState.IDLE) {
                        voiceViewModel.activateAfterWakeWord()
                    }
                }
            }

            wakeWordStateJob = lifecycleScope.launch {
                voiceViewModel.voiceState.collect { state ->
                    if (state == VoiceState.IDLE) {
                        Log.i(TAG, "WAKE_WORD_LISTENING phrase=hello_world")
                        engine.start()
                    } else {
                        engine.stop()
                    }
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Wake-word prototype failed to initialize", e)
        }
    }

    override fun onDestroy() {
        wakeWordDetectionJob?.cancel()
        wakeWordStateJob?.cancel()
        wakeWordEngine?.release()
        wakeTestTts?.shutdown()
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
