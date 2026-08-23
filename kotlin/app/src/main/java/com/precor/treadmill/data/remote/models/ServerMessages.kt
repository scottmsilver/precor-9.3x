package com.precor.treadmill.data.remote.models

import android.util.Log
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
            "profile_changed" -> ProfileChangedMessage.serializer()
            else -> {
                Log.w("ServerMsg", "Unknown message type: ${element.jsonObject["type"]}")
                UnknownMessage.serializer()
            }
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
    @Serializable(with = LenientBoolSerializer::class) val proxy: Boolean,
    @Serializable(with = LenientBoolSerializer::class) val emulate: Boolean,
    @Serializable(with = LenientIntSerializer::class) @SerialName("emu_speed") val emuSpeed: Int,
    @Serializable(with = LenientDoubleSerializer::class) @SerialName("emu_speed_mph") val emuSpeedMph: Double,
    @Serializable(with = LenientDoubleSerializer::class) @SerialName("emu_incline") val emuIncline: Double,
    val speed: Double? = null,
    val incline: Double? = null,
    val motor: Map<String, String> = emptyMap(),
    @Serializable(with = LenientBoolSerializer::class) @SerialName("treadmill_connected") val treadmillConnected: Boolean,
    @Serializable(with = LenientIntSerializer::class) @SerialName("heart_rate") val heartRate: Int = 0,
    @Serializable(with = LenientBoolSerializer::class) @SerialName("hrm_connected") val hrmConnected: Boolean = false,
    @SerialName("hrm_device") val hrmDevice: String = "",
) : ServerMessage

@Serializable
data class SessionMessage(
    val type: String = "session",
    @Serializable(with = LenientBoolSerializer::class) val active: Boolean,
    @Serializable(with = LenientDoubleSerializer::class) val elapsed: Double,
    @Serializable(with = LenientDoubleSerializer::class) val distance: Double,
    @Serializable(with = LenientDoubleSerializer::class) @SerialName("vert_feet") val vertFeet: Double,
    @Serializable(with = LenientDoubleSerializer::class) val calories: Double = 0.0,
    @SerialName("wall_started_at") val wallStartedAt: String,
    @SerialName("end_reason") val endReason: String? = null,
) : ServerMessage

@Serializable
data class Interval(
    val name: String,
    @Serializable(with = LenientDoubleSerializer::class) val duration: Double,
    @Serializable(with = LenientDoubleSerializer::class) val speed: Double,
    @Serializable(with = LenientDoubleSerializer::class) val incline: Double,
)

@Serializable
data class Program(
    val name: String,
    @Serializable(with = LenientNullableBoolSerializer::class) val manual: Boolean? = null,
    val intervals: List<Interval>,
)

@Serializable
data class ProgramMessage(
    val type: String = "program",
    val program: Program? = null,
    @Serializable(with = LenientBoolSerializer::class) val running: Boolean,
    @Serializable(with = LenientBoolSerializer::class) val paused: Boolean,
    @Serializable(with = LenientBoolSerializer::class) val completed: Boolean,
    @Serializable(with = LenientIntSerializer::class) @SerialName("current_interval") val currentInterval: Int,
    @Serializable(with = LenientDoubleSerializer::class) @SerialName("interval_elapsed") val intervalElapsed: Double,
    @Serializable(with = LenientDoubleSerializer::class) @SerialName("total_elapsed") val totalElapsed: Double,
    @Serializable(with = LenientDoubleSerializer::class) @SerialName("total_duration") val totalDuration: Double,
    val encouragement: String? = null,
) : ServerMessage

@Serializable
data class ConnectionMessage(
    val type: String = "connection",
    @Serializable(with = LenientBoolSerializer::class) val connected: Boolean,
) : ServerMessage

@Serializable
data class HRMessage(
    val type: String = "hr",
    val bpm: Int,
    @Serializable(with = LenientBoolSerializer::class) val connected: Boolean,
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

@Serializable
data class ProfileChangedMessage(
    val type: String = "profile_changed",
    val profile: Profile? = null,
    @Serializable(with = LenientBoolSerializer::class) @SerialName("guest_mode") val guestMode: Boolean = false,
) : ServerMessage

@Serializable
data class UnknownMessage(val type: String? = null) : ServerMessage
