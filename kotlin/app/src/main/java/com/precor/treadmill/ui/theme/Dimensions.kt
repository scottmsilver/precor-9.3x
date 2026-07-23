package com.precor.treadmill.ui.theme

import androidx.compose.runtime.Composable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

object Touch {
    const val FINGERTIP_MM = 12f
    const val FINGER_PAD_MM = 16f
    const val THUMB_PAD_MM = 20f

    /**
     * xdpi/ydpi is the panel's self-reported physical dpi and is not always sane:
     * emulators keep reporting the hardware dpi after a `wm density` override, and
     * some panels report nonsense. Trust it only within 0.5×–2× of the density
     * bucket; otherwise fall back to the bucket (which collapses mm→dp to the
     * standard 160dp/inch rule instead of inflating a 20mm target to ~500dp).
     */
    fun sanitizedDpi(reported: Float, densityDpi: Float): Float {
        // A broken baseline (zero/negative/non-finite densityDpi) would turn the
        // fallback itself into garbage — bottom out at the mdpi reference instead.
        val base = if (densityDpi.isFinite() && densityDpi > 0f) densityDpi else 160f
        return if (reported in base / 2f..base * 2f) reported else base
    }

    @Composable
    fun mmToHorizontalDp(mm: Float): Dp {
        val metrics = LocalContext.current.resources.displayMetrics
        val dpi = sanitizedDpi(metrics.xdpi, metrics.densityDpi.toFloat())
        return ((mm / 25.4f) * dpi / metrics.density).dp
    }

    @Composable
    fun mmToVerticalDp(mm: Float): Dp {
        val metrics = LocalContext.current.resources.displayMetrics
        val dpi = sanitizedDpi(metrics.ydpi, metrics.densityDpi.toFloat())
        return ((mm / 25.4f) * dpi / metrics.density).dp
    }
}

/** Chevron button width (12mm, horizontal axis) */
@Composable
fun touchFingertip(): Dp = Touch.mmToHorizontalDp(Touch.FINGERTIP_MM)

/** Button height for repeated presses (16mm, vertical axis) */
@Composable
fun touchFingerPad(): Dp = Touch.mmToVerticalDp(Touch.FINGER_PAD_MM)

/** Stop button height — largest target (20mm, vertical axis) */
@Composable
fun touchThumbPad(): Dp = Touch.mmToVerticalDp(Touch.THUMB_PAD_MM)
