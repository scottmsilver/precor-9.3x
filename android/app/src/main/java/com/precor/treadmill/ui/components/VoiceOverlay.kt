package com.precor.treadmill.ui.components

import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LocalPrecorColors
import com.precor.treadmill.ui.theme.PillShape

@Composable
fun VoiceOverlay(
    voiceState: String, // "idle", "listening", "speaking"
    modifier: Modifier = Modifier,
) {
    val colors = LocalPrecorColors.current
    val active = voiceState == "listening" || voiceState == "speaking"
    val isListening = voiceState == "listening"

    // Pulse animation for listening dot
    val infiniteTransition = rememberInfiniteTransition(label = "voicePulse")
    val pulseAlpha by infiniteTransition.animateFloat(
        initialValue = 1f,
        targetValue = 0.3f,
        animationSpec = infiniteRepeatable(
            animation = tween(1200, easing = EaseInOut),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulseAlpha",
    )

    AnimatedVisibility(
        visible = active,
        enter = slideInVertically(initialOffsetY = { -it }) + fadeIn(),
        exit = slideOutVertically(targetOffsetY = { -it }) + fadeOut(),
        modifier = modifier
            .fillMaxWidth()
            .statusBarsPadding(),
    ) {
        Box(
            modifier = Modifier.fillMaxWidth(),
            contentAlignment = Alignment.TopCenter,
        ) {
            Row(
                modifier = Modifier
                    .padding(top = 8.dp)
                    .background(
                        color = if (isListening) colors.red.copy(alpha = 0.15f)
                        else colors.purple.copy(alpha = 0.15f),
                        shape = PillShape,
                    )
                    .padding(horizontal = 16.dp, vertical = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                if (isListening) {
                    Box(
                        modifier = Modifier
                            .size(8.dp)
                            .alpha(pulseAlpha)
                            .clip(CircleShape)
                            .background(colors.red),
                    )
                }
                Text(
                    text = if (isListening) "Listening..." else "Speaking...",
                    color = if (isListening) colors.red else colors.purple,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Medium,
                )
            }
        }
    }
}
