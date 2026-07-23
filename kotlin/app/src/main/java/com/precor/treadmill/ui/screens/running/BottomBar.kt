package com.precor.treadmill.ui.screens.running

import androidx.compose.animation.core.EaseInOut
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.max
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LegibleGlassPanel
import com.precor.treadmill.ui.theme.OpacityGroup
import com.precor.treadmill.ui.theme.LocalOpacityGroup
import androidx.compose.runtime.remember
import androidx.compose.runtime.CompositionLocalProvider
import com.precor.treadmill.ui.theme.touchFingerPad
import com.precor.treadmill.ui.theme.touchThumbPad
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import com.precor.treadmill.ui.viewmodel.VoiceState

@Composable
fun BottomBar(
    viewModel: TreadmillViewModel,
    showControls: Boolean = true,
    externalPadding: Boolean = false,
    uniformHeight: Boolean = false,
    // Optional voice affordance: when non-null, a square mic glass button sits at the RIGHT of
    // the action row (both running and paused states). The lambda must be the permission-safe
    // handleVoiceToggle path lifted from AppNavigation — never a raw VoiceViewModel.toggle().
    onVoiceToggle: (() -> Unit)? = null,
    voiceState: VoiceState = VoiceState.IDLE,
    modifier: Modifier = Modifier,
) {
    val status by viewModel.status.collectAsState()
    val pgm by viewModel.derivedProgram.collectAsState()
    val context = LocalContext.current

    val isRunning = status.emulate && (status.emuSpeed > 0 || (pgm.running && !pgm.paused))
    val defaultHeight = touchFingerPad()
    // Stop is normally the largest emergency target (thumb pad). In the landscape HUD the action
    // bar has a fixed footprint, so keep one height across paused/running to avoid the row resizing.
    val stopHeight = if (uniformHeight) defaultHeight else touchThumbPad()

    // Use safeDrawing insets for bottom — covers nav bar, display cutouts, and curved screens
    val bottomSafe = WindowInsets.safeDrawing.asPaddingValues().calculateBottomPadding()
    val bottomPad = if (externalPadding) 0.dp else max(bottomSafe, 8.dp)
    val topPad = if (externalPadding) 0.dp else 6.dp

    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(top = topPad)
            .padding(bottom = bottomPad),
    ) {
        // Speed/Incline controls (hidden in landscape where they're shown separately)
        if (showControls) {
            SpeedInclineControls(
                viewModel = viewModel,
                modifier = Modifier.padding(bottom = 12.dp),
            )
        }

        // Action buttons — grouped so they share one uniform opacity (consistent glass across the row).
        // externalPadding (HUD) zeroes the internal horizontal inset so the row's left/right edges
        // line up with the panels above it (which the caller positions with its own padding).
        CompositionLocalProvider(LocalOpacityGroup provides remember { OpacityGroup() }) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = if (externalPadding) 0.dp else 12.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (pgm.paused) {
                // Resume (green) + Reset (red) — each button's brand color IS its scrim: the
                // color's opacity is solved like any scrim so the white label clears APCA over
                // the photo behind it, keeping the button solidly green/red as needed.
                ActionButton(
                    text = "Resume",
                    brand = Color(0xFF6BC89B),
                    onClick = { viewModel.pauseProgram(); haptic(context, 25) },
                    fontSize = 17.sp,
                    modifier = Modifier.weight(2f).height(defaultHeight),
                )
                ActionButton(
                    text = "Reset",
                    brand = Color(0xFFC45C52),
                    onClick = { viewModel.resetAll(); haptic(context, longArrayOf(50, 30, 50)) },
                    fontSize = 15.sp,
                    modifier = Modifier.weight(1f).height(defaultHeight),
                )
            } else {
                // Stop button
                ActionButton(
                    text = "Stop",
                    brand = Color(0xFFC45C52),
                    onClick = { if (isRunning) { viewModel.pauseProgram(); haptic(context, longArrayOf(50, 30, 50)) } },
                    enabled = isRunning,
                    fontSize = 17.sp,
                    modifier = Modifier.weight(1f).height(stopHeight),
                )
            }
            // Square mic at the row's right — fixed width equal to its height, so Stop (or
            // Resume/Reset) keeps its dominant weighted width. Same glass + opacity group as
            // the other buttons; active/listening state pulses like the NavRail mic.
            if (onVoiceToggle != null) {
                val micSize = if (pgm.paused) defaultHeight else stopHeight
                VoiceButton(
                    voiceState = voiceState,
                    onClick = { haptic(context, 20); onVoiceToggle() },
                    modifier = Modifier.size(micSize),
                )
            }
        }
        }
    }
}

/**
 * Square mic glass button: same [LegibleGlassPanel] adaptive-scrim language as [ActionButton]
 * (neutral glass surface, icon color solved as the panel accent). While the voice session is
 * active the mic tints to the session color and pulses — mirroring NavRail's VoiceTabItem.
 */
@Composable
private fun VoiceButton(
    voiceState: VoiceState,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val active = voiceState != VoiceState.IDLE
    val shouldPulse = voiceState == VoiceState.CONNECTING || voiceState == VoiceState.LISTENING
    // Same session palette as NavRail's mic: gold=connecting, green=listening, violet=speaking.
    val tint = when (voiceState) {
        VoiceState.CONNECTING -> Color(0xFFB8A87A)
        VoiceState.LISTENING -> Color(0xFF6BC89B)
        VoiceState.SPEAKING -> Color(0xFF8B7FA0)
        VoiceState.IDLE -> Color.White
    }

    val infiniteTransition = rememberInfiniteTransition(label = "micGlow")
    val pulseAlpha by infiniteTransition.animateFloat(
        initialValue = 0.6f,
        targetValue = if (voiceState == VoiceState.LISTENING) 0.15f else 0.4f,
        animationSpec = infiniteRepeatable(
            animation = tween(if (voiceState == VoiceState.LISTENING) 1000 else 1600, easing = EaseInOut),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "micPulseAlpha",
    )

    LegibleGlassPanel(
        accents = listOf(tint),
        modifier = modifier,
        shape = RoundedCornerShape(14.dp),
        targetLc = 60.0,
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .clickable(
                    interactionSource = remember { MutableInteractionSource() },
                    indication = null,
                    onClick = onClick,
                )
                .semantics { contentDescription = "Voice control" },
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Default.Mic,
                contentDescription = null,
                tint = tint,
                modifier = Modifier
                    .size(24.dp)
                    .drawBehind {
                        if (active) {
                            val alpha = if (shouldPulse) pulseAlpha else 0.5f
                            drawCircle(
                                color = tint.copy(alpha = alpha),
                                radius = size.minDimension * 0.9f,
                            )
                        }
                    },
            )
        }
    }
}

/**
 * An action button whose brand color is its scrim: [LegibleGlassPanel] raises the color's
 * opacity (the same APCA scrim math) until the white label clears over the photo behind it,
 * so the button reads as a solid green/red surface — as solid as needed, no more.
 */
@Composable
private fun ActionButton(
    text: String,
    brand: Color,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
    fontSize: TextUnit = 17.sp,
) {
    LegibleGlassPanel(
        accents = listOf(Color.White),
        scrimColor = brand,
        modifier = modifier.alpha(if (enabled) 1f else 0.4f),
        shape = RoundedCornerShape(14.dp),
        targetLc = 60.0,
        // Opacity is solved, not hand-set: the MAX of (white label clears APCA) and (the brand
        // surface stands out from the photo by GLASS_SURFACE_LC). Glassy where it can be, firm
        // where it must be — same model as every other glass surface.
    ) {
        Button(
            onClick = onClick,
            enabled = enabled,
            colors = ButtonDefaults.buttonColors(
                containerColor = Color.Transparent,
                contentColor = Color.White,
                disabledContainerColor = Color.Transparent,
                disabledContentColor = Color.White.copy(alpha = 0.6f),
            ),
            shape = RoundedCornerShape(14.dp),
            modifier = Modifier.fillMaxSize(),
        ) {
            Text(text, fontSize = fontSize, fontWeight = FontWeight.SemiBold) // legible-exempt: inside LegibleGlassPanel (white label solved)
        }
    }
}
