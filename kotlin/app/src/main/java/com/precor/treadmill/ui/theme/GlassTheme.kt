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
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.precor.treadmill.ui.theme.readability.Theme as ReadTheme

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
