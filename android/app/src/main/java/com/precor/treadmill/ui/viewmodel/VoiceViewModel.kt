package com.precor.treadmill.ui.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.models.AppConfig
import com.precor.treadmill.data.remote.models.ExtractIntentRequest
import com.precor.treadmill.data.remote.models.StatusMessage
import com.precor.treadmill.data.remote.models.ProgramMessage
import com.precor.treadmill.voice.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch

/**
 * Voice session state machine.
 * States: Idle -> Connecting -> Listening -> Speaking
 *
 * Port of useVoice.ts.
 */

enum class VoiceState { IDLE, CONNECTING, LISTENING, SPEAKING }

class VoiceViewModel(
    private val api: TreadmillApi,
) : ViewModel() {

    companion object {
        private const val TAG = "VoiceVM"
    }

    private val _voiceState = MutableStateFlow(VoiceState.IDLE)
    val voiceState: StateFlow<VoiceState> = _voiceState.asStateFlow()

    private var config: AppConfig? = null
    private var geminiClient: GeminiLiveClient? = null
    private var audioCapture: AudioCapture? = null
    private var audioPlayer: AudioPlayer? = null
    private var pendingPrompt: String? = null
    private var lastSpeed: Int = 0
    private var lastIncline: Double = 0.0
    private var currentStateContext = ""

    private val functionBridge = FunctionBridge(api)

    /** Call this when treadmill status or program state changes to keep voice context current. */
    fun updateTreadmillState(status: StatusMessage?, program: ProgramMessage?) {
        if (status == null) return
        val speedTenths = if (status.emulate) status.emuSpeed else ((status.speed ?: 0.0) * 10).toInt()
        val incline = if (status.emulate) status.emuIncline.toDouble() else (status.incline ?: 0.0)

        val parts = mutableListOf<String>()
        parts.add("Speed: ${speedTenths / 10.0} mph")
        parts.add("Incline: $incline%")
        parts.add("Mode: ${if (status.emulate) "emulate" else "proxy"}")
        if (program?.running == true) {
            parts.add("Program: \"${program.program?.name ?: "unnamed"}\" running")
            program.program?.let { prog ->
                prog.intervals.getOrNull(program.currentInterval)?.let { iv ->
                    parts.add("Current interval: \"${iv.name}\"")
                }
            }
            if (program.paused) parts.add("PAUSED")
        }
        currentStateContext = parts.joinToString("\n")

        // Only push to Gemini when speed or incline actually changes
        if (speedTenths != lastSpeed || incline != lastIncline) {
            lastSpeed = speedTenths
            lastIncline = incline
            geminiClient?.updateStateContext(currentStateContext)
        }
    }

    /** Send a text command to Gemini for testing (no mic needed). */
    fun sendTestCommand(text: String) {
        if (geminiClient?.isConnected == true) {
            Log.d(TAG, "Sending test command: $text")
            geminiClient?.sendTextPrompt(text)
        } else {
            Log.d(TAG, "Not connected, connecting first then sending: $text")
            pendingPrompt = text
            connect()
        }
    }

    /**
     * Toggle voice session. Starts if idle, stops if active.
     * Optionally sends a text prompt when connected (e.g. "Tell us your own" flow).
     */
    fun toggle(prompt: String? = null) {
        when (_voiceState.value) {
            VoiceState.IDLE -> {
                pendingPrompt = prompt
                connect()
            }
            VoiceState.CONNECTING -> disconnectAll()
            VoiceState.LISTENING -> disconnectAll()
            VoiceState.SPEAKING -> interrupt()
        }
    }

    fun interrupt() {
        Log.d(TAG, "interrupt() called — flushing player")
        audioPlayer?.flush()
        _voiceState.value = VoiceState.LISTENING
    }

    private fun connect() {
        viewModelScope.launch {
            // Fetch config if not cached
            if (config == null) {
                config = try {
                    api.getConfig()
                } catch (e: Exception) {
                    Log.e(TAG, "Failed to fetch config", e)
                    _voiceState.value = VoiceState.IDLE
                    return@launch
                }
            }
            val cfg = config!!
            if (cfg.geminiApiKey.isEmpty()) {
                Log.w(TAG, "No Gemini API key configured")
                _voiceState.value = VoiceState.IDLE
                return@launch
            }

            Log.d(TAG, "Connecting: model=${cfg.geminiLiveModel.ifEmpty { "gemini-2.5-flash-native-audio-latest" }}, voice=${cfg.geminiVoice.ifEmpty { "Kore" }}")
            _voiceState.value = VoiceState.CONNECTING

            val player = AudioPlayer(
                sampleRate = 24000,
                onPlaybackStart = null,
                onPlaybackEnd = null,
            )
            audioPlayer = player

            // Read smartass preference — passed from the calling layer or default false
            val callbacks = object : GeminiLiveCallbacks {
                override fun onStateChange(state: ClientState) {
                    when (state) {
                        ClientState.CONNECTED -> {
                            _voiceState.value = VoiceState.LISTENING
                            // Send pending text prompt BEFORE starting mic.
                            // Mic audio interferes with text prompt processing
                            // in Gemini Live, so defer mic start until after
                            // the response (onSpeakingEnd will start it).
                            val prompt = pendingPrompt
                            if (prompt != null) {
                                Log.d(TAG, "Sending pending prompt (mic deferred): $prompt")
                                geminiClient?.sendTextPrompt(prompt)
                                pendingPrompt = null
                            } else {
                                Log.d(TAG, "No pending prompt, starting mic")
                                startMicCapture()
                            }
                        }
                        ClientState.DISCONNECTED, ClientState.ERROR -> {
                            Log.d(TAG, "State: $state — stopping mic, flushing player")
                            stopMicCapture()
                            player.flush()
                            _voiceState.value = VoiceState.IDLE
                        }
                        ClientState.CONNECTING -> {}
                    }
                }

                override fun onAudioChunk(pcmBase64: String) {
                    player.enqueue(pcmBase64)
                }

                override fun onSpeakingStart() {
                    _voiceState.value = VoiceState.SPEAKING
                    // Mic stays open for full-duplex — Gemini Live detects
                    // barge-in (user speaking) and sends an "interrupted" event.
                }

                override fun onSpeakingEnd() {
                    _voiceState.value = VoiceState.LISTENING
                    // Start mic if not already running (deferred from text prompt)
                    if (audioCapture == null) {
                        Log.d(TAG, "Starting deferred mic capture after speaking")
                        startMicCapture()
                    }
                }

                override fun onInterrupted() {
                    Log.d(TAG, "onInterrupted — flushing player (barge-in)")
                    player.flush()
                    _voiceState.value = VoiceState.LISTENING
                }

                override fun onError(msg: String) {
                    Log.e(TAG, "Gemini error: $msg")
                }

                override fun onTextFallback(text: String, executedCalls: List<String>) {
                    Log.d(TAG, "Text fallback triggered: $text")
                    Log.d(TAG, "Already executed by Live: $executedCalls")
                    viewModelScope.launch {
                        try {
                            Log.d(TAG, "Extracting intent via Flash...")
                            val result = api.extractIntent(
                                ExtractIntentRequest(text, executedCalls)
                            )
                            Log.d(TAG, "Fallback result: actions=${result.actions}, text=${result.text}")
                            if (result.actions.isNotEmpty()) {
                                Log.d(
                                    TAG,
                                    "Fallback executed: ${result.actions.map { "${it.name} -> ${it.result}" }}"
                                )
                            }
                        } catch (e: Exception) {
                            Log.e(TAG, "Intent extraction failed", e)
                        }
                    }
                }
            }

            val client = GeminiLiveClient(
                apiKey = cfg.geminiApiKey,
                model = cfg.geminiLiveModel.ifEmpty { "gemini-2.5-flash-native-audio-latest" },
                voice = cfg.geminiVoice.ifEmpty { "Kore" },
                callbacks = callbacks,
                functionBridge = functionBridge,
                stateContext = currentStateContext,
                smartass = false,
            )
            geminiClient = client
            client.connect()
        }
    }

    private fun startMicCapture() {
        val client = geminiClient ?: return
        Log.d(TAG, "Starting mic capture...")
        audioCapture = AudioCapture { pcmBase64 ->
            if (client.isConnected) {
                client.sendAudio(pcmBase64)
            }
        }
        val started = audioCapture?.start() ?: false
        if (started) {
            Log.d(TAG, "Mic capture started successfully")
        } else {
            Log.e(TAG, "Failed to start mic capture — check RECORD_AUDIO permission")
            disconnectAll()
        }
    }

    private fun stopMicCapture() {
        audioCapture?.stop()
        audioCapture = null
    }

    private fun disconnectAll() {
        Log.d(TAG, "disconnectAll() — stopping everything")
        stopMicCapture()
        geminiClient?.disconnect()
        geminiClient = null
        audioPlayer?.release()
        audioPlayer = null
        config = null  // Clear cached token so next connect gets a fresh one
        _voiceState.value = VoiceState.IDLE
    }

    override fun onCleared() {
        super.onCleared()
        stopMicCapture()
        geminiClient?.disconnect()
        audioPlayer?.release()
    }
}
