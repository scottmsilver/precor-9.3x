@file:OptIn(ExperimentalTextApi::class)

package com.precor.treadmill.ui.screens.running

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import com.precor.treadmill.R
import kotlin.math.max
import kotlin.math.min

/**
 * Space Grotesk — UI/labels/eyebrows. Variable font (weight axis), vendored OFL
 * at res/font/space_grotesk.ttf (Google Fonts).
 */
val RidgelineLabelFamily = FontFamily(
    Font(
        R.font.space_grotesk,
        weight = FontWeight.Bold,
        variationSettings = FontVariation.Settings(FontVariation.Setting("wght", 700f)),
    ),
    Font(
        R.font.space_grotesk,
        weight = FontWeight.SemiBold,
        variationSettings = FontVariation.Settings(FontVariation.Setting("wght", 600f)),
    ),
    Font(
        R.font.space_grotesk,
        weight = FontWeight.Medium,
        variationSettings = FontVariation.Settings(FontVariation.Setting("wght", 500f)),
    ),
    Font(
        R.font.space_grotesk,
        weight = FontWeight.Normal,
        variationSettings = FontVariation.Settings(FontVariation.Setting("wght", 400f)),
    ),
)

/**
 * Azeret Mono — every numeric value (metrics, NEXT, steppers, strip labels). Variable
 * font (weight axis), vendored OFL at res/font/azeret_mono.ttf (Google Fonts). Use with
 * `fontFeatureSettings = "tnum"` for tabular alignment.
 */
val RidgelineMonoFamily = FontFamily(
    Font(
        R.font.azeret_mono,
        weight = FontWeight.SemiBold,
        variationSettings = FontVariation.Settings(FontVariation.Setting("wght", 600f)),
    ),
    Font(
        R.font.azeret_mono,
        weight = FontWeight.Medium,
        variationSettings = FontVariation.Settings(FontVariation.Setting("wght", 500f)),
    ),
    Font(
        R.font.azeret_mono,
        weight = FontWeight.Normal,
        variationSettings = FontVariation.Settings(FontVariation.Setting("wght", 400f)),
    ),
)

/**
 * Ridgeline HUD "Trail" theme tokens + steepness color ramp.
 *
 * These are the design's base hues/values; over the background photo they are routed through
 * the photo-sampler legibility system (LegibleGlassPanel / LegibleText / Color.legibleOn) so
 * panels dim adaptively and colored values clear APCA. All token values are ported verbatim
 * from the design handoff (`hud/sim.jsx` THEMES.trail + README).
 */
object RidgelineTheme {
    val bg = Color(0xFF070B0E)
    val glow = Color(0xFF10201A)
    val fg = Color(0xFFEEF4F1)
    val dim = Color(0xFF71837F)
    val dim2 = Color(0xFF4A5A5B)
    val line = Color(0xFFFFFFFF).copy(alpha = 0.085f)
    val line2 = Color(0xFFFFFFFF).copy(alpha = 0.16f)
    // README pill uses 0.5 for legibility; sim uses 0.22. Use 0.5.
    val pillBg = Color(0xFF090F12).copy(alpha = 0.5f)
    val trailDim = Color(0xFF3C4D4A)
    val accent = Color(0xFF54FFAB)      // live / now (green)
    val accentDim = Color(0xFF1F6E4D)
    val elev = Color(0xFFFFB35C)        // elevation / summit (amber)
    val elevDim = Color(0xFF7A5526)
    // contour "major" line color from DirectionD.jsx (#2f7053) and minor uses greenDim/accentDim
    val contourMajor = Color(0xFF2F7053)

    // Steepness ramp (earth): [gradePct, r, g, b]
    private val rampStops = arrayOf(
        floatArrayOf(0f, 111f, 150f, 104f),
        floatArrayOf(3f, 150f, 168f, 100f),
        floatArrayOf(6f, 198f, 168f, 96f),
        floatArrayOf(9f, 195f, 132f, 77f),
        floatArrayOf(12f, 174f, 101f, 66f),
        floatArrayOf(14f, 154f, 74f, 60f),
    )

    /** Color for an incline grade (clamped 0..14, linearly interpolated between ramp stops). */
    fun gradeColor(gradePct: Double): Color {
        val g = max(0.0, min(14.0, gradePct)).toFloat()
        for (i in 0 until rampStops.size - 1) {
            val s0 = rampStops[i]
            val s1 = rampStops[i + 1]
            if (g <= s1[0]) {
                val t = if (s1[0] == s0[0]) 0f else (g - s0[0]) / (s1[0] - s0[0])
                return Color(
                    red = (s0[1] + (s1[1] - s0[1]) * t) / 255f,
                    green = (s0[2] + (s1[2] - s0[2]) * t) / 255f,
                    blue = (s0[3] + (s1[3] - s0[3]) * t) / 255f,
                )
            }
        }
        val last = rampStops.last()
        return Color(last[1] / 255f, last[2] / 255f, last[3] / 255f)
    }

    private const val SPD_EASY = 2.5
    private const val SPD_HARD = 7.0

    /** Speed carries its own hardness on the same ramp (faster = harder = redder). */
    fun speedColor(mph: Double): Color {
        val t = (mph - SPD_EASY) / (SPD_HARD - SPD_EASY)
        return gradeColor(max(0.0, min(14.0, t * 14.0)))
    }
}

/** Format seconds as M:SS or H:MM:SS (matches sim.jsx fmtTime). */
fun ridgelineFmtTime(secs: Double): String {
    val s = max(0, secs.toInt())
    val h = s / 3600
    val m = (s % 3600) / 60
    val ss = s % 60
    return if (h > 0) "%d:%02d:%02d".format(h, m, ss) else "%d:%02d".format(m, ss)
}
