package com.precor.treadmill.voice

import com.precor.treadmill.data.remote.TreadmillApi
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Proxy
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue

class GeminiLiveClientSendTest {

    @Test
    fun acceptedAudioSends_logOnlyFirstAndHundredthChunk_withoutPayloads() {
        val harness = Harness()
        val connection = harness.connectAndReady()

        repeat(100) { index ->
            harness.client.sendAudio("secret-pcm-${index + 1}")
        }

        assertEquals(100, connection.socket.audioSendCount)
        assertEquals(
            listOf("Accepted audio chunk 1", "Accepted audio chunk 100"),
            harness.debugLogs.filter { it.startsWith("Accepted audio chunk") },
        )
        assertFalse(harness.debugLogs.any { "secret-pcm" in it })
        assertTrue(harness.client.isConnected)
    }

    @Test
    fun acceptedAudioCounter_resetsForReplacementConnection() {
        val harness = Harness()
        harness.connectAndReady()
        harness.client.sendAudio("first-payload")
        harness.client.disconnect()

        harness.connectAndReady()
        harness.client.sendAudio("second-payload")

        assertEquals(
            listOf("Accepted audio chunk 1", "Accepted audio chunk 1"),
            harness.debugLogs.filter { it.startsWith("Accepted audio chunk") },
        )
        assertFalse(harness.debugLogs.any { "payload" in it })
    }

    @Test
    fun rejectedAudioSend_reportsOneError_andLaterCallbacksCannotDowngradeIt() {
        val harness = Harness()
        val connection = harness.connectAndReady()
        connection.socket.acceptAudio = false
        connection.socket.beforeAudioResult = {
            connection.listener.onClosed(connection.socket, 1006, "close during send")
        }

        harness.client.sendAudio("rejected-payload")
        connection.listener.onFailure(connection.socket, IllegalStateException("late failure"), null)
        connection.listener.onClosed(connection.socket, 1006, "late close")
        harness.client.sendAudio("ignored-payload")

        assertEquals(1, connection.socket.audioSendCount)
        assertEquals(1, harness.callbacks.states.count { it == ClientState.ERROR })
        assertEquals(0, harness.callbacks.states.count { it == ClientState.DISCONNECTED })
        assertEquals(1, harness.callbacks.errors.size)
        assertEquals(1, harness.errorLogs.count { "Audio WebSocket send rejected" in it })
        assertFalse(harness.errorLogs.any { "payload" in it })
        assertFalse(harness.client.isConnected)
    }

    @Test
    fun rejectedAudioSend_dispatchesCallbacksAwayFromCapturePath() {
        val pendingCallbacks = LinkedBlockingQueue<() -> Unit>()
        val harness = Harness(terminalCallbackDispatch = pendingCallbacks::add)
        val connection = harness.connectAndReady()
        connection.socket.acceptAudio = false

        harness.client.sendAudio("rejected-payload")

        assertFalse(harness.client.isConnected)
        assertEquals(0, harness.callbacks.states.count { it == ClientState.ERROR })
        assertTrue(harness.callbacks.errors.isEmpty())

        pendingCallbacks.remove().invoke()

        assertEquals(1, harness.callbacks.states.count { it == ClientState.ERROR })
        assertEquals(1, harness.callbacks.errors.size)
    }

    @Test
    fun staleFailureFromOldSocket_doesNotTerminateReplacementConnection() {
        val harness = Harness()
        val oldConnection = harness.connectAndReady()
        harness.client.disconnect()
        harness.connectAndReady()

        oldConnection.listener.onFailure(
            oldConnection.socket,
            IllegalStateException("stale failure"),
            null,
        )

        assertTrue(harness.client.isConnected)
        assertEquals(0, harness.callbacks.states.count { it == ClientState.ERROR })
        assertTrue(harness.callbacks.errors.isEmpty())
    }

    private class Harness(
        terminalCallbackDispatch: ((() -> Unit) -> Unit) = { callback -> callback() },
    ) {
        val callbacks = RecordingCallbacks()
        val debugLogs = CopyOnWriteArrayList<String>()
        val errorLogs = CopyOnWriteArrayList<String>()
        private val connections = CopyOnWriteArrayList<FakeConnection>()

        val client = GeminiLiveClient(
            apiKey = "test-token",
            model = "gemini-3.1-flash-live-preview",
            voice = "Kore",
            callbacks = callbacks,
            functionBridge = FunctionBridge(fakeApi()),
            okHttpClient = OkHttpClient(),
            webSocketFactory = { _, request, listener ->
                FakeWebSocket(request).also { socket ->
                    connections += FakeConnection(socket, listener)
                }
            },
            debugLog = { debugLogs += it },
            errorLog = { errorLogs += it },
            elapsedRealtime = { 1L },
            terminalCallbackDispatch = terminalCallbackDispatch,
        )

        fun connectAndReady(): FakeConnection {
            val expectedCount = connections.size + 1
            client.connect()
            val connection = connections.last()
            connection.listener.onMessage(connection.socket, "{\"setupComplete\":{}}")
            assertTrue(callbacks.awaitConnectedCount(expectedCount))
            return connection
        }
    }

    private data class FakeConnection(
        val socket: FakeWebSocket,
        val listener: WebSocketListener,
    )

    private class FakeWebSocket(private val request: Request) : WebSocket {
        @Volatile var acceptAudio = true
        @Volatile var audioSendCount = 0
        @Volatile var beforeAudioResult: (() -> Unit)? = null

        override fun request(): Request = request
        override fun queueSize(): Long = 0

        override fun send(text: String): Boolean {
            if ("\"audio\"" !in text) return true
            audioSendCount += 1
            beforeAudioResult?.invoke()
            return acceptAudio
        }

        override fun send(bytes: ByteString): Boolean = true
        override fun close(code: Int, reason: String?): Boolean = true
        override fun cancel() = Unit
    }

    private class RecordingCallbacks : GeminiLiveCallbacks {
        val states = CopyOnWriteArrayList<ClientState>()
        val errors = CopyOnWriteArrayList<String>()

        override fun onStateChange(state: ClientState) {
            states += state
        }

        fun awaitConnectedCount(expectedCount: Int): Boolean {
            val deadline = System.nanoTime() + 2_000_000_000L
            while (System.nanoTime() < deadline) {
                if (states.count { it == ClientState.CONNECTED } >= expectedCount) return true
                Thread.sleep(10)
            }
            return false
        }

        override fun onAudioChunk(pcmBase64: String) = Unit
        override fun onSpeakingStart() = Unit
        override fun onSpeakingEnd() = Unit
        override fun onInterrupted() = Unit
        override fun onError(msg: String) { errors += msg }
    }

    companion object {
        @Suppress("UNCHECKED_CAST")
        private fun fakeApi(): TreadmillApi = Proxy.newProxyInstance(
            TreadmillApi::class.java.classLoader,
            arrayOf(TreadmillApi::class.java),
        ) { _, _, _ -> null } as TreadmillApi
    }
}
