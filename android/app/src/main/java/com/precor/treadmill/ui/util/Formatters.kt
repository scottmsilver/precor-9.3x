package com.precor.treadmill.ui.util

import androidx.compose.ui.graphics.Color
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.round

/** Format seconds as m:ss or h:mm:ss. */
fun fmtDur(secs: Number?): String {
    val s = max(0, (secs?.toDouble() ?: 0.0).toInt())
    val m = s / 60
    val sec = s % 60
    return if (m >= 60) {
        val h = m / 60
        "$h:${(m % 60).toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}"
    } else {
        "$m:${sec.toString().padStart(2, '0')}"
    }
}

/** Format speed as pace mm:ss min/mi. */
fun paceDisplay(speedMph: Double): String {
    if (speedMph <= 0.0) return "--:--"
    val minPerMile = 60.0 / speedMph
    val m = floor(minPerMile).toInt()
    val s = round((minPerMile - m) * 60).toInt()
    return "$m:${s.toString().padStart(2, '0')}"
}

/** Compute interval color based on name and intensity. */
fun ivColor(name: String?, speed: Double, incline: Int): Color {
    if (name == null) return Color(0x4D6BC89B) // rgba(107,200,155,0.3)
    val n = name.lowercase()
    if ("warm" in n || "cool" in n) return Color(0x666BC89B) // rgba(107,200,155,0.4)
    val t = (speed / 12.0 + incline / 15.0) / 2.0
    val alpha = (0.3 + t * 0.7).coerceIn(0.0, 1.0)
    return Color(
        red = 107 / 255f,
        green = 200 / 255f,
        blue = 155 / 255f,
        alpha = alpha.toFloat()
    )
}
