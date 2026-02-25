package com.precor.treadmill.ui.screens.running

import android.view.MotionEvent
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.input.pointer.pointerInteropFilter
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.platform.LocalConfiguration
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.delay

@OptIn(ExperimentalComposeUiApi::class)
@Composable
fun SpeedInclineControls(
    viewModel: TreadmillViewModel,
    vertical: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val status by viewModel.status.collectAsState()
    val context = LocalContext.current

    val speedAdjust = { delta: Int ->
        viewModel.adjustSpeed(delta)
        haptic(context, 15)
    }
    val inclineAdjust = { delta: Double ->
        viewModel.adjustIncline(delta)
        haptic(context, 15)
    }

    if (vertical) {
        Column(
            modifier = modifier
                .alpha(if (status.treadmillConnected) 1f else 0.3f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ControlPanel(
                value = (status.emuSpeed / 10.0).let { "%.1f".format(it) },
                label = "mph",
                accentColor = Color(0xFF6BC89B),
                smallDelta = 1.0, largeDelta = 10.0,
                enabled = status.treadmillConnected,
                onAdjust = { speedAdjust(it.toInt()) },
                modifier = Modifier.fillMaxWidth(),
            )
            ControlPanel(
                value = formatIncline(status.emuIncline),
                label = "% incline",
                accentColor = Color(0xFFA69882),
                smallDelta = 0.5, largeDelta = 1.0,
                enabled = status.treadmillConnected,
                onAdjust = inclineAdjust,
                modifier = Modifier.fillMaxWidth(),
            )
        }
    } else {
        Row(
            modifier = modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp)
                .alpha(if (status.treadmillConnected) 1f else 0.3f),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            ControlPanel(
                value = (status.emuSpeed / 10.0).let { "%.1f".format(it) },
                label = "mph",
                accentColor = Color(0xFF6BC89B),
                smallDelta = 1.0, largeDelta = 10.0,
                enabled = status.treadmillConnected,
                onAdjust = { speedAdjust(it.toInt()) },
                modifier = Modifier.weight(1f),
            )
            ControlPanel(
                value = formatIncline(status.emuIncline),
                label = "% incline",
                accentColor = Color(0xFFA69882),
                smallDelta = 0.5, largeDelta = 1.0,
                enabled = status.treadmillConnected,
                onAdjust = inclineAdjust,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

private fun formatIncline(value: Double): String {
    return "%.1f".format(value)
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun ControlPanel(
    value: String,
    label: String,
    accentColor: Color,
    smallDelta: Double,
    largeDelta: Double,
    enabled: Boolean,
    onAdjust: (Double) -> Unit,
    modifier: Modifier = Modifier,
) {
    // Build readable metric name from label (e.g., "mph" -> "speed", "% incline" -> "incline")
    val metricName = if (label.contains("incline", ignoreCase = true)) "incline" else "speed"
    val smallAmount = if (metricName == "speed") "%.1f mph".format(smallDelta / 10.0) else "%.1f%%".format(smallDelta)
    val largeAmount = if (metricName == "speed") "%.1f mph".format(largeDelta / 10.0) else "%.1f%%".format(largeDelta)

    Row(
        modifier = modifier
            .background(
                color = Color(0xFF1E1D1B),
                shape = RoundedCornerShape(16.dp),
            )
            .border(
                width = 1.dp,
                color = Color.White.copy(alpha = 0.25f),
                shape = RoundedCornerShape(16.dp),
            )
            .padding(5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(1.dp),
    ) {
        // Small buttons column
        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            RepeatButton(
                delta = smallDelta,
                enabled = enabled,
                onAdjust = onAdjust,
                isUp = true,
                color = accentColor,
                description = "Increase $metricName by $smallAmount",
            )
            RepeatButton(
                delta = -smallDelta,
                enabled = enabled,
                onAdjust = onAdjust,
                isUp = false,
                color = accentColor,
                description = "Decrease $metricName by $smallAmount",
            )
        }

        // Value display — use min dimension to detect true tablet vs phone-in-landscape
        val config = LocalConfiguration.current
        val isTablet = minOf(config.screenWidthDp, config.screenHeightDp) >= 600
        val valueFontSize = if (isTablet) 32.sp else 26.sp
        Column(
            modifier = Modifier
                .weight(1f)
                .padding(vertical = 10.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(
                text = value,
                color = accentColor,
                fontSize = valueFontSize,
                fontWeight = FontWeight.SemiBold,
                textAlign = TextAlign.Center,
                lineHeight = (valueFontSize.value + 2).sp,
            )
            Text(
                text = label,
                color = Color(0x59E8E4DF),
                fontSize = if (isTablet) 12.sp else 10.sp,
            )
        }

        // Large buttons column
        Column(verticalArrangement = Arrangement.spacedBy(3.dp)) {
            RepeatButton(
                delta = largeDelta,
                enabled = enabled,
                onAdjust = onAdjust,
                isUp = true,
                color = accentColor,
                isDouble = true,
                description = "Increase $metricName by $largeAmount",
            )
            RepeatButton(
                delta = -largeDelta,
                enabled = enabled,
                onAdjust = onAdjust,
                isUp = false,
                color = accentColor,
                isDouble = true,
                description = "Decrease $metricName by $largeAmount",
            )
        }
    }
}

/**
 * Button with hold-to-repeat: 400ms initial delay, 150ms repeat, 75ms after 5 repeats.
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun RepeatButton(
    delta: Double,
    enabled: Boolean,
    onAdjust: (Double) -> Unit,
    isUp: Boolean,
    color: Color,
    isDouble: Boolean = false,
    description: String = "",
    modifier: Modifier = Modifier,
) {
    var pressed by remember { mutableStateOf(false) }

    // Hold-to-repeat coroutine
    LaunchedEffect(pressed) {
        if (!pressed || !enabled) return@LaunchedEffect
        onAdjust(delta)
        delay(400) // initial delay
        var count = 0
        while (pressed) {
            onAdjust(delta)
            count++
            delay(if (count > 5) 75 else 150)
        }
    }

    val config = LocalConfiguration.current
    val isTablet = minOf(config.screenWidthDp, config.screenHeightDp) >= 600
    val btnW = if (isTablet) 52.dp else 44.dp
    val btnH = if (isTablet) 72.dp else 62.dp

    Box(
        modifier = modifier
            .size(width = btnW, height = btnH)
            .semantics { contentDescription = description }
            .background(
                color = Color(0x3D787880), // fill
                shape = RoundedCornerShape(10.dp),
            )
            .pointerInteropFilter { event ->
                if (!enabled) return@pointerInteropFilter false
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> {
                        pressed = true
                        true
                    }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                        pressed = false
                        true
                    }
                    else -> false
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        // Chevron icon — Canvas-drawn to match web SVG style
        val iconSize = if (isTablet) 28.dp else 24.dp
        Canvas(
            modifier = Modifier.size(iconSize),
        ) {
            val w = size.width
            val h = size.height
            val sw = w * 0.11f
            val stroke = Stroke(
                width = sw,
                cap = StrokeCap.Round,
                join = StrokeJoin.Round,
            )
            val inset = sw / 2 + w * 0.05f

            if (isDouble) {
                // Two chevrons stacked, tight vertical gap
                val chevAmp = h * 0.18f  // how tall each V is
                val gap = h * 0.06f      // space between the two Vs
                val totalH = chevAmp * 2 + gap
                val topY = (h - totalH) / 2  // center vertically

                if (isUp) {
                    val path1 = Path().apply {
                        moveTo(inset, topY + chevAmp)
                        lineTo(w / 2, topY)
                        lineTo(w - inset, topY + chevAmp)
                    }
                    drawPath(path1, color, style = stroke)
                    val path2 = Path().apply {
                        moveTo(inset, topY + chevAmp + gap + chevAmp)
                        lineTo(w / 2, topY + chevAmp + gap)
                        lineTo(w - inset, topY + chevAmp + gap + chevAmp)
                    }
                    drawPath(path2, color, style = stroke)
                } else {
                    val path1 = Path().apply {
                        moveTo(inset, topY)
                        lineTo(w / 2, topY + chevAmp)
                        lineTo(w - inset, topY)
                    }
                    drawPath(path1, color, style = stroke)
                    val path2 = Path().apply {
                        moveTo(inset, topY + chevAmp + gap)
                        lineTo(w / 2, topY + chevAmp + gap + chevAmp)
                        lineTo(w - inset, topY + chevAmp + gap)
                    }
                    drawPath(path2, color, style = stroke)
                }
            } else {
                // Single chevron — same amplitude as each line in the double
                val chevAmp = h * 0.22f
                val topY = (h - chevAmp) / 2
                val path = Path().apply {
                    if (isUp) {
                        moveTo(inset, topY + chevAmp)
                        lineTo(w / 2, topY)
                        lineTo(w - inset, topY + chevAmp)
                    } else {
                        moveTo(inset, topY)
                        lineTo(w / 2, topY + chevAmp)
                        lineTo(w - inset, topY)
                    }
                }
                drawPath(path, color, style = stroke)
            }
        }
    }
}
