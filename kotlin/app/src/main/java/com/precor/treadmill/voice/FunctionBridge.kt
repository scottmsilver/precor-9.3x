package com.precor.treadmill.voice

import android.util.Log
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.models.ToolCallRequest
import com.precor.treadmill.data.remote.models.ToolCallResponse
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.JsonElement
import retrofit2.Call
import retrofit2.Callback
import retrofit2.Response
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Forwards Gemini function calls to the server via /api/tool.
 *
 * All tool execution lives in server.py's _exec_fn() — the single source of
 * truth. This bridge just forwards the call and returns the result.
 */
class FunctionBridge(
    private val api: TreadmillApi,
    private val authorizeAndStart: ((() -> Unit) -> Boolean) = { start ->
        start()
        true
    },
) {

    companion object {
        private const val TAG = "FunctionBridge"
    }

    data class FunctionResult(
        val name: String,
        val response: String,
    )

    suspend fun execute(
        name: String,
        args: Map<String, JsonElement>,
        context: String? = null,
    ): FunctionResult = suspendCancellableCoroutine { continuation ->
        val started = authorizeAndStart {
            val call = api.execToolCall(ToolCallRequest(name, args, context))
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback<ToolCallResponse> {
                override fun onResponse(
                    call: Call<ToolCallResponse>,
                    response: Response<ToolCallResponse>,
                ) {
                    if (!continuation.isActive) return
                    val body = response.body()
                    val result = when {
                        !response.isSuccessful -> "Error executing $name: HTTP ${response.code()}"
                        body == null -> "Error executing $name: empty response"
                        body.ok -> body.result ?: "Done"
                        else -> "Error: ${body.error ?: "unknown"}"
                    }
                    continuation.resume(FunctionResult(name = name, response = result))
                }

                override fun onFailure(
                    call: Call<ToolCallResponse>,
                    error: Throwable,
                ) {
                    if (!continuation.isActive) return
                    if (error is CancellationException) {
                        continuation.resumeWithException(error)
                        return
                    }
                    Log.e(TAG, "Error executing $name", error)
                    continuation.resume(
                        FunctionResult(name = name, response = "Error executing $name: ${error.message}"),
                    )
                }
            })
        }
        if (!started && continuation.isActive) {
            continuation.resume(
                FunctionResult(name = name, response = "Voice session is no longer active"),
            )
        }
    }
}
