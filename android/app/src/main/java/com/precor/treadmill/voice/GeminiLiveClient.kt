package com.precor.treadmill.voice

import android.util.Log
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import okhttp3.*

/**
 * WebSocket client for Gemini Live (BidiGenerateContentConstrained) API.
 * Port of GeminiLiveClient.ts.
 *
 * Manages the bidirectional streaming connection:
 * - Sends setup message with model config, tools, system prompt
 * - Streams mic audio as base64 PCM chunks
 * - Receives audio responses and tool calls
 * - Handles barge-in (interruption)
 */

enum class ClientState { DISCONNECTED, CONNECTING, CONNECTED, ERROR }

interface GeminiLiveCallbacks {
    fun onStateChange(state: ClientState)
    fun onAudioChunk(pcmBase64: String)
    fun onSpeakingStart()
    fun onSpeakingEnd()
    fun onInterrupted()
    fun onError(msg: String)
    fun onTextFallback(text: String, executedCalls: List<String>) {}
}

class GeminiLiveClient(
    private val apiKey: String,
    private val model: String,
    private val voice: String,
    private val callbacks: GeminiLiveCallbacks,
    private val functionBridge: FunctionBridge,
    private var stateContext: String = "",
    private val smartass: Boolean = false,
) {
    companion object {
        private const val TAG = "GeminiLive"
        private const val GEMINI_WS_BASE =
            "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained"
        private const val TURN_COMPLETE_DELAY_MS = 200L
    }

    private val json = Json { ignoreUnknownKeys = true }
    private var ws: WebSocket? = null
    private var client: OkHttpClient? = null
    private var scope: CoroutineScope? = null
    private var state: ClientState = ClientState.DISCONNECTED
    private var setupDone = false
    private var receivingAudio = false
    private var turnCompleteJob: Job? = null
    private val turnTextParts = mutableListOf<String>()
    private val turnToolCalls = mutableListOf<String>()

    val isConnected: Boolean
        get() = state == ClientState.CONNECTED && setupDone

    private fun setState(s: ClientState) {
        state = s
        callbacks.onStateChange(s)
    }

    /** Update the treadmill state context. Sends to Gemini mid-session if connected. */
    fun updateStateContext(ctx: String) {
        if (ctx == stateContext) return
        stateContext = ctx
        sendStateUpdate(ctx)
    }

    private fun sendStateUpdate(ctx: String) {
        if (ws == null || !setupDone) return
        val msg = buildJsonObject {
            putJsonObject("client_content") {
                putJsonArray("turns") {
                    addJsonObject {
                        put("role", "user")
                        putJsonArray("parts") {
                            addJsonObject {
                                put("text", "[State update — do not respond]\n$ctx")
                            }
                        }
                    }
                }
                put("turn_complete", true)
            }
        }
        ws?.send(msg.toString())
    }

    fun connect() {
        if (ws != null) return
        setState(ClientState.CONNECTING)

        scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        val okClient = OkHttpClient.Builder()
            .readTimeout(0, java.util.concurrent.TimeUnit.MILLISECONDS)
            .build()
        client = okClient

        val url = "$GEMINI_WS_BASE?access_token=$apiKey"
        Log.d(TAG, "Connecting to: ${url.take(120)}...")
        val request = Request.Builder().url(url).build()

        ws = okClient.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.d(TAG, "WebSocket connected, sending setup...")
                sendSetup()
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                Log.d(TAG, "onMessage(text): ${text.take(300)}")
                scope?.launch { handleMessage(text) }
            }

            override fun onMessage(webSocket: WebSocket, bytes: okio.ByteString) {
                val text = bytes.utf8()
                Log.d(TAG, "onMessage(binary, ${bytes.size} bytes): ${text.take(300)}")
                scope?.launch { handleMessage(text) }
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.e(TAG, "WebSocket error: ${t.message}")
                callbacks.onError("WebSocket connection error")
                cleanup()
                setState(ClientState.ERROR)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.d(TAG, "WebSocket closed: $code $reason")
                cleanup()
                if (state != ClientState.ERROR) {
                    setState(ClientState.DISCONNECTED)
                }
            }
        })
    }

    fun disconnect() {
        ws?.close(1000, "Client disconnect")
        cleanup()
        setState(ClientState.DISCONNECTED)
    }

    private fun cleanup() {
        ws = null
        setupDone = false
        receivingAudio = false
        turnTextParts.clear()
        turnToolCalls.clear()
        turnCompleteJob?.cancel()
        turnCompleteJob = null
        scope?.cancel()
        scope = null
        client?.dispatcher?.executorService?.shutdown()
        client = null
    }

    private fun sendSetup() {
        val basePrompt = if (smartass) {
            VOICE_SYSTEM_PROMPT + VOICE_SMARTASS_ADDENDUM
        } else {
            VOICE_SYSTEM_PROMPT
        }
        val systemText = if (stateContext.isNotEmpty()) {
            "$basePrompt\n\nCurrent treadmill state:\n$stateContext"
        } else {
            basePrompt
        }

        val toolDecls = buildJsonArray {
            for (decl in TOOL_DECLARATIONS) {
                addJsonObject {
                    put("name", decl.name)
                    put("description", decl.description)
                    putJsonObject("parameters") {
                        put("type", decl.parameters.type)
                        putJsonObject("properties") {
                            for ((key, param) in decl.parameters.properties) {
                                putJsonObject(key) {
                                    put("type", param.type)
                                    param.description?.let { put("description", it) }
                                    param.items?.let { put("items", it) }
                                }
                            }
                        }
                        decl.parameters.required?.let { req ->
                            putJsonArray("required") { req.forEach { add(it) } }
                        }
                    }
                }
            }
        }

        val setup = buildJsonObject {
            putJsonObject("setup") {
                put("model", "models/$model")
                putJsonObject("system_instruction") {
                    putJsonArray("parts") {
                        addJsonObject { put("text", systemText) }
                    }
                }
                putJsonArray("tools") {
                    addJsonObject {
                        put("function_declarations", toolDecls)
                    }
                }
                putJsonObject("generation_config") {
                    putJsonObject("speech_config") {
                        putJsonObject("voice_config") {
                            putJsonObject("prebuilt_voice_config") {
                                put("voice_name", voice)
                            }
                        }
                    }
                    putJsonArray("response_modalities") { add("AUDIO") }
                }
            }
        }

        val setupStr = setup.toString()
        Log.d(TAG, "Setup message (first 500): ${setupStr.take(500)}")
        ws?.send(setupStr)
    }

    private suspend fun handleMessage(raw: String) {
        val msg = try {
            json.parseToJsonElement(raw).jsonObject
        } catch (e: Exception) {
            return
        }

        // Setup complete
        if ("setupComplete" in msg || "setup_complete" in msg) {
            Log.d(TAG, "Setup complete, ready for audio")
            setupDone = true
            setState(ClientState.CONNECTED)
            // DEBUG: auto-send greeting to test audio response pipeline
            sendTextPrompt("Say hello briefly in one sentence")
            return
        }

        // Tool call cancellation
        if ("toolCallCancellation" in msg || "tool_call_cancellation" in msg) {
            return
        }

        // Server content (audio, turn complete, interrupted)
        val serverContent = (msg["serverContent"] ?: msg["server_content"])?.jsonObject
        if (serverContent != null) {
            // Interrupted
            if (serverContent["interrupted"]?.jsonPrimitive?.booleanOrNull == true) {
                receivingAudio = false
                callbacks.onInterrupted()
                return
            }

            // Turn complete
            if (serverContent["turnComplete"]?.jsonPrimitive?.booleanOrNull == true ||
                serverContent["turn_complete"]?.jsonPrimitive?.booleanOrNull == true
            ) {
                val textJoined = turnTextParts.joinToString(" ")
                Log.d(TAG, "Turn complete: toolCalls=$turnToolCalls, text=${textJoined.ifEmpty { "(none)" }}")
                if (turnTextParts.isNotEmpty()) {
                    callbacks.onTextFallback(textJoined, turnToolCalls.toList())
                }
                turnTextParts.clear()
                turnToolCalls.clear()

                // Small delay to let last audio chunks finish before signaling speaking end
                turnCompleteJob?.cancel()
                turnCompleteJob = scope?.launch {
                    delay(TURN_COMPLETE_DELAY_MS)
                    if (receivingAudio) {
                        receivingAudio = false
                        callbacks.onSpeakingEnd()
                    }
                }
                return
            }

            // Model turn — audio and text parts
            val modelTurn = (serverContent["modelTurn"] ?: serverContent["model_turn"])?.jsonObject
            val parts = modelTurn?.get("parts")?.jsonArray
            if (parts != null) {
                for (part in parts) {
                    val partObj = part.jsonObject

                    // Collect text parts for fallback detection
                    val text = partObj["text"]?.jsonPrimitive?.contentOrNull
                    if (!text.isNullOrBlank()) {
                        Log.d(TAG, "modelTurn text: $text")
                        turnTextParts.add(text)
                    }

                    // Audio inline data
                    val inlineData = (partObj["inlineData"] ?: partObj["inline_data"])?.jsonObject
                    val audioData = inlineData?.get("data")?.jsonPrimitive?.contentOrNull
                    if (audioData != null) {
                        if (!receivingAudio) {
                            receivingAudio = true
                            callbacks.onSpeakingStart()
                        }
                        turnCompleteJob?.cancel()
                        turnCompleteJob = null
                        callbacks.onAudioChunk(audioData)
                    }
                }
            }
        }

        // Tool call
        val toolCall = (msg["toolCall"] ?: msg["tool_call"])?.jsonObject
        val functionCalls = toolCall?.get("functionCalls")?.jsonArray
        if (functionCalls != null) {
            for (fc in functionCalls) {
                val fcObj = fc.jsonObject
                val name = fcObj["name"]?.jsonPrimitive?.content ?: continue
                val args = fcObj["args"]?.jsonObject?.let { argsObj ->
                    argsObj.entries.associate { (k, v) -> k to v }
                } ?: emptyMap()

                turnToolCalls.add(name)
                Log.d(TAG, "toolCall: $name($args)")

                val result = functionBridge.execute(name, args)
                sendToolResponse(result.name, result.response)
            }
            // Fire fallback immediately if there was narration text alongside tool calls
            if (turnTextParts.isNotEmpty()) {
                val textJoined = turnTextParts.joinToString(" ")
                Log.d(TAG, "Fallback (post-toolCall): already_executed=$turnToolCalls")
                callbacks.onTextFallback(textJoined, turnToolCalls.toList())
                turnTextParts.clear()
            }
        }
    }

    private fun sendToolResponse(name: String, response: String) {
        if (ws == null) return
        val msg = buildJsonObject {
            putJsonObject("toolResponse") {
                putJsonArray("functionResponses") {
                    addJsonObject {
                        put("name", name)
                        putJsonObject("response") {
                            put("result", response)
                        }
                    }
                }
            }
        }
        ws?.send(msg.toString())
    }

    /** Send a text prompt into the live session as a user turn. */
    fun sendTextPrompt(text: String) {
        if (ws == null || !setupDone) return
        val msg = buildJsonObject {
            putJsonObject("client_content") {
                putJsonArray("turns") {
                    addJsonObject {
                        put("role", "user")
                        putJsonArray("parts") {
                            addJsonObject { put("text", text) }
                        }
                    }
                }
                put("turn_complete", true)
            }
        }
        ws?.send(msg.toString())
    }

    /** Send a PCM16 audio chunk (base64 encoded) to Gemini. */
    fun sendAudio(pcmBase64: String) {
        if (ws == null || !setupDone) return
        val msg = buildJsonObject {
            putJsonObject("realtimeInput") {
                putJsonArray("mediaChunks") {
                    addJsonObject {
                        put("mimeType", "audio/pcm;rate=16000")
                        put("data", pcmBase64)
                    }
                }
            }
        }
        ws?.send(msg.toString())
    }
}
