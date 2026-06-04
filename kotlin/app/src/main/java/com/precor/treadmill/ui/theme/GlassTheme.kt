package com.precor.treadmill.ui.theme

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.Build
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.graphics.Color
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.Text
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.precor.treadmill.ui.theme.readability.Rgb
import com.precor.treadmill.ui.theme.readability.Theme as ReadTheme
import com.precor.treadmill.ui.theme.readability.ensureLegible
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import com.precor.treadmill.ui.theme.readability.NormRect
import com.precor.treadmill.ui.theme.readability.Role
import com.precor.treadmill.ui.theme.readability.composite
import com.precor.treadmill.ui.theme.readability.cropMapRect
import com.precor.treadmill.ui.theme.readability.sampleRegionPixels
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import com.precor.treadmill.ui.theme.readability.legibleTreatment

/**
 * Glass panel parameters derived from background image brightness.
 * Tint opacity is the primary readability lever (not blur).
 * Based on WCAG contrast research and Apple's materials system approach.
 */
data class GlassParams(
    val blur: Dp = 2.dp,
    val panelOpacity: Float = 0.34f,
    val borderOpacity: Float = 0.30f,
    val overlayOpacity: Float = 0.12f,
    // Photo-derived scrim color (engine `Theme.tint`). Defaults to black so legacy
    // callers behave exactly as before; the running screen overrides it.
    val tint: Color = Color.Black,
    // Engine-chosen text color (ivory/charcoal). Defaults to the app's ivory so legacy
    // callers are unchanged; the running screen overrides it for readability-driven text.
    val textColor: Color = Color(0xFFE8E4DF),
    // Effective background color behind panel text (scrim composited over the photo).
    // This is what accent colors must be checked against by ensureLegible / LegibleText.
    val panelBg: Color = Color(0xFF101010),
) {
    companion object {
        val Default = GlassParams()

        fun fromBrightness(brightness: Float): GlassParams {
            val b = brightness.coerceIn(0f, 255f)
            // Tint opacity is the primary readability lever.
            // Min 38% ensures glass is always clearly visible against any photo.
            return GlassParams(
                blur = (b * 0.012f).coerceIn(0f, 3f).dp,
                panelOpacity = (38f + b * 0.06f).coerceIn(38f, 52f) / 100f,
                borderOpacity = (45f - b * 0.08f).coerceIn(22f, 38f) / 100f,
                overlayOpacity = (10f + (b - 60f) * 0.10f).coerceIn(10f, 28f) / 100f,
            )
        }

        fun sampleBrightness(bitmap: Bitmap): Float {
            val scaled = Bitmap.createScaledBitmap(bitmap, 200, 125, true)
            val pixels = IntArray(scaled.width * scaled.height)
            scaled.getPixels(pixels, 0, scaled.width, 0, 0, scaled.width, scaled.height)
            if (scaled !== bitmap) scaled.recycle()

            var totalLum = 0.0
            for (pixel in pixels) {
                val r = (pixel shr 16) and 0xFF
                val g = (pixel shr 8) and 0xFF
                val b2 = pixel and 0xFF
                totalLum += 0.299 * r + 0.587 * g + 0.114 * b2
            }
            return (totalLum / pixels.size).toFloat()
        }
    }
}

val LocalGlassParams = compositionLocalOf { GlassParams.Default }

@Composable
fun rememberGlassParams(drawableRes: Int): GlassParams {
    val context = LocalContext.current
    return remember(drawableRes) {
        try {
            val opts = BitmapFactory.Options().apply { inSampleSize = 8 }
            val bitmap = BitmapFactory.decodeResource(context.resources, drawableRes, opts)
                ?: return@remember GlassParams.Default
            val brightness = GlassParams.sampleBrightness(bitmap)
            bitmap.recycle()
            GlassParams.fromBrightness(brightness)
        } catch (_: Exception) {
            GlassParams.Default
        }
    }
}

fun Modifier.glassPanel(
    params: GlassParams,
    shape: RoundedCornerShape = RoundedCornerShape(12.dp),
): Modifier {
    var m = this
        .background(params.tint.copy(alpha = params.panelOpacity), shape)
        .border(1.dp, Color.White.copy(alpha = params.borderOpacity), shape)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        m = m.blur(params.blur)
    }
    return m
}

fun ReadTheme.composeTextColor() =
    Color(text.r.toInt().coerceIn(0,255), text.g.toInt().coerceIn(0,255), text.b.toInt().coerceIn(0,255))

// --- Overlay-text legibility guard ------------------------------------------
// Every piece of text drawn on top of the background photo should pass through
// ensureLegible against the actual background behind it. LocalOverlayBackground
// carries that background down the tree; LegibleText is the sanctioned way to
// render overlay text so the APCA check cannot be forgotten.

/** Effective background color behind overlay content at this point in the tree. */
val LocalOverlayBackground = compositionLocalOf { Color(0xFF101010) }

private fun Color.toReadRgb() = Rgb(red.toDouble() * 255, green.toDouble() * 255, blue.toDouble() * 255)
private fun Rgb.toComposeColor() = Color(r.toInt().coerceIn(0, 255), g.toInt().coerceIn(0, 255), b.toInt().coerceIn(0, 255))

/** Return this color adjusted (hue/chroma preserved) to clear [targetLc] APCA against [bg]. */
fun Color.legibleOn(bg: Color, targetLc: Double = 60.0): Color =
    ensureLegible(toReadRgb(), bg.toReadRgb(), targetLc).toComposeColor()

/**
 * Samples the *actual* background pixels behind a widget at its measured screen bounds
 * (after ContentScale.Crop), composited with the panel scrim. This is what overlay
 * colors are checked against, so legibility is measured against what's really rendered
 * behind each element — not one screen-wide estimate.
 */
class PhotoSampler(
    private val pixels: IntArray,
    private val imgW: Int,
    private val imgH: Int,
    private val containerW: Float,
    private val containerH: Float,
) {
    /** The RAW (un-scrimmed) average photo color behind [coords], or null if not measurable yet. */
    fun bgAt(coords: LayoutCoordinates): Color? {
        if (!coords.isAttached || containerW <= 0f || containerH <= 0f) return null
        val r = coords.boundsInWindow()
        if (r.width <= 0f || r.height <= 0f) return null
        val rect = NormRect(
            (r.left / containerW).toDouble(),
            (r.top / containerH).toDouble(),
            (r.width / containerW).toDouble(),
            (r.height / containerH).toDouble(),
        )
        val mapped = cropMapRect(imgW, imgH, containerW, containerH, rect)
        return sampleRegionPixels(pixels, imgW, imgH, mapped, "x", Role.BODY).avg.toComposeColor()
    }
}

/**
 * A glass panel that darkens itself UNIFORMLY (one scrim value, consistent across its whole
 * area) just enough to make its [accents] clear [targetLc] APCA over the actual photo behind
 * it — measured at the panel's real bounds via [LocalPhotoSampler]. Provides the resulting
 * effective background + opacity to its [content] (via [LocalGlassParams]/[LocalOverlayBackground])
 * so text inside is checked against the right background. This is the per-panel analog of the
 * engine's region scrim: every panel on the photo gets its own consistent darkening.
 */
@Composable
fun LegibleGlassPanel(
    accents: List<Color>,
    modifier: Modifier = Modifier,
    shape: RoundedCornerShape = RoundedCornerShape(12.dp),
    targetLc: Double = 70.0,
    content: @Composable () -> Unit,
) {
    val sampler = LocalPhotoSampler.current
    val tint = LocalOverlayScrimTint.current
    val base = LocalGlassParams.current
    var rawBg by remember { mutableStateOf<Color?>(null) }
    val tintRgb = tint.toReadRgb()
    val alpha: Float = run {
        val b = rawBg
        if (b == null) base.panelOpacity
        else {
            val need = accents.maxOfOrNull { acc ->
                legibleTreatment(acc.toReadRgb(), b.toReadRgb(), tintRgb, targetLc).scrimAlpha
            } ?: 0.0
            maxOf(base.panelOpacity.toDouble(), need).toFloat().coerceIn(0.30f, 0.80f)
        }
    }
    val effectiveBg = composite((rawBg ?: base.panelBg).toReadRgb(), tintRgb, alpha.toDouble()).toComposeColor()
    var m = modifier
        .onGloballyPositioned { c -> sampler?.bgAt(c)?.let { if (it != rawBg) rawBg = it } }
        .background(tint.copy(alpha = alpha), shape)
        .border(1.dp, Color.White.copy(alpha = base.borderOpacity), shape)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && base.blur.value > 0f) m = m.blur(base.blur)
    androidx.compose.foundation.layout.Box(modifier = m) {
        CompositionLocalProvider(
            LocalOverlayBackground provides effectiveBg,
            LocalGlassParams provides base.copy(panelOpacity = alpha, panelBg = effectiveBg),
        ) { content() }
    }
}

/** Per-screen photo sampler so overlay widgets can measure their own background. Null off the photo. */
val LocalPhotoSampler = compositionLocalOf<PhotoSampler?> { null }

/** The scrim color the local-darkening lever uses (the engine's photo-cohesive dark tint). */
val LocalOverlayScrimTint = compositionLocalOf { Color(0xFF101010) }

/**
 * Text drawn on top of the background photo, guaranteed legible by a two-lever guard run
 * against the actual pixels behind it (measured via [LocalPhotoSampler], falling back to
 * [LocalOverlayBackground] until laid out):
 *   1. darken the local background — a soft scrim of [LocalOverlayScrimTint] drawn behind
 *      the text, only as strong as needed (zero when the color already passes), so accent
 *      colors keep their true hue;
 *   2. nudge the text color — only if even max scrim is not enough.
 * Use this instead of a raw `Text` for any overlay text so the check can't be skipped.
 */
@Composable
fun LegibleText(
    text: String,
    color: Color,
    modifier: Modifier = Modifier,
    targetLc: Double = 60.0,
    style: TextStyle = LocalTextStyle.current,
) {
    // Background darkening is applied UNIFORMLY by the enclosing LegibleGlassPanel (consistent
    // in x/y), which provides the effective background here. We only fall back to a per-element
    // color nudge if that uniform panel still leaves this color short — no per-element scrim,
    // which the eye would read as patchy.
    val bg = LocalOverlayBackground.current
    Text(text = text, modifier = modifier, style = style.copy(color = color.legibleOn(bg, targetLc)))
}

/** The engine's photo-derived scrim tint as an opaque Compose color (alpha applied by the panel). */
fun ReadTheme.composeTintColor() =
    Color(tint.r.toInt().coerceIn(0,255), tint.g.toInt().coerceIn(0,255), tint.b.toInt().coerceIn(0,255))

fun Modifier.glassPanelTinted(
    params: GlassParams,
    tint: Color,
    tintAlpha: Float = 1.0f,
    shape: RoundedCornerShape = RoundedCornerShape(14.dp),
): Modifier {
    var m = this
        .background(tint.copy(alpha = params.panelOpacity * tintAlpha), shape)
        .border(1.dp, tint.copy(alpha = (params.borderOpacity + 0.15f).coerceAtMost(1f)), shape)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        m = m.blur(params.blur)
    }
    return m
}
