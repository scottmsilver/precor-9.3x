package com.precor.treadmill.ui.util

import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.BaselineShift
import androidx.compose.ui.text.withStyle
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.round

/** Raise colons for vertical centering in timer displays. */
fun timerText(text: String): AnnotatedString = buildAnnotatedString {
    for (ch in text) {
        if (ch == ':') {
            withStyle(SpanStyle(baselineShift = BaselineShift(0.15f))) {
                append(':')
            }
        } else {
            append(ch)
        }
    }
}

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
    // Matches sim.jsx fmtPace: <0.4 mph reads as no pace; carry s==60 to next minute.
    if (speedMph < 0.4) return "--:--"
    val minPerMile = 60.0 / speedMph
    val m = floor(minPerMile).toInt()
    val s = round((minPerMile - m) * 60).toInt()
    return if (s == 60) "${m + 1}:00" else "$m:${s.toString().padStart(2, '0')}"
}

private val glowPurple = Color(0xFF8B7FA0)

/** Parse <<word>> glow markup into an AnnotatedString with purple glow effect. */
fun glowText(text: String): AnnotatedString = buildAnnotatedString {
    val regex = Regex("<<(.+?)>>")
    var cursor = 0
    for (match in regex.findAll(text)) {
        append(text.substring(cursor, match.range.first))
        withStyle(SpanStyle(
            color = glowPurple,
            fontWeight = FontWeight.SemiBold,
            shadow = Shadow(
                color = glowPurple.copy(alpha = 0.6f),
                offset = Offset.Zero,
                blurRadius = 12f,
            ),
        )) {
            append(match.groupValues[1])
        }
        cursor = match.range.last + 1
    }
    if (cursor < text.length) append(text.substring(cursor))
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
