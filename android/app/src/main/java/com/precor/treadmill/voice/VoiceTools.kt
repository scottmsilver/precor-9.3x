package com.precor.treadmill.voice

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Tool declarations for Gemini Live API function calling.
 * Mirrors TOOL_DECLARATIONS from voiceTools.ts / program_engine.py.
 */

@Serializable
data class FunctionParameter(
    val type: String,
    val description: String? = null,
    val items: JsonElement? = null,
)

@Serializable
data class FunctionParameters(
    val type: String = "OBJECT",
    val properties: Map<String, FunctionParameter>,
    val required: List<String>? = null,
)

@Serializable
data class FunctionDeclaration(
    val name: String,
    val description: String,
    val parameters: FunctionParameters,
)

val TOOL_DECLARATIONS: List<FunctionDeclaration> = listOf(
    FunctionDeclaration(
        name = "set_speed",
        description = "Set treadmill belt speed",
        parameters = FunctionParameters(
            properties = mapOf(
                "mph" to FunctionParameter(type = "NUMBER", description = "Speed in mph (0-12)"),
            ),
            required = listOf("mph"),
        ),
    ),
    FunctionDeclaration(
        name = "set_incline",
        description = "Set treadmill incline grade",
        parameters = FunctionParameters(
            properties = mapOf(
                "incline" to FunctionParameter(type = "NUMBER", description = "Incline percent (0-15)"),
            ),
            required = listOf("incline"),
        ),
    ),
    FunctionDeclaration(
        name = "start_workout",
        description = "Generate and start an interval training program",
        parameters = FunctionParameters(
            properties = mapOf(
                "description" to FunctionParameter(type = "STRING", description = "Workout description"),
            ),
            required = listOf("description"),
        ),
    ),
    FunctionDeclaration(
        name = "stop_treadmill",
        description = "Stop the treadmill and end any running program",
        parameters = FunctionParameters(
            properties = emptyMap(),
        ),
    ),
    FunctionDeclaration(
        name = "pause_program",
        description = "Pause the running interval program",
        parameters = FunctionParameters(
            properties = emptyMap(),
        ),
    ),
    FunctionDeclaration(
        name = "resume_program",
        description = "Resume a paused program",
        parameters = FunctionParameters(
            properties = emptyMap(),
        ),
    ),
    FunctionDeclaration(
        name = "skip_interval",
        description = "Skip to next interval in program",
        parameters = FunctionParameters(
            properties = emptyMap(),
        ),
    ),
    FunctionDeclaration(
        name = "extend_interval",
        description = "Add or subtract seconds from the current interval duration. Positive = longer, negative = shorter. Min 10s.",
        parameters = FunctionParameters(
            properties = mapOf(
                "seconds" to FunctionParameter(type = "NUMBER", description = "Seconds to add (positive) or subtract (negative)"),
            ),
            required = listOf("seconds"),
        ),
    ),
    FunctionDeclaration(
        name = "add_time",
        description = "Add extra intervals at the end of the running program",
        parameters = FunctionParameters(
            properties = mapOf(
                "intervals" to FunctionParameter(
                    type = "ARRAY",
                    description = "Array of interval objects with name, duration (seconds), speed (mph), incline (%)",
                    items = JsonObject(
                        mapOf(
                            "type" to JsonPrimitive("OBJECT"),
                            "properties" to JsonObject(
                                mapOf(
                                    "name" to JsonObject(mapOf("type" to JsonPrimitive("STRING"))),
                                    "duration" to JsonObject(mapOf("type" to JsonPrimitive("NUMBER"))),
                                    "speed" to JsonObject(mapOf("type" to JsonPrimitive("NUMBER"))),
                                    "incline" to JsonObject(mapOf("type" to JsonPrimitive("NUMBER"))),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            required = listOf("intervals"),
        ),
    ),
)

val VOICE_SYSTEM_PROMPT = """
You are an AI treadmill coach. You control a Precor treadmill via function calls.
Be brief, friendly, motivating. Respond in 1-3 short sentences max.
Feel free to use emoji in your text responses when it feels natural.

Tools:
- set_speed: change speed (mph). Use 0 to stop belt.
- set_incline: change incline (0-15%)
- start_workout: create & start an interval program from a description
- stop_treadmill: emergency stop (speed 0, incline 0, end program)
- pause_program / resume_program: pause/resume interval programs
- skip_interval: skip to next interval
- extend_interval: add or subtract seconds from current interval (positive = longer, negative = shorter)
- add_time: add extra intervals at the end of the current program

CRITICAL RULE — never change speed, incline, or any treadmill setting unless the user explicitly asks you to. Do NOT proactively adjust settings to "push" or "challenge" the user. Only use tools in direct response to a clear user request.

Guidelines:
- For workout requests, use start_workout with a detailed description
- For simple adjustments ("faster", "more incline"), use set_speed/set_incline
- Walking: 2-4 mph. Jogging: 4-6 mph. Running: 6+ mph
- If user says "stop", use stop_treadmill immediately
- For "more time", "extend", "add 5 minutes" etc., use extend_interval or add_time
- extend_interval changes the CURRENT interval's duration (e.g. +60 adds 1 min)
- add_time appends new intervals at the END of the program
- Always confirm what you did briefly
- You can wrap a single important word in <<double angle brackets>> to give it an animated glow effect in the UI. Use sparingly for emphasis.
""".trimIndent()

val VOICE_SMARTASS_ADDENDUM = """

SMART-ASS MODE: Be sarcastic, witty, and make fun of the user for being lazy.
Roast them (lovingly) about their pace, breaks, or workout choices.
Still be helpful and encouraging underneath the sass.
""".trimIndent()
