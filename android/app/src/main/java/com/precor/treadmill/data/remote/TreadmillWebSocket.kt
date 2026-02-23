package com.precor.treadmill.data.remote

import android.util.Log
import com.precor.treadmill.data.remote.models.ServerMessage
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.json.Json
import okhttp3.*

class TreadmillWebSocket(
    private val client: OkHttpClient,
    private val json: Json,
) {
    companion object {
        private const val TAG = "TreadmillWS"
        private const val RECONNECT_DELAY_MS = 2000L
    }

    private val _messages = MutableSharedFlow<ServerMessage>(extraBufferCapacity = 64)
    val messages: SharedFlow<ServerMessage> = _messages.asSharedFlow()

    private val _connected = MutableStateFlow(false)
    val connected: StateFlow<Boolean> = _connected.asStateFlow()

    private var webSocket: WebSocket? = null
    private var scope: CoroutineScope? = null
    private var serverUrl: String = ""
    private var shouldReconnect = false

    fun connect(baseUrl: String) {
        // Skip if already connecting/connected to same URL
        if (baseUrl == serverUrl && shouldReconnect) return
        disconnect()
        serverUrl = baseUrl
        shouldReconnect = true
        scope = CoroutineScope(Dispatchers.IO + SupervisorJob())
        doConnect()
    }

    fun disconnect() {
        shouldReconnect = false
        webSocket?.close(1000, "Client disconnect")
        webSocket = null
        scope?.cancel()
        scope = null
        _connected.value = false
    }

    private fun doConnect() {
        val wsUrl = serverUrl
            .replace("https://", "wss://")
            .replace("http://", "ws://")
            .trimEnd('/') + "/ws"

        val request = Request.Builder().url(wsUrl).build()
        webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.d(TAG, "Connected to $wsUrl")
                _connected.value = true
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val message = json.decodeFromString(ServerMessage.serializer(), text)
                    _messages.tryEmit(message)
                } catch (e: Exception) {
                    Log.w(TAG, "Failed to parse message: ${e.message}")
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.d(TAG, "Closed: $code $reason")
                _connected.value = false
                scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "WebSocket failure: ${t.message}")
                _connected.value = false
                scheduleReconnect()
            }
        })
    }

    private fun scheduleReconnect() {
        if (!shouldReconnect) return
        scope?.launch {
            delay(RECONNECT_DELAY_MS)
            if (shouldReconnect) {
                Log.d(TAG, "Reconnecting...")
                doConnect()
            }
        }
    }
}
