package com.precor.treadmill.data.remote.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

// --- Request models ---

@Serializable
data class SpeedRequest(val value: Double)

@Serializable
data class InclineRequest(val value: Double)

@Serializable
data class EmulateRequest(val enabled: Boolean)

@Serializable
data class ProxyRequest(val enabled: Boolean)

@Serializable
data class GenerateRequest(val prompt: String)

@Serializable
data class QuickStartRequest(
    val speed: Double = 3.0,
    val incline: Double = 0.0,
    @SerialName("duration_minutes") val durationMinutes: Int = 60,
)

@Serializable
data class AdjustDurationRequest(
    @SerialName("delta_seconds") val deltaSeconds: Int,
)

@Serializable
data class ExtendRequest(val seconds: Int)

@Serializable
data class ChatRequest(
    val message: String,
    val smartass: Boolean = false,
)

@Serializable
data class VoiceChatRequest(
    val audio: String,
    @SerialName("mime_type") val mimeType: String,
    val smartass: Boolean = false,
)

@Serializable
data class ExtractIntentRequest(
    val text: String,
    @SerialName("already_executed") val alreadyExecuted: List<String> = emptyList(),
)

@Serializable
data class TtsRequest(
    val text: String,
    val voice: String = "Kore",
)

@Serializable
data class HrmSelectRequest(val address: String)

@Serializable
data class HrmStatusResponse(
    @SerialName("heart_rate") val heartRate: Int = 0,
    val connected: Boolean = false,
    val device: String = "",
    @SerialName("available_devices") val availableDevices: List<HrmDevice> = emptyList(),
)

// --- Response models ---

@Serializable
data class GenericOkResponse(
    val ok: Boolean,
    val error: String? = null,
)

@Serializable
data class GenerateResponse(
    val ok: Boolean,
    val program: Program? = null,
    val error: String? = null,
)

@Serializable
data class LoadHistoryResponse(
    val ok: Boolean,
    val program: Program? = null,
    val error: String? = null,
)

@Serializable
data class GpxUploadResponse(
    val ok: Boolean,
    val program: Program? = null,
    val error: String? = null,
)

@Serializable
data class ChatAction(
    val name: String,
    val args: Map<String, JsonElement>,
    val result: String,
)

@Serializable
data class ChatResponse(
    val text: String,
    val actions: List<ChatAction>,
    val transcription: String? = null,
)

@Serializable
data class ExtractIntentResponse(
    val actions: List<ChatAction>,
    val text: String,
)

@Serializable
data class HistoryEntry(
    val id: String,
    val prompt: String,
    val program: Program,
    @SerialName("created_at") val createdAt: String,
    @SerialName("total_duration") val totalDuration: Double,
)

@Serializable
data class AppConfig(
    @SerialName("gemini_api_key") val geminiApiKey: String,
    @SerialName("gemini_model") val geminiModel: String,
    @SerialName("gemini_live_model") val geminiLiveModel: String,
    @SerialName("gemini_voice") val geminiVoice: String,
)

@Serializable
data class LogResponse(val lines: List<String>)

@Serializable
data class VoicePromptResponse(val prompt: String)

@Serializable
data class TtsResponse(
    val ok: Boolean,
    val audio: String? = null,
    @SerialName("sample_rate") val sampleRate: Int? = null,
    val error: String? = null,
)
