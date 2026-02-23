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
    private var lastSpeed: Number = 0
    private var lastIncline: Number = 0
    private var currentStateContext = ""

    private val functionBridge = FunctionBridge(api)

    /** Call this when treadmill status or program state changes to keep voice context current. */
    fun updateTreadmillState(status: StatusMessage?, program: ProgramMessage?) {
        if (status == null) return
        val speed = if (status.emulate) status.emuSpeed else ((status.speed ?: 0.0) * 10).toInt()
        val incline = if (status.emulate) status.emuIncline else (status.incline ?: 0.0)

        val parts = mutableListOf<String>()
        parts.add("Speed: ${speed / 10.0} mph")
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
        if (speed != lastSpeed || incline != lastIncline) {
            lastSpeed = speed
            lastIncline = incline
            geminiClient?.updateStateContext(currentStateContext)
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
                            startMicCapture()
                            _voiceState.value = VoiceState.LISTENING
                            // Send pending prompt
                            pendingPrompt?.let {
                                geminiClient?.sendTextPrompt(it)
                                pendingPrompt = null
                            }
                        }
                        ClientState.DISCONNECTED, ClientState.ERROR -> {
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
                }

                override fun onSpeakingEnd() {
                    _voiceState.value = VoiceState.LISTENING
                }

                override fun onInterrupted() {
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
        audioCapture = AudioCapture { pcmBase64 ->
            if (client.isConnected) {
                client.sendAudio(pcmBase64)
            }
        }
        val started = audioCapture?.start() ?: false
        if (!started) {
            Log.e(TAG, "Failed to start mic capture")
            disconnectAll()
        }
    }

    private fun stopMicCapture() {
        audioCapture?.stop()
        audioCapture = null
    }

    private fun disconnectAll() {
        stopMicCapture()
        geminiClient?.disconnect()
        geminiClient = null
        audioPlayer?.flush()
        _voiceState.value = VoiceState.IDLE
    }

    override fun onCleared() {
        super.onCleared()
        stopMicCapture()
        geminiClient?.disconnect()
        audioPlayer?.release()
    }
}
