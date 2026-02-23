package com.precor.treadmill.ui.screens.running

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
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
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.Center,
            verticalAlignment = Alignment.Bottom,
        ) {
            // Heart rate (only when HRM connected)
            if (status.hrmConnected) {
                HeartRateMetric(bpm = status.heartRate)
                Spacer(Modifier.width(20.dp))
            }
            MetricItem(
                value = sess.pace,
                label = "min/mi",
                color = Color(0xFF6B8F8B), // teal
            )
            Spacer(Modifier.width(20.dp))
            MetricItem(
                value = sess.distDisplay,
                label = "miles",
                color = Color(0xFFE8E4DF), // text
            )
            Spacer(Modifier.width(20.dp))
            MetricItem(
                value = sess.vertDisplay,
                label = "vert ft",
                color = Color(0xFFA69882), // orange
            )
        }
    }
}

@Composable
private fun HeartRateMetric(bpm: Int) {
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
        Text(
            text = "\u2665",
            color = color,
            fontSize = 14.sp,
            modifier = Modifier.scale(pulseScale),
        )
        Text(
            text = if (bpm > 0) bpm.toString() else "---",
            color = color,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = "bpm",
            color = Color(0x59E8E4DF), // text3
            fontSize = 10.sp,
        )
    }
}

@Composable
private fun MetricItem(
    value: String,
    label: String,
    color: Color,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Text(
            text = value,
            color = color,
            fontSize = 15.sp,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = label,
            color = Color(0x59E8E4DF), // text3
            fontSize = 10.sp,
        )
    }
}
