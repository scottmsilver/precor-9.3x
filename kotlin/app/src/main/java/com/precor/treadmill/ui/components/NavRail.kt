package com.precor.treadmill.ui.components

import android.content.res.Configuration
import androidx.compose.animation.core.*
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.automirrored.filled.DirectionsRun
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Settings
import androidx.compose.foundation.border
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.data.remote.models.Profile

@Composable
fun NavRail(
    currentRoute: String,
    voiceState: String, // "idle", "connecting", "listening", "speaking"
    onNavigate: (String) -> Unit,
    onVoiceToggle: () -> Unit,
    onSettingsToggle: () -> Unit,
    activeProfile: Profile? = null,
    guestMode: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val isRunSelected = currentRoute.startsWith("running")
    val isLandscape = LocalConfiguration.current.orientation == Configuration.ORIENTATION_LANDSCAPE

    if (isLandscape) {
        val layoutDir = LocalLayoutDirection.current
        val startSafe = WindowInsets.safeDrawing.asPaddingValues().calculateStartPadding(layoutDir)
        // Modifier order: bg covers safe area, padding offsets content, then fixed 56dp content width
        Column(
            modifier = modifier
                .fillMaxHeight()
                .background(Color(0xFF121210))
                .padding(start = startSafe)
                .width(56.dp)
                .padding(vertical = 8.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            ProfileTabItem(
                profile = activeProfile,
                guestMode = guestMode,
                selected = currentRoute == "profiles",
                onClick = { onNavigate("profiles") },
                showLabel = false,
            )
            TabItem(
                icon = Icons.Default.Home,
                label = "Home",
                selected = currentRoute == "lobby",
                onClick = { onNavigate("lobby") },
                showLabel = false,
            )
            TabItem(
                icon = Icons.AutoMirrored.Filled.DirectionsRun,
                label = "Run",
                selected = isRunSelected,
                onClick = { onNavigate("running") },
                showLabel = false,
            )
            VoiceTabItem(
                voiceState = voiceState,
                onClick = onVoiceToggle,
                showLabel = false,
            )
            TabItem(
                icon = Icons.Default.Settings,
                label = "Settings",
                selected = false,
                onClick = onSettingsToggle,
                showLabel = false,
            )
        }
    } else {
        val bottomSafe = WindowInsets.safeDrawing.asPaddingValues().calculateBottomPadding()
        Row(
            modifier = modifier
                .fillMaxWidth()
                .background(Color(0xFF121210))
                .padding(bottom = bottomSafe)
                .height(56.dp)
                .padding(horizontal = 24.dp),
            horizontalArrangement = Arrangement.SpaceAround,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            ProfileTabItem(
                profile = activeProfile,
                guestMode = guestMode,
                selected = currentRoute == "profiles",
                onClick = { onNavigate("profiles") },
            )
            TabItem(
                icon = Icons.Default.Home,
                label = "Home",
                selected = currentRoute == "lobby",
                onClick = { onNavigate("lobby") },
            )
            TabItem(
                icon = Icons.AutoMirrored.Filled.DirectionsRun,
                label = "Run",
                selected = isRunSelected,
                onClick = { onNavigate("running") },
            )
            VoiceTabItem(
                voiceState = voiceState,
                onClick = onVoiceToggle,
            )
            TabItem(
                icon = Icons.Default.Settings,
                label = "Settings",
                selected = false,
                onClick = onSettingsToggle,
            )
        }
    }
}

@Composable
private fun TabItem(
    icon: ImageVector,
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
    tint: Color? = null,
    showLabel: Boolean = true,
    modifier: Modifier = Modifier,
) {
    val color = tint ?: if (selected) Color(0xFFE8E4DF) else Color(0x59E8E4DF)

    Column(
        modifier = modifier
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                onClick = onClick,
            )
            .padding(vertical = if (showLabel) 4.dp else 10.dp, horizontal = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = label,
            tint = color,
            modifier = Modifier.size(24.dp),
        )
        if (showLabel) {
            Spacer(Modifier.height(2.dp))
            Text(
                text = label,
                color = color,
                fontSize = 10.sp,
            )
        }
    }
}

@Composable
private fun VoiceTabItem(
    voiceState: String,
    onClick: () -> Unit,
    showLabel: Boolean = true,
) {
    val active = voiceState == "connecting" || voiceState == "listening" || voiceState == "speaking"
    val shouldPulse = voiceState == "connecting" || voiceState == "listening"
    val glowColor = when (voiceState) {
        "connecting" -> Color(0xFFB8A87A)
        "listening" -> Color(0xFF6BC89B)
        "speaking" -> Color(0xFF8B7FA0)
        else -> Color.Transparent
    }

    val infiniteTransition = rememberInfiniteTransition(label = "tabMicGlow")
    val pulseAlpha by infiniteTransition.animateFloat(
        initialValue = 0.6f,
        targetValue = if (voiceState == "listening") 0.15f else 0.4f,
        animationSpec = infiniteRepeatable(
            animation = tween(if (voiceState == "listening") 1000 else 1600, easing = EaseInOut),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "tabPulseAlpha",
    )

    val color = when (voiceState) {
        "connecting" -> Color(0xFFB8A87A)
        "listening" -> Color(0xFF6BC89B)
        "speaking" -> Color(0xFF8B7FA0)
        else -> Color(0x59E8E4DF)
    }

    Column(
        modifier = Modifier
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                onClick = onClick,
            )
            .padding(vertical = if (showLabel) 4.dp else 10.dp, horizontal = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Icon(
            imageVector = Icons.Default.Mic,
            contentDescription = when (voiceState) {
                "connecting" -> "Connecting"
                "listening" -> "Listening"
                "speaking" -> "Speaking"
                else -> "Voice"
            },
            tint = color,
            modifier = Modifier
                .size(24.dp)
                .drawBehind {
                    if (active) {
                        val alpha = if (shouldPulse) pulseAlpha else 0.5f
                        drawCircle(
                            color = glowColor.copy(alpha = alpha),
                            radius = size.minDimension * 0.9f,
                        )
                    }
                },
        )
        if (showLabel) {
            Spacer(Modifier.height(2.dp))
            Text(
                text = when (voiceState) {
                    "connecting" -> "Connecting..."
                    "listening" -> "Listening"
                    "speaking" -> "Speaking"
                    else -> "Voice"
                },
                color = color,
                fontSize = 10.sp,
            )
        }
    }
}

@Composable
private fun ProfileTabItem(
    profile: Profile?,
    guestMode: Boolean,
    selected: Boolean,
    onClick: () -> Unit,
    showLabel: Boolean = true,
) {
    val label = profile?.name ?: if (guestMode) "Guest" else "Profile"
    val initials = profile?.initials ?: if (guestMode) "?" else "?"
    val bgColor = try {
        Color(android.graphics.Color.parseColor(profile?.color ?: "#d4c4a8"))
    } catch (_: Exception) {
        Color(0xFFD4C4A8)
    }

    Column(
        modifier = Modifier
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                onClick = onClick,
            )
            .padding(vertical = if (showLabel) 4.dp else 10.dp, horizontal = 12.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier
                .size(24.dp)
                .clip(CircleShape)
                .background(bgColor)
                .then(
                    if (selected) Modifier.border(2.dp, Color(0xFFE8E4DF), CircleShape)
                    else Modifier
                ),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                text = initials,
                fontSize = 11.sp,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF1E1D1B),
                textAlign = TextAlign.Center,
            )
        }
        if (showLabel) {
            Spacer(Modifier.height(2.dp))
            Text(
                text = label,
                color = if (selected) Color(0xFFE8E4DF) else Color(0x59E8E4DF),
                fontSize = 10.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
    }
}
