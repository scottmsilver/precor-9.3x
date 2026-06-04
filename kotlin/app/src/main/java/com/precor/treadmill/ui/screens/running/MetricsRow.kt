package com.precor.treadmill.ui.screens.running

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LegibleText
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.TimerFontFamily
import com.precor.treadmill.ui.theme.glassPanel
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlin.math.max

private fun hrColor(bpm: Int): Color = when {
    bpm >= 170 -> Color(0xFFC45C52)  // red
    bpm >= 150 -> Color(0xFFD4845A)  // orange
    bpm >= 120 -> Color(0xFFD4B85A)  // yellow
    else -> Color(0xFF6BC89B)        // green
}

@Composable
fun MetricsRow(
    viewModel: TreadmillViewModel,
    scale: Float = 1f,
    modifier: Modifier = Modifier,
) {
    val sess by viewModel.derivedSession.collectAsState()
    val status by viewModel.status.collectAsState()

    AnimatedVisibility(
        visible = sess.active,
        enter = expandVertically() + fadeIn(),
        exit = shrinkVertically() + fadeOut(),
        modifier = modifier,
    ) {
        Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
        Row(
            modifier = Modifier
                .glassPanel(LocalGlassParams.current, RoundedCornerShape(10.dp))
                .padding(horizontal = 20.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.Bottom,
        ) {
            // Heart rate (only when HRM connected)
            if (status.hrmConnected) {
                HeartRateMetric(bpm = status.heartRate, scale = scale)
                Spacer(Modifier.width((20 * scale).dp))
            }
            MetricItem(
                value = sess.pace,
                label = "min/mi",
                color = Color(0xFF6B8F8B), // teal
                fontFamily = TimerFontFamily,
                scale = scale,
            )
            Spacer(Modifier.width((20 * scale).dp))
            MetricItem(
                value = sess.distDisplay,
                label = "miles",
                color = Color(0xFFE8E4DF), // text
                scale = scale,
            )
            Spacer(Modifier.width((20 * scale).dp))
            MetricItem(
                value = sess.vertDisplay,
                label = "vert ft",
                color = Color(0xFFA69882), // orange
                scale = scale,
            )
            Spacer(Modifier.width((20 * scale).dp))
            MetricItem(
                value = sess.caloriesDisplay,
                label = "cal",
                color = Color(0xFFE8E4DF), // text
                scale = scale,
            )
        }
        } // Box
    }
}

@Composable
private fun HeartRateMetric(bpm: Int, scale: Float = 1f) {
    val color = hrColor(bpm)
    val pulseDurationMs = if (bpm > 0) max(400, (60_000 / bpm)) else 1000

    val infiniteTransition = rememberInfiniteTransition(label = "hrPulse")
    val pulseScale by infiniteTransition.animateFloat(
        initialValue = 1f,
        targetValue = 1.18f,
        animationSpec = infiniteRepeatable(
            animation = keyframes {
                durationMillis = pulseDurationMs
                1f at 0
                1.18f at (pulseDurationMs * 0.15f).toInt()
                1f at (pulseDurationMs * 0.30f).toInt()
                1.12f at (pulseDurationMs * 0.45f).toInt()
                1f at (pulseDurationMs * 0.60f).toInt()
                1f at pulseDurationMs
            },
            repeatMode = RepeatMode.Restart,
        ),
        label = "pulseScale",
    )

    Row(
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        LegibleText(
            text = "\u2665",
            color = color,
            modifier = Modifier.scale(pulseScale),
            style = TextStyle(fontSize = (14 * scale).sp),
        )
        LegibleText(
            text = if (bpm > 0) bpm.toString() else "---",
            color = color,
            targetLc = 70.0,
            style = TextStyle(
                fontSize = (15 * scale).sp,
                fontWeight = FontWeight.SemiBold,
                shadow = Shadow(
                    color = Color.Black.copy(alpha = 0.4f),
                    offset = Offset(0f, 1f),
                    blurRadius = 4f,
                ),
            ),
        )
        LegibleText(
            text = "bpm",
            color = LocalGlassParams.current.textColor,
            style = TextStyle(
                fontSize = (10 * scale).sp,
                shadow = Shadow(
                    color = Color.Black.copy(alpha = 0.4f),
                    offset = Offset(0f, 1f),
                    blurRadius = 3f,
                ),
            ),
        )
    }
}

@Composable
private fun MetricItem(
    value: String,
    label: String,
    color: Color,
    modifier: Modifier = Modifier,
    fontFamily: FontFamily? = null,
    scale: Float = 1f,
) {
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        LegibleText(
            text = value,
            color = color,
            targetLc = 70.0,
            modifier = Modifier.widthIn(min = (40 * scale).dp).alignByBaseline(),
            style = TextStyle(
                fontSize = (15 * scale).sp,
                fontWeight = FontWeight.SemiBold,
                fontFeatureSettings = "tnum",
                fontFamily = fontFamily,
                textAlign = TextAlign.Right,
                shadow = Shadow(
                    color = Color.Black.copy(alpha = 0.4f),
                    offset = Offset(0f, 1f),
                    blurRadius = 4f,
                ),
            ),
        )
        LegibleText(
            text = label,
            color = LocalGlassParams.current.textColor,
            modifier = Modifier.alignByBaseline(),
            style = TextStyle(
                fontSize = (10 * scale).sp,
                shadow = Shadow(
                    color = Color.Black.copy(alpha = 0.4f),
                    offset = Offset(0f, 1f),
                    blurRadius = 3f,
                ),
            ),
        )
    }
}
