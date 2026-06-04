package com.precor.treadmill.ui.theme.readability

import kotlin.math.abs
import kotlin.math.min

data class RegionFit(val scrimAlpha: Double, val lc: Double, val met: Boolean)
data class ThemeChoice(val theme: Theme, val cost: Double, val runnersUp: List<Pair<Theme, Double>>)

private fun clamp255(x: Double) = maxOf(0.0, minOf(255.0, x))

private val NEUTRAL_FALLBACK = Theme(Rgb(18.0, 18.0, 18.0), IVORY, 2.0, MAX_REGION_SCRIM)

fun harmonizePalette(globalDominant: Rgb, prior: AdvicePrior): List<TintCandidate> {
    val domH = rgbToOklch(globalDominant).h
    val hues = mutableListOf(domH)
    prior.paletteHue?.let { hues.add(it) }
    hues.add((domH + 180) % 360)
    val out = hues.map {
        val raw = oklchToRgb(Oklch(0.26, 0.05, it))
        val color = Rgb(clamp255(raw.r), clamp255(raw.g), clamp255(raw.b))
        TintCandidate(color, oklabDeltaE(rgbToOklab(color), rgbToOklab(globalDominant)))
    }.toMutableList()
    out.add(TintCandidate(Rgb(26.0,26.0,26.0), 0.0))
    return out
}

private fun moodOf(prior: AdvicePrior, avgLuma: Double): String =
    when {
        prior.mood?.contains("dark") == true || avgLuma < 70 -> "dark"
        prior.mood?.contains("cool") == true -> "cool"
        prior.mood?.contains("warm") == true -> "warm"
        else -> "neutral"
    }

fun beautyCost(theme: Theme, palette: List<TintCandidate>, w: BeautyWeights, mood: String): Double {
    val tintDist = palette.minOf { oklabDeltaE(rgbToOklab(it.color), rgbToOklab(theme.tint)) }
    var cost = w.scrim*theme.baseScrimAlpha + w.blur*(theme.blurDp/3.0) + w.palette*tintDist
    val textLuma = 0.299*theme.text.r + 0.587*theme.text.g + 0.114*theme.text.b
    val charcoalText = textLuma < 128
    if (charcoalText && mood == "dark") cost += w.charcoalOnDark
    return cost
}

private fun composite(bg: Rgb, tint: Rgb, a: Double) =
    Rgb(bg.r*(1-a)+tint.r*a, bg.g*(1-a)+tint.g*a, bg.b*(1-a)+tint.b*a)

fun fitRegion(theme: Theme, region: RegionStats): RegionFit {
    val target = ROLE_TARGET_LC.getValue(region.role)
    var alpha = theme.baseScrimAlpha
    while (alpha <= MAX_REGION_SCRIM + 1e-9) {
        val a = min(alpha, MAX_REGION_SCRIM)
        val lc = abs(apcaLc(theme.text, composite(region.avg, theme.tint, a)))
        if (lc >= target) return RegionFit(a, lc, true)
        alpha += 0.04
    }
    val lc = abs(apcaLc(theme.text, composite(region.avg, theme.tint, MAX_REGION_SCRIM)))
    return RegionFit(MAX_REGION_SCRIM, lc, false)
}

fun chooseTheme(regions: List<RegionStats>, prior: AdvicePrior, weights: BeautyWeights = BeautyWeights()): ThemeChoice {
    if (regions.isEmpty()) return ThemeChoice(NEUTRAL_FALLBACK, Double.POSITIVE_INFINITY, emptyList())
    val globalDominant = regions.first().dominant
    val palette = harmonizePalette(globalDominant, prior)
    val avgLuma = regions.map { it.luma }.average()
    val mood = moodOf(prior, avgLuma)
    val textOrder = if (prior.suggestedPolarity == "dark") listOf(CHARCOAL, IVORY) else listOf(IVORY, CHARCOAL)

    val scored = mutableListOf<Pair<Theme, Double>>()
    for (tintC in palette) for (text in textOrder) for (scrim in SCRIM_STEPS) for (blur in BLUR_STEPS) {
        val theme = Theme(tintC.color, text, blur, scrim)
        if (regions.all { fitRegion(theme, it).met }) scored.add(theme to beautyCost(theme, palette, weights, mood))
    }
    if (scored.isEmpty()) {
        return ThemeChoice(NEUTRAL_FALLBACK, Double.POSITIVE_INFINITY, emptyList())
    }
    scored.sortBy { it.second }
    return ThemeChoice(scored[0].first, scored[0].second, scored.drop(1).take(3))
}
