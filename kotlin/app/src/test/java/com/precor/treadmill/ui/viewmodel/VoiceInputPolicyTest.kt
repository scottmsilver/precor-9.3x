package com.precor.treadmill.ui.viewmodel

import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.models.ToolCallResponse
import com.precor.treadmill.ui.navigation.MicrophonePermissionAction
import com.precor.treadmill.ui.navigation.microphonePermissionAction
import com.precor.treadmill.voice.FunctionBridge
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import java.lang.reflect.Proxy
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import okhttp3.Request
import okio.Timeout
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response

class VoiceInputPolicyTest {
    @Test
    fun staleSessionCannotRunAfterDisableAndReenable() {
        val gate = VoiceActivationGate()
        gate.setEnabled(true)
        val staleSession = gate.beginSession()

        gate.setEnabled(false)
        gate.setEnabled(true)

        assertFalse(gate.isActive(staleSession))
        assertFalse(gate.runIfActive(staleSession) { error("stale callback ran") })
        assertTrue(gate.isActive(gate.beginSession()))
    }

    @Test
    fun staleAudioChunkCannotReadReplacementClient() {
        val gate = VoiceActivationGate()
        gate.setEnabled(true)
        val staleSession = gate.beginSession()
        val client = AtomicReference("old")
        val actionEntered = CountDownLatch(1)
        val releaseAction = CountDownLatch(1)
        val replaceStarted = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)
        val sentTo = AtomicReference<String?>(null)

        try {
            val send = executor.submit<Boolean> {
                gate.runIfActive(staleSession) {
                    actionEntered.countDown()
                    releaseAction.await()
                    sentTo.set(client.get())
                }
            }
            assertTrue(actionEntered.await(1, TimeUnit.SECONDS))

            val replace = executor.submit {
                replaceStarted.countDown()
                gate.setEnabled(false)
                client.set("new")
                gate.setEnabled(true)
                gate.beginSession()
            }
            assertTrue(replaceStarted.await(1, TimeUnit.SECONDS))
            releaseAction.countDown()

            assertTrue(send.get(1, TimeUnit.SECONDS))
            replace.get(1, TimeUnit.SECONDS)
            assertEquals("old", sentTo.get())
            assertFalse(gate.runIfActive(staleSession) { sentTo.set(client.get()) })
            assertEquals("old", sentTo.get())
        } finally {
            releaseAction.countDown()
            executor.shutdownNow()
        }
    }

    @Test
    fun disablingWaitsForAnActiveCaptureBoundaryThenInvalidatesIt() {
        val gate = VoiceActivationGate()
        gate.setEnabled(true)
        val session = gate.beginSession()
        val actionEntered = CountDownLatch(1)
        val releaseAction = CountDownLatch(1)
        val disableStarted = CountDownLatch(1)
        val executor = Executors.newFixedThreadPool(2)

        try {
            val capture = executor.submit<Boolean> {
                gate.runIfActive(session) {
                    actionEntered.countDown()
                    releaseAction.await()
                }
            }
            assertTrue(actionEntered.await(1, TimeUnit.SECONDS))

            val disable = executor.submit<Boolean> {
                disableStarted.countDown()
                gate.setEnabled(false)
            }
            assertTrue(disableStarted.await(1, TimeUnit.SECONDS))
            assertThrows(TimeoutException::class.java) {
                disable.get(100, TimeUnit.MILLISECONDS)
            }

            releaseAction.countDown()
            assertTrue(capture.get(1, TimeUnit.SECONDS))
            assertTrue(disable.get(1, TimeUnit.SECONDS))
            assertFalse(gate.runIfActive(session) { error("disabled capture ran") })
        } finally {
            releaseAction.countDown()
            executor.shutdownNow()
        }
    }

    @Test
    fun firstPermissionAttemptUsesRuntimeDialog() {
        assertEquals(
            MicrophonePermissionAction.RequestPermission,
            microphonePermissionAction(
                granted = false,
                previouslyRequested = false,
                shouldShowRationale = false,
            ),
        )
    }

    @Test
    fun permanentDenialOpensApplicationSettings() {
        assertEquals(
            MicrophonePermissionAction.OpenAppSettings,
            microphonePermissionAction(
                granted = false,
                previouslyRequested = true,
                shouldShowRationale = false,
            ),
        )
    }

    @Test
    fun ordinaryDenialRetriesRuntimeDialog() {
        assertEquals(
            MicrophonePermissionAction.RequestPermission,
            microphonePermissionAction(
                granted = false,
                previouslyRequested = true,
                shouldShowRationale = true,
            ),
        )
    }

    @Test
    fun inactiveSessionCannotReachToolApi() = runBlocking {
        val gate = VoiceActivationGate()
        gate.setEnabled(true)
        val staleSession = gate.beginSession()
        gate.setEnabled(false)
        gate.setEnabled(true)
        gate.beginSession()
        var apiCalls = 0
        val api = proxyApi {
            apiCalls += 1
            error("tool API must not be called")
        }
        val bridge = FunctionBridge(api) { start ->
            gate.runIfActive(staleSession, start)
        }

        val result = bridge.execute("set_speed", emptyMap())

        assertEquals(0, apiCalls)
        assertEquals("Voice session is no longer active", result.response)
    }

    @Test
    fun toolCancellationIsNotConvertedIntoAResult() {
        val api = proxyApi { throw CancellationException("disabled") }
        val bridge = FunctionBridge(api)

        assertThrows(CancellationException::class.java) {
            runBlocking { bridge.execute("set_speed", emptyMap()) }
        }
    }

    @Test
    fun disableCannotInterleaveBeforeToolRequestStartAndDoesNotWaitForResponse() {
        val gate = VoiceActivationGate()
        gate.setEnabled(true)
        val session = gate.beginSession()
        val authorized = CountDownLatch(1)
        val releaseStart = CountDownLatch(1)
        val disableStarted = CountDownLatch(1)
        val requestStarted = CountDownLatch(1)
        val sequence = AtomicInteger()
        val requestStartOrder = AtomicInteger()
        val disableOrder = AtomicInteger()
        val call = PendingToolCall(requestStarted) {
            requestStartOrder.set(sequence.incrementAndGet())
        }
        val api = Proxy.newProxyInstance(
            TreadmillApi::class.java.classLoader,
            arrayOf(TreadmillApi::class.java),
        ) { _, method, _ ->
            if (method.name == "execToolCall") call else error("Unexpected API call: ${method.name}")
        } as TreadmillApi
        val bridge = FunctionBridge(
            api,
            authorizeAndStart = { start ->
                gate.runIfActive(session) {
                    authorized.countDown()
                    releaseStart.await()
                    start()
                }
            },
        )
        val executor = Executors.newFixedThreadPool(2)

        try {
            val execution = executor.submit<FunctionBridge.FunctionResult> {
                runBlocking { bridge.execute("set_speed", emptyMap()) }
            }
            assertTrue(authorized.await(1, TimeUnit.SECONDS))

            val disable = executor.submit<Boolean> {
                disableStarted.countDown()
                gate.setEnabled(false).also { disableOrder.set(sequence.incrementAndGet()) }
            }
            assertTrue(disableStarted.await(1, TimeUnit.SECONDS))
            assertThrows(TimeoutException::class.java) {
                disable.get(100, TimeUnit.MILLISECONDS)
            }

            releaseStart.countDown()
            assertTrue(requestStarted.await(1, TimeUnit.SECONDS))
            assertTrue(disable.get(1, TimeUnit.SECONDS))
            assertTrue(requestStartOrder.get() < disableOrder.get())
            assertFalse("disable must not wait for the HTTP response", execution.isDone)

            call.respond()
            assertEquals("Done", execution.get(1, TimeUnit.SECONDS).response)
        } finally {
            releaseStart.countDown()
            executor.shutdownNow()
        }
    }

    private fun proxyApi(block: () -> Any?): TreadmillApi =
        Proxy.newProxyInstance(
            TreadmillApi::class.java.classLoader,
            arrayOf(TreadmillApi::class.java),
        ) { _, _, _ -> block() } as TreadmillApi

    private class PendingToolCall(
        private val started: CountDownLatch,
        private val onStarted: () -> Unit,
    ) : Call<ToolCallResponse> {
        private val executed = AtomicBoolean(false)
        private val canceled = AtomicBoolean(false)
        private lateinit var callback: Callback<ToolCallResponse>

        override fun enqueue(callback: Callback<ToolCallResponse>) {
            check(executed.compareAndSet(false, true))
            this.callback = callback
            onStarted()
            started.countDown()
        }

        fun respond() {
            callback.onResponse(this, Response.success(ToolCallResponse(ok = true, result = "Done")))
        }

        override fun execute(): Response<ToolCallResponse> = error("synchronous execution not expected")
        override fun isExecuted(): Boolean = executed.get()
        override fun cancel() { canceled.set(true) }
        override fun isCanceled(): Boolean = canceled.get()
        override fun clone(): Call<ToolCallResponse> = PendingToolCall(started, onStarted)
        override fun request(): Request = Request.Builder().url("https://example.invalid/api/tool").build()
        override fun timeout(): Timeout = Timeout.NONE
    }
}
