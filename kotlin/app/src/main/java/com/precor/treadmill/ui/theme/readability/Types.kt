package com.precor.treadmill.ui.theme.readability

enum class Role { HERO, BODY, MUTED }

data class RegionStats(val id: String, val role: Role, val avg: Rgb, val dominant: Rgb, val luma: Double)
data class TintCandidate(val color: Rgb, val paletteDistance: Double)
data class Theme(val tint: Rgb, val text: Rgb, val blurDp: Double, val baseScrimAlpha: Double)
data class AdvicePrior(
    val paletteHue: Double? = null,
    val suggestedPolarity: String? = null,
    val mood: String? = null,
)
data class BeautyWeights(val scrim: Double = 1.0, val blur: Double = 0.5, val palette: Double = 0.6, val charcoalOnDark: Double = 0.3)

val ROLE_TARGET_LC = mapOf(Role.HERO to 75.0, Role.BODY to 60.0, Role.MUTED to 45.0)
val IVORY = Rgb(242.0, 236.0, 223.0)
val CHARCOAL = Rgb(30.0, 32.0, 30.0)
val SCRIM_STEPS = listOf(0.18, 0.28, 0.38, 0.5, 0.62)
val BLUR_STEPS = listOf(0.0, 1.0, 2.0, 3.0)
const val MAX_REGION_SCRIM = 0.72
