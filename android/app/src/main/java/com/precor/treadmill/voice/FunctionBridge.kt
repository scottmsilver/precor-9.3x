package com.precor.treadmill.voice

import android.util.Log
import com.precor.treadmill.data.remote.TreadmillApi
import com.precor.treadmill.data.remote.models.*
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive

/**
 * Maps Gemini function calls to REST API calls.
 * Port of functionBridge.ts.
 */
class FunctionBridge(private val api: TreadmillApi) {

    companion object {
        private const val TAG = "FunctionBridge"
    }

    data class FunctionResult(
        val name: String,
        val response: String,
    )

    suspend fun execute(name: String, args: Map<String, JsonElement>): FunctionResult {
        val result = try {
            when (name) {
                "set_speed" -> {
                    val mph = args["mph"]?.jsonPrimitive?.double ?: 0.0
                    api.setSpeed(SpeedRequest(mph))
                    "Speed set to $mph mph"
                }

                "set_incline" -> {
                    val incline = args["incline"]?.jsonPrimitive?.double ?: 0.0
                    api.setIncline(InclineRequest(incline))
                    "Incline set to $incline%"
                }

                "start_workout" -> {
                    val description = args["description"]?.jsonPrimitive?.content ?: ""
                    val gen = api.generateProgram(GenerateRequest(description))
                    if (gen.ok) {
                        api.startProgram()
                        "Workout program started"
                    } else {
                        "Error generating program: ${gen.error ?: "unknown"}"
                    }
                }

                "stop_treadmill" -> {
                    api.setSpeed(SpeedRequest(0.0))
                    api.setIncline(InclineRequest(0.0))
                    api.stopProgram()
                    "Treadmill stopped"
                }

                "pause_program" -> {
                    api.pauseProgram()
                    "Program paused"
                }

                "resume_program" -> {
                    api.pauseProgram() // toggle pause
                    "Program resumed"
                }

                "skip_interval" -> {
                    api.skipInterval()
                    "Skipped to next interval"
                }

                "extend_interval" -> {
                    val seconds = args["seconds"]?.jsonPrimitive?.int ?: 0
                    api.extendInterval(ExtendRequest(seconds))
                    "Interval extended by $seconds seconds"
                }

                "add_time" -> {
                    val intervals = args["intervals"]?.jsonArray?.toString() ?: "[]"
                    val resp = api.sendChat(
                        ChatRequest("[function_result] add_time with intervals: $intervals")
                    )
                    resp.text.ifEmpty { "Time added" }
                }

                else -> "Unknown function: $name"
            }
        } catch (e: Exception) {
            Log.e(TAG, "Error executing $name", e)
            "Error executing $name: ${e.message}"
        }

        return FunctionResult(name = name, response = result)
    }
}
