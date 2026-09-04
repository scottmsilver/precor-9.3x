package com.precor.treadmill.ui.viewmodel

import com.precor.treadmill.data.remote.TreadmillApi
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
        var apiCalls = 0
        val api = proxyApi {
            apiCalls += 1
            error("tool API must not be called")
        }
        val bridge = FunctionBridge(api, isExecutionAllowed = { false })

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

    private fun proxyApi(block: () -> Any?): TreadmillApi =
        Proxy.newProxyInstance(
            TreadmillApi::class.java.classLoader,
            arrayOf(TreadmillApi::class.java),
        ) { _, _, _ -> block() } as TreadmillApi
}
