package com.precor.treadmill.ui.screens.running

import android.content.res.Configuration
import androidx.compose.animation.*
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.blur
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.max
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.components.HistoryList
import com.precor.treadmill.ui.theme.TimerFontFamily
import com.precor.treadmill.ui.util.fmtDur
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.delay

@Composable
fun RunningScreen(
    viewModel: TreadmillViewModel,
    voiceState: String,
    onNavigateHome: () -> Unit,
    onVoiceToggle: (String?) -> Unit,
    modifier: Modifier = Modifier,
) {
    val sess by viewModel.derivedSession.collectAsState()
    val pgm by viewModel.derivedProgram.collectAsState()
    val status by viewModel.status.collectAsState()
    val encouragement by viewModel.encouragement.collectAsState()
    val context = LocalContext.current

    // Auto-clear encouragement after 4 seconds
    LaunchedEffect(encouragement) {
        if (encouragement != null) {
            delay(4000)
            viewModel.clearEncouragement()
        }
    }

    val isManual = pgm.program?.manual == true
    val physicalActive = sess.active || pgm.running

    // Delayed visual active state for manual programs
    var visualActive by remember { mutableStateOf(false) }
    LaunchedEffect(physicalActive, isManual, status.emuSpeed, status.emuIncline) {
        if (physicalActive && isManual && !visualActive) {
            delay(1200)
            visualActive = true
        } else if (physicalActive && !isManual) {
            visualActive = true
        } else if (!physicalActive) {
            visualActive = false
        }
    }

    val isActive = visualActive
    var durationEditOpen by remember { mutableStateOf(false) }
    val configuration = LocalConfiguration.current
    val isLandscape = configuration.orientation == Configuration.ORIENTATION_LANDSCAPE
    val isWideScreen = configuration.screenWidthDp >= 600

    if (isLandscape) {
        // Landscape: side-by-side layout
        RunningScreenLandscape(
            viewModel = viewModel,
            voiceState = voiceState,
            onNavigateHome = onNavigateHome,
            onVoiceToggle = onVoiceToggle,
            isActive = isActive,
            isManual = isManual,
            durationEditOpen = durationEditOpen,
            onDurationEditToggle = { durationEditOpen = !durationEditOpen },
            modifier = modifier,
        )
        return
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .background(Color(0xFF121210))
            .statusBarsPadding(),
    ) {
        // Header with hero timer
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(top = 4.dp, start = 16.dp, end = 16.dp, bottom = 4.dp),
        ) {
            // Ambient glow
            if (isActive) {
                Box(
                    modifier = Modifier
                        .size(200.dp, 140.dp)
                        .align(Alignment.Center)
                        .blur(50.dp)
                        .background(
                            brush = Brush.radialGradient(
                                colors = listOf(Color(0xFF6B8F8B).copy(alpha = 0.25f), Color.Transparent),
                            ),
                        ),
                )
            }

            // Hero timer (drawn first so buttons overlay it)
            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                AnimatedVisibility(
                    visible = isActive,
                    enter = fadeIn() + scaleIn(initialScale = 0.8f),
                    exit = fadeOut() + scaleOut(targetScale = 0.8f),
                ) {
                    // Bounce between timer and encouragement
                    AnimatedContent(
                        targetState = encouragement,
                        transitionSpec = {
                            (scaleIn(
                                animationSpec = spring(dampingRatio = 0.6f),
                                initialScale = 0.85f,
                            ) + fadeIn()) togetherWith (scaleOut(
                                targetScale = 0.85f,
                            ) + fadeOut()) using SizeTransform(clip = false)
                        },
                        contentAlignment = Alignment.Center,
                        label = "hero-bounce",
                    ) { msg ->
                        if (msg != null) {
                            Text(
                                text = msg,
                                color = Color(0xFF6BC89B),
                                fontSize = 28.sp,
                                fontWeight = FontWeight.SemiBold,
                                fontFamily = TimerFontFamily,
                                textAlign = TextAlign.Center,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(vertical = 16.dp),
                            )
                        } else {
                            Text(
                                text = sess.elapsedDisplay,
                                color = Color(0xFFE8E4DF),
                                fontSize = 96.sp,
                                fontWeight = FontWeight.SemiBold,
                                fontFamily = TimerFontFamily,
                                lineHeight = 96.sp,
                                letterSpacing = (-0.02).sp,
                                textAlign = TextAlign.Center,
                                modifier = Modifier
                                    .clickable(
                                        enabled = isManual && pgm.running,
                                        interactionSource = remember { MutableInteractionSource() },
                                        indication = null,
                                    ) {
                                        durationEditOpen = !durationEditOpen
                                        haptic(context, 10)
                                    },
                            )
                        }
                    }
                }

                // Manual remaining time
                if (isManual && pgm.running) {
                    Text(
                        text = "${fmtDur(pgm.totalRemaining.toInt())} remaining of ${fmtDur(pgm.totalDuration.toInt())}",
                        color = Color(0x59E8E4DF),
                        fontSize = 12.sp,
                        fontFamily = TimerFontFamily,
                    )
                }

                // Duration edit buttons
                AnimatedVisibility(
                    visible = durationEditOpen && isManual && pgm.running,
                    enter = fadeIn() + expandVertically(),
                    exit = fadeOut() + shrinkVertically(),
                ) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        modifier = Modifier.padding(top = 8.dp),
                    ) {
                        for (d in listOf(-10, -5, 5, 10)) {
                            DurationButton(d) {
                                viewModel.adjustDuration(d * 60)
                                haptic(context, 25)
                            }
                        }
                    }
                }
            }

            // Home button (top-left, drawn after timer so it's on top)
            if (isActive || pgm.completed) {
                IconButton(
                    onClick = { onNavigateHome(); haptic(context, 15) },
                    modifier = Modifier.align(Alignment.TopStart),
                ) {
                    Icon(Icons.Default.Home, "Home", tint = Color(0x59E8E4DF).copy(alpha = 0.7f))
                }
            }

            // Voice button (top-right, drawn after timer so it's on top)
            if (isActive || pgm.completed) {
                IconButton(
                    onClick = {
                        haptic(context, if (voiceState == "idle") 20 else 10)
                        onVoiceToggle(null)
                    },
                    modifier = Modifier.align(Alignment.TopEnd),
                ) {
                    Icon(
                        Icons.Default.Mic,
                        contentDescription = when (voiceState) {
                            "listening" -> "Listening"
                            "speaking" -> "Speaking"
                            else -> "Voice"
                        },
                        tint = when (voiceState) {
                            "listening" -> Color(0xFFC45C52)
                            "speaking" -> Color(0xFF8B7FA0)
                            else -> Color(0x59E8E4DF).copy(alpha = 0.7f)
                        },
                    )
                }
            }
        }

        // Metrics row
        MetricsRow(viewModel = viewModel)

        // Main content area (HUD / Complete / Idle)
        Box(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(top = 6.dp),
        ) {
            AnimatedContent(
                targetState = when {
                    pgm.program != null && pgm.running -> "hud"
                    pgm.completed -> "complete"
                    else -> "idle"
                },
                transitionSpec = {
                    fadeIn() + scaleIn(initialScale = 0.96f) togetherWith
                            fadeOut() + scaleOut(targetScale = 0.96f)
                },
                label = "run-content",
            ) { state ->
                when (state) {
                    "hud" -> ProgramHUD(viewModel = viewModel, modifier = Modifier.fillMaxSize())
                    "complete" -> {
                        Column(modifier = Modifier.fillMaxSize()) {
                            ProgramComplete(
                                viewModel = viewModel,
                                onVoice = { haptic(context, 20); onVoiceToggle(null) },
                                modifier = Modifier.weight(1f),
                            )
                            HistoryList(
                                variant = "compact",
                            )
                        }
                    }
                    else -> {
                        Box(modifier = Modifier.fillMaxSize()) {
                            IdleCard(
                                viewModel = viewModel,
                                onVoice = { prompt -> haptic(context, 20); onVoiceToggle(prompt) },
                                modifier = Modifier.fillMaxSize(),
                            )
                            // Floating home/voice icons — rendered after IdleCard so they draw on top
                            if (!isActive) {
                                IconButton(
                                    onClick = { onNavigateHome(); haptic(context, 15) },
                                    modifier = Modifier
                                        .align(Alignment.TopStart)
                                        .padding(start = 28.dp, top = 16.dp)
                                        .size(44.dp)
                                        .background(
                                            color = Color(0xFF1E1D1B),
                                            shape = RoundedCornerShape(12.dp),
                                        )
                                        .border(
                                            width = 1.dp,
                                            color = Color.White.copy(alpha = 0.25f),
                                            shape = RoundedCornerShape(12.dp),
                                        ),
                                ) {
                                    Icon(Icons.Default.Home, "Home", tint = Color(0x59E8E4DF).copy(alpha = 0.7f))
                                }
                                IconButton(
                                    onClick = {
                                        haptic(context, if (voiceState == "idle") 20 else 10)
                                        onVoiceToggle(null)
                                    },
                                    modifier = Modifier
                                        .align(Alignment.TopEnd)
                                        .padding(end = 28.dp, top = 16.dp)
                                        .size(44.dp)
                                        .background(
                                            color = Color(0xFF1E1D1B),
                                            shape = RoundedCornerShape(12.dp),
                                        )
                                        .border(
                                            width = 1.dp,
                                            color = Color.White.copy(alpha = 0.25f),
                                            shape = RoundedCornerShape(12.dp),
                                        ),
                                ) {
                                    Icon(
                                        Icons.Default.Mic,
                                        "Voice",
                                        tint = when (voiceState) {
                                            "listening" -> Color(0xFFC45C52)
                                            "speaking" -> Color(0xFF8B7FA0)
                                            else -> Color(0x59E8E4DF).copy(alpha = 0.7f)
                                        },
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }

        // Bottom bar
        BottomBar(viewModel = viewModel)
    }
}

@Composable
private fun RunningScreenLandscape(
    viewModel: TreadmillViewModel,
    voiceState: String,
    onNavigateHome: () -> Unit,
    onVoiceToggle: (String?) -> Unit,
    isActive: Boolean,
    isManual: Boolean,
    durationEditOpen: Boolean,
    onDurationEditToggle: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val sess by viewModel.derivedSession.collectAsState()
    val pgm by viewModel.derivedProgram.collectAsState()
    val status by viewModel.status.collectAsState()
    val encouragement by viewModel.encouragement.collectAsState()
    val context = LocalContext.current

    Row(
        modifier = modifier
            .fillMaxSize()
            .background(Color(0xFF121210))
            .systemBarsPadding(),
    ) {
        // Left column: timer + metrics + HUD
        Column(
            modifier = Modifier
                .weight(1f)
                .fillMaxHeight(),
        ) {
            // Header row with home + voice buttons
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 8.dp, start = 12.dp, end = 12.dp),
            ) {
                if (isActive || pgm.completed) {
                    IconButton(
                        onClick = { onNavigateHome(); haptic(context, 15) },
                        modifier = Modifier.align(Alignment.TopStart),
                    ) {
                        Icon(Icons.Default.Home, "Home", tint = Color(0x59E8E4DF).copy(alpha = 0.7f))
                    }
                    IconButton(
                        onClick = {
                            haptic(context, if (voiceState == "idle") 20 else 10)
                            onVoiceToggle(null)
                        },
                        modifier = Modifier.align(Alignment.TopEnd),
                    ) {
                        Icon(
                            Icons.Default.Mic,
                            "Voice",
                            tint = when (voiceState) {
                                "listening" -> Color(0xFFC45C52)
                                "speaking" -> Color(0xFF8B7FA0)
                                else -> Color(0x59E8E4DF).copy(alpha = 0.7f)
                            },
                        )
                    }
                }

                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    AnimatedVisibility(
                        visible = isActive,
                        enter = fadeIn() + scaleIn(initialScale = 0.8f),
                        exit = fadeOut() + scaleOut(targetScale = 0.8f),
                    ) {
                        AnimatedContent(
                            targetState = encouragement,
                            transitionSpec = {
                                (scaleIn(
                                    animationSpec = spring(dampingRatio = 0.6f),
                                    initialScale = 0.85f,
                                ) + fadeIn()) togetherWith (scaleOut(
                                    targetScale = 0.85f,
                                ) + fadeOut()) using SizeTransform(clip = false)
                            },
                            contentAlignment = Alignment.Center,
                            label = "hero-bounce-landscape",
                        ) { msg ->
                            if (msg != null) {
                                Text(
                                    text = msg,
                                    color = Color(0xFF6BC89B),
                                    fontSize = 24.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    fontFamily = TimerFontFamily,
                                    textAlign = TextAlign.Center,
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(vertical = 12.dp),
                                )
                            } else {
                                Text(
                                    text = sess.elapsedDisplay,
                                    color = Color(0xFFE8E4DF),
                                    fontSize = 72.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    fontFamily = TimerFontFamily,
                                    lineHeight = 72.sp,
                                    textAlign = TextAlign.Center,
                                    modifier = Modifier.clickable(
                                        enabled = isManual && pgm.running,
                                        interactionSource = remember { MutableInteractionSource() },
                                        indication = null,
                                    ) { onDurationEditToggle(); haptic(context, 10) },
                                )
                            }
                        }
                    }
                }
            }

            MetricsRow(viewModel = viewModel)

            // HUD / Complete / Idle
            Box(modifier = Modifier.weight(1f).fillMaxWidth()) {
                when {
                    pgm.program != null && pgm.running -> ProgramHUD(viewModel = viewModel, modifier = Modifier.fillMaxSize())
                    pgm.completed -> ProgramComplete(
                        viewModel = viewModel,
                        onVoice = { haptic(context, 20); onVoiceToggle(null) },
                    )
                    else -> IdleCard(
                        viewModel = viewModel,
                        onVoice = { prompt -> haptic(context, 20); onVoiceToggle(prompt) },
                        modifier = Modifier.fillMaxSize(),
                    )
                }
            }
        }

        // Right column: controls stacked vertically + stop button
        val bottomSafe = WindowInsets.safeDrawing.asPaddingValues().calculateBottomPadding()
        Column(
            modifier = Modifier
                .width(220.dp)
                .fillMaxHeight()
                .padding(end = 8.dp, top = 8.dp, bottom = max(bottomSafe, 4.dp)),
            verticalArrangement = Arrangement.Bottom,
        ) {
            SpeedInclineControls(
                viewModel = viewModel,
                vertical = true,
                modifier = Modifier.weight(1f, fill = false),
            )
            Spacer(Modifier.height(6.dp))
            // Inline stop button for landscape (avoids BottomBar nav padding)
            val isRunning = status.emulate && (status.emuSpeed > 0 || (pgm.running && !pgm.paused))
            if (pgm.paused) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Button(
                        onClick = { viewModel.pauseProgram(); haptic(context, 25) },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Color(0xFF6BC89B),
                            contentColor = Color.White,
                        ),
                        shape = RoundedCornerShape(14.dp),
                        contentPadding = PaddingValues(horizontal = 8.dp),
                        modifier = Modifier.weight(2f).height(50.dp),
                    ) {
                        Text("Resume", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                    }
                    Button(
                        onClick = { viewModel.resetAll(); haptic(context, longArrayOf(50, 30, 50)) },
                        colors = ButtonDefaults.buttonColors(
                            containerColor = Color(0xFFC45C52).copy(alpha = 0.15f),
                            contentColor = Color(0xFFC45C52),
                        ),
                        shape = RoundedCornerShape(14.dp),
                        contentPadding = PaddingValues(horizontal = 8.dp),
                        modifier = Modifier.weight(1f).height(50.dp),
                    ) {
                        Text("Reset", fontSize = 15.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            } else {
                Button(
                    onClick = {
                        if (isRunning) { viewModel.pauseProgram(); haptic(context, longArrayOf(50, 30, 50)) }
                    },
                    enabled = isRunning,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = if (isRunning) Color(0xFFC45C52) else Color(0x3D787880),
                        contentColor = if (isRunning) Color.White else Color(0x59E8E4DF),
                        disabledContainerColor = Color(0x3D787880),
                        disabledContentColor = Color(0x59E8E4DF),
                    ),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.fillMaxWidth().height(50.dp).alpha(if (isRunning) 1f else 0.4f),
                ) {
                    Text("Stop", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}

@Composable
private fun DurationButton(
    deltaMinutes: Int,
    onClick: () -> Unit,
) {
    Box(
        modifier = Modifier
            .height(36.dp)
            .background(
                color = Color(0xFF1E1D1B),
                shape = RoundedCornerShape(9999.dp),
            )
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                onClick = onClick,
            )
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = "${if (deltaMinutes > 0) "+" else ""}${deltaMinutes}m",
            color = if (deltaMinutes > 0) Color(0xFF6BC89B) else Color(0x59E8E4DF),
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }
}
