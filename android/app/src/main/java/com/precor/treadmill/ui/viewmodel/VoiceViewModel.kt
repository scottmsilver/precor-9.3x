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
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient

/**
 * Voice session state machine with always-on Gemini Live connection.
 *
 * The Gemini WebSocket stays connected in the background whenever the server
 * is reachable. Token is refreshed every 20 minutes. Mic is pre-warmed.
 * toggle() just flips the mic on/off — near-instant voice activation.
 */

enum class VoiceState { IDLE, CONNECTING, LISTENING, SPEAKING }

class VoiceViewModel(
    private val api: TreadmillApi,
    private val okHttpClient: OkHttpClient,
) : ViewModel() {

    companion object {
        private const val TAG = "VoiceVM"
        private const val TOKEN_REFRESH_MS = 20 * 60 * 1000L  // 20 minutes
        private const val RECONNECT_DELAY_MS = 2000L
    }

    private val _voiceState = MutableStateFlow(VoiceState.IDLE)
    val voiceState: StateFlow<VoiceState> = _voiceState.asStateFlow()

    // Connection state
    private var config: AppConfig? = null
    private var geminiClient: GeminiLiveClient? = null
    private var audioCapture: AudioCapture? = null   // persistent, pre-warmed
    private var audioPlayer: AudioPlayer? = null      // persistent
    private var tokenRefreshJob: Job? = null
    private var reconnectJob: Job? = null
    private var connectJob: Job? = null
    private var serverConnected = false

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

    // ── Always-on connection lifecycle ──────────────────────────────────

    /** Called when server WebSocket connects. Starts background Gemini connection. */
    fun ensureConnected() {
        serverConnected = true
        if (geminiClient?.isConnected == true) return
        connectBackground()
    }

    /** Called when server WebSocket disconnects. Tears down Gemini connection. */
    fun onServerDisconnected() {
        serverConnected = false
        tokenRefreshJob?.cancel()
        reconnectJob?.cancel()
        connectJob?.cancel()
        teardownConnection()
    }

    private fun connectBackground() {
        connectJob?.cancel()
        connectJob = viewModelScope.launch {
            if (config == null) {
                config = try { api.getConfig() } catch (e: Exception) {
                    Log.e(TAG, "Failed to fetch config for background connect", e)
                    null
                }
            }
            val cfg = config ?: return@launch
            if (cfg.geminiApiKey.isEmpty()) {
                Log.w(TAG, "No Gemini API key — skipping background connect")
                return@launch
            }

            // Create persistent AudioPlayer if needed
            if (audioPlayer == null) {
                audioPlayer = AudioPlayer(sampleRate = 24000)
            }

            // Warm up mic (pre-create AudioRecord + AEC) without starting recording
            if (audioCapture == null) {
                audioCapture = AudioCapture { /* callback set on toggle */ }
            }
            audioCapture?.warmUp()

            val player = audioPlayer!!

            val client = GeminiLiveClient(
                apiKey = cfg.geminiApiKey,
                model = cfg.geminiLiveModel.ifEmpty { "gemini-2.5-flash-native-audio-latest" },
                voice = cfg.geminiVoice.ifEmpty { "Kore" },
                callbacks = backgroundCallbacks(player),
                functionBridge = functionBridge,
                stateContext = currentStateContext,
                smartass = false,
                okHttpClient = okHttpClient,
            )
            geminiClient = client
            client.speechEndTimestampProvider = { audioCapture?.silenceStartMs ?: 0L }
            Log.d(TAG, "Opening background Gemini connection (model=${cfg.geminiLiveModel.ifEmpty { "gemini-2.5-flash-native-audio-latest" }})")
            client.connect()

            startTokenRefresh()
        }
    }

    /** Callbacks for the always-on background connection. */
    private fun backgroundCallbacks(player: AudioPlayer) = object : GeminiLiveCallbacks {
        override fun onStateChange(state: ClientState) {
            when (state) {
                ClientState.CONNECTED -> {
                    Log.d(TAG, "Background connection ready")
                    // If we were in CONNECTING state (user tapped before connection ready),
                    // transition to active voice
                    if (_voiceState.value == VoiceState.CONNECTING) {
                        _voiceState.value = VoiceState.LISTENING
                        val prompt = pendingPrompt
                        if (prompt != null) {
                            Log.d(TAG, "Sending pending prompt (mic deferred): $prompt")
                            geminiClient?.sendTextPrompt(prompt)
                            pendingPrompt = null
                        } else {
                            startMicCapture()
                        }
                    }
                    // Otherwise, just sitting ready in background — don't change state
                }
                ClientState.DISCONNECTED, ClientState.ERROR -> {
                    Log.d(TAG, "Background connection lost: $state")
                    if (_voiceState.value != VoiceState.IDLE) {
                        stopMicCapture()
                        player.flush()
                        _voiceState.value = VoiceState.IDLE
                    }
                    if (serverConnected) scheduleReconnect()
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
            // Start mic if not already running (deferred from text prompt)
            if (audioCapture?.let { !isMicActive() } == true) {
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
                        Log.d(TAG, "Fallback executed: ${result.actions.map { "${it.name} -> ${it.result}" }}")
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Intent extraction failed", e)
                }
            }
        }
    }

    private fun startTokenRefresh() {
        tokenRefreshJob?.cancel()
        tokenRefreshJob = viewModelScope.launch {
            while (true) {
                delay(TOKEN_REFRESH_MS)
                Log.d(TAG, "Token refresh: fetching new config")
                val newConfig = try { api.getConfig() } catch (_: Exception) { null }
                if (newConfig != null) {
                    config = newConfig
                    // Reconnect with fresh token
                    geminiClient?.disconnect()
                    geminiClient = null
                    connectBackground()
                }
            }
        }
    }

    private fun scheduleReconnect() {
        reconnectJob?.cancel()
        reconnectJob = viewModelScope.launch {
            delay(RECONNECT_DELAY_MS)
            Log.d(TAG, "Auto-reconnecting...")
            config = null  // Force fresh token
            connectBackground()
        }
    }

    // ── Voice toggle (just controls mic) ───────────────────────────────

    /** Send a text command to Gemini for testing (no mic needed). */
    fun sendTestCommand(text: String) {
        if (geminiClient?.isConnected == true) {
            Log.d(TAG, "Sending test command: $text")
            geminiClient?.sendTextPrompt(text)
        } else {
            Log.d(TAG, "Not connected, connecting first then sending: $text")
            pendingPrompt = text
            _voiceState.value = VoiceState.CONNECTING
            connectBackground()
        }
    }

    /**
     * Toggle voice session. If connection is hot, just flips the mic.
     * If connection isn't ready, shows CONNECTING and waits.
     */
    fun toggle(prompt: String? = null) {
        when (_voiceState.value) {
            VoiceState.IDLE -> {
                if (geminiClient?.isConnected == true) {
                    // Hot path: connection ready, just start mic
                    if (prompt != null) {
                        pendingPrompt = prompt
                        geminiClient?.sendTextPrompt(prompt)
                        _voiceState.value = VoiceState.LISTENING
                        // Mic deferred until onSpeakingEnd
                    } else {
                        startMicCapture()
                        _voiceState.value = VoiceState.LISTENING
                    }
                } else {
                    // Cold path: connection not ready, show connecting
                    pendingPrompt = prompt
                    _voiceState.value = VoiceState.CONNECTING
                    connectBackground()
                }
            }
            VoiceState.CONNECTING -> {
                stopMicCapture()
                pendingPrompt = null
                _voiceState.value = VoiceState.IDLE
            }
            VoiceState.LISTENING -> {
                stopMicCapture()
                _voiceState.value = VoiceState.IDLE
            }
            VoiceState.SPEAKING -> interrupt()
        }
    }

    fun interrupt() {
        Log.d(TAG, "interrupt() called — flushing player")
        audioPlayer?.flush()
        _voiceState.value = VoiceState.LISTENING
    }

    // ── Mic capture ────────────────────────────────────────────────────

    @Volatile
    private var micActive = false

    private fun isMicActive() = micActive

    private fun startMicCapture() {
        if (geminiClient == null) return
        Log.d(TAG, "Starting mic capture...")
        // Reference geminiClient directly so token refresh doesn't strand the callback
        audioCapture?.updateCallback { pcmBase64 ->
            geminiClient?.takeIf { it.isConnected }?.sendAudio(pcmBase64)
        }
        // Save raw PCM for offline replay testing
        audioCapture?.recordingPath = "/sdcard/Download/voice_recording.pcm"
        val started = audioCapture?.start() ?: false
        if (started) {
            micActive = true
            Log.d(TAG, "Mic capture started successfully")
        } else {
            Log.e(TAG, "Failed to start mic capture — check RECORD_AUDIO permission")
            _voiceState.value = VoiceState.IDLE
        }
    }

    private fun stopMicCapture() {
        audioCapture?.stop()  // keeps AudioRecord alive for reuse
        micActive = false
    }

    // ── Teardown ───────────────────────────────────────────────────────

    private fun teardownConnection() {
        Log.d(TAG, "teardownConnection()")
        stopMicCapture()
        geminiClient?.disconnect()
        geminiClient = null
        audioPlayer?.flush()
        _voiceState.value = VoiceState.IDLE
    }

    override fun onCleared() {
        super.onCleared()
        tokenRefreshJob?.cancel()
        reconnectJob?.cancel()
        connectJob?.cancel()
        stopMicCapture()
        audioCapture?.release()
        geminiClient?.disconnect()
        audioPlayer?.release()
    }
}
