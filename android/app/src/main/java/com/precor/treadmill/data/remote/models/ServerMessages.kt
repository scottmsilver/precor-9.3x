package com.precor.treadmill.data.remote.models

import kotlinx.serialization.DeserializationStrategy
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonContentPolymorphicSerializer
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

@Serializable(with = ServerMessageSerializer::class)
sealed interface ServerMessage

object ServerMessageSerializer : JsonContentPolymorphicSerializer<ServerMessage>(ServerMessage::class) {
    override fun selectDeserializer(element: JsonElement): DeserializationStrategy<ServerMessage> {
        return when (element.jsonObject["type"]?.jsonPrimitive?.content) {
            "kv" -> KVMessage.serializer()
            "status" -> StatusMessage.serializer()
            "session" -> SessionMessage.serializer()
            "program" -> ProgramMessage.serializer()
            "connection" -> ConnectionMessage.serializer()
            "hr" -> HRMessage.serializer()
            "scan_result" -> ScanResultMessage.serializer()
            else -> throw IllegalArgumentException(
                "Unknown server message type: ${element.jsonObject["type"]}"
            )
        }
    }
}

@Serializable
data class KVMessage(
    val type: String = "kv",
    val source: String,
    val key: String,
    val value: String,
    val ts: Double? = null,
) : ServerMessage

@Serializable
data class StatusMessage(
    val type: String = "status",
    val proxy: Boolean,
    val emulate: Boolean,
    @SerialName("emu_speed") val emuSpeed: Int,
    @SerialName("emu_speed_mph") val emuSpeedMph: Double,
    @SerialName("emu_incline") val emuIncline: Double,
    val speed: Double? = null,
    val incline: Double? = null,
    val motor: Map<String, String> = emptyMap(),
    @SerialName("treadmill_connected") val treadmillConnected: Boolean,
    @SerialName("heart_rate") val heartRate: Int = 0,
    @SerialName("hrm_connected") val hrmConnected: Boolean = false,
    @SerialName("hrm_device") val hrmDevice: String = "",
) : ServerMessage

@Serializable
data class SessionMessage(
    val type: String = "session",
    val active: Boolean,
    val elapsed: Double,
    val distance: Double,
    @SerialName("vert_feet") val vertFeet: Double,
    @SerialName("wall_started_at") val wallStartedAt: String,
    @SerialName("end_reason") val endReason: String? = null,
) : ServerMessage

@Serializable
data class Interval(
    val name: String,
    val duration: Int,
    val speed: Double,
    val incline: Double,
)

@Serializable
data class Program(
    val name: String,
    val manual: Boolean? = null,
    val intervals: List<Interval>,
)

@Serializable
data class ProgramMessage(
    val type: String = "program",
    val program: Program? = null,
    val running: Boolean,
    val paused: Boolean,
    val completed: Boolean,
    @SerialName("current_interval") val currentInterval: Int,
    @SerialName("interval_elapsed") val intervalElapsed: Double,
    @SerialName("total_elapsed") val totalElapsed: Double,
    @SerialName("total_duration") val totalDuration: Double,
    val encouragement: String? = null,
) : ServerMessage

@Serializable
data class ConnectionMessage(
    val type: String = "connection",
    val connected: Boolean,
) : ServerMessage

@Serializable
data class HRMessage(
    val type: String = "hr",
    val bpm: Int,
    val connected: Boolean,
    val device: String = "",
    val address: String = "",
) : ServerMessage

@Serializable
data class HrmDevice(
    val address: String,
    val name: String,
    val rssi: Int = 0,
)

@Serializable
data class ScanResultMessage(
    val type: String = "scan_result",
    val devices: List<HrmDevice>,
) : ServerMessage
