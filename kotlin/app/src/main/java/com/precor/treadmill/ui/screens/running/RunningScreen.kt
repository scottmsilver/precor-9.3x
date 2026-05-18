package com.precor.treadmill.ui.screens.running

import android.content.res.Configuration
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.Typeface
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.blur
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.FirstBaseline
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.core.content.res.ResourcesCompat
import com.precor.treadmill.R
import com.precor.treadmill.ui.components.ProgramBrowser
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.TimerFontFamily
import com.precor.treadmill.ui.theme.rememberGlassParams
import com.precor.treadmill.ui.util.glowText
import com.precor.treadmill.ui.util.timerText
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.delay

/** Symmetric edge padding for top (via timer trim) and bottom (via Column padding) */
private val EdgePad = 16.dp

/**
 * Pre-compute the glyph bounds (pixels above/below baseline) for timer characters,
 * using the actual font at the given size. These values are combined with
 * placeable[FirstBaseline] in the layout modifier to compute exact trim amounts.
 *
 * Returns (glyphAboveBaseline, glyphBelowBaseline) in pixels.
 */
@Composable
private fun timerGlyphBounds(fontSize: TextUnit): Pair<Int, Int> {
    val context = LocalContext.current
    val density = LocalDensity.current
    val fontSizePx = with(density) { fontSize.toPx() }

    return remember(fontSizePx) {
        val typeface = ResourcesCompat.getFont(context, R.font.inter_variable) ?: Typeface.DEFAULT
        val paint = Paint().apply {
            textSize = fontSizePx
            this.typeface = typeface
        }
        val bounds = Rect()
        paint.getTextBounds("0:00", 0, 4, bounds)
        // bounds.top is negative (above baseline), bounds.bottom is positive (below baseline)
        (-bounds.top) to bounds.bottom
    }
}

@Composable
fun RunningScreen(
    viewModel: TreadmillViewModel,
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
    // Initialize to current state so rotation doesn't re-trigger enter animation
    var visualActive by remember { mutableStateOf(physicalActive) }
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
    // Pre-seed transition so rotation doesn't re-trigger enter animation
    val timerVisible = remember { MutableTransitionState(isActive) }
    timerVisible.targetState = isActive
    var durationEditOpen by remember { mutableStateOf(false) }
    val configuration = LocalConfiguration.current
    val isLandscape = configuration.orientation == Configuration.ORIENTATION_LANDSCAPE

    if (isLandscape) {
        // Landscape: side-by-side layout
        RunningScreenLandscape(
            viewModel = viewModel,
            onVoiceToggle = onVoiceToggle,
            timerVisible = timerVisible,
            isManual = isManual,
            durationEditOpen = durationEditOpen,
            onDurationEditToggle = { durationEditOpen = !durationEditOpen },
            modifier = modifier,
        )
        return
    }

    // Edge padding applied to Column bottom; timer trim matches this value
    val edgePadPx = with(LocalDensity.current) { EdgePad.roundToPx() }
    val (glyphAbove, glyphBelow) = timerGlyphBounds(96.sp)
    val timerTrimmedHeight = with(LocalDensity.current) {
        (glyphAbove + glyphBelow + 2 * edgePadPx).toDp()
    }

    val glassParams = rememberGlassParams(R.drawable.bg_forest)

    // 3-row layout: top (timer+metrics), middle (HUD), bottom (buttons)
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Color.Black),
    ) {
        // Background image
        Image(
            painter = painterResource(R.drawable.bg_forest),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )

        // Overlay gradient
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    brush = Brush.verticalGradient(
                        colors = listOf(
                            Color.Black.copy(alpha = glassParams.overlayOpacity + 0.05f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity / 2f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity / 2f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity + 0.08f),
                        ),
                    ),
                ),
        )

        CompositionLocalProvider(LocalGlassParams provides glassParams) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(top = 0.dp, bottom = EdgePad),
    ) {
        // ROW 1: Timer + Metrics (wraps content)
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
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

            Column(
                modifier = Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                AnimatedVisibility(
                    visibleState = timerVisible,
                    enter = fadeIn() + scaleIn(initialScale = 0.8f),
                    exit = fadeOut() + scaleOut(targetScale = 0.8f),
                ) {
                    AnimatedContent(
                        targetState = encouragement != null,
                        transitionSpec = {
                            (scaleIn(
                                animationSpec = spring(dampingRatio = 0.6f),
                                initialScale = 0.85f,
                            ) + fadeIn()) togetherWith (scaleOut(
                                targetScale = 0.85f,
                            ) + fadeOut()) using SizeTransform(clip = false) { _, _ -> snap() }
                        },
                        contentAlignment = Alignment.Center,
                        label = "hero-bounce",
                    ) { showEncouragement ->
                        if (showEncouragement) {
                            Text(
                                text = glowText(encouragement ?: ""),
                                color = Color(0xFF6BC89B),
                                fontSize = 28.sp,
                                fontWeight = FontWeight.Medium,
                                fontFamily = TimerFontFamily,
                                letterSpacing = (-0.03).em,
                                textAlign = TextAlign.Center,
                                style = TextStyle(
                                    shadow = Shadow(
                                        color = Color.Black.copy(alpha = 0.5f),
                                        offset = Offset(0f, 2f),
                                        blurRadius = 12f,
                                    ),
                                ),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(timerTrimmedHeight)
                                    .wrapContentHeight(Alignment.CenterVertically),
                            )
                        } else {
                            Text(
                                text = timerText(sess.elapsedDisplay),
                                textAlign = TextAlign.Center,
                                style = TextStyle(
                                    color = Color(0xFFE8E4DF),
                                    fontSize = 96.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    fontFamily = TimerFontFamily,
                                    lineHeight = 96.sp,
                                    letterSpacing = (-0.03).em,
                                    fontFeatureSettings = "tnum",
                                    shadow = Shadow(
                                        color = Color.Black.copy(alpha = 0.5f),
                                        offset = Offset(0f, 2f),
                                        blurRadius = 12f,
                                    ),
                                ),
                                modifier = Modifier
                                    .layout { measurable, constraints ->
                                        val placeable = measurable.measure(constraints)
                                        val baseline = placeable[FirstBaseline]
                                        val visibleTop = baseline - glyphAbove
                                        val visibleBottom = baseline + glyphBelow
                                        val trimTop = (visibleTop - edgePadPx).coerceAtLeast(0)
                                        val trimBottom = (placeable.height - visibleBottom - edgePadPx).coerceAtLeast(0)
                                        layout(placeable.width, placeable.height - trimTop - trimBottom) {
                                            placeable.place(0, -trimTop)
                                        }
                                    }
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
        }

        MetricsRow(viewModel = viewModel)

        // ROW 2: HUD / Complete / Idle (fills remaining space)
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
                            ProgramBrowser(
                                variant = "compact",
                            )
                        }
                    }
                    else -> {
                        IdleCard(
                            viewModel = viewModel,
                            onVoice = { prompt -> haptic(context, 20); onVoiceToggle(prompt) },
                            modifier = Modifier.fillMaxSize(),
                        )
                    }
                }
            }
        }

        // ROW 3: Bottom bar (wraps content, no internal padding)
        BottomBar(viewModel = viewModel, externalPadding = true)
    }
        } // CompositionLocalProvider
    } // Box
}

@Composable
private fun RunningScreenLandscape(
    viewModel: TreadmillViewModel,
    onVoiceToggle: (String?) -> Unit,
    timerVisible: MutableTransitionState<Boolean>,
    isManual: Boolean,
    durationEditOpen: Boolean,
    onDurationEditToggle: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val sess by viewModel.derivedSession.collectAsState()
    val pgm by viewModel.derivedProgram.collectAsState()
    val encouragement by viewModel.encouragement.collectAsState()
    val context = LocalContext.current

    // Use BoxWithConstraints to scale elements proportionally to available height
    BoxWithConstraints(
        modifier = modifier
            .fillMaxSize()
            .background(Color.Black),
    ) {
        val glassParams = rememberGlassParams(R.drawable.bg_forest)

        // Background image
        Image(
            painter = painterResource(R.drawable.bg_forest),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )

        // Overlay gradient
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    brush = Brush.verticalGradient(
                        colors = listOf(
                            Color.Black.copy(alpha = glassParams.overlayOpacity + 0.05f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity / 2f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity / 2f),
                            Color.Black.copy(alpha = glassParams.overlayOpacity + 0.08f),
                        ),
                    ),
                ),
        )

        // Proportional scaling (reference: ~740dp tablet landscape)
        val h = maxHeight.value
        val w = maxWidth.value
        val timerFontSize = (h * 0.14f).coerceIn(48f, 140f).sp
        val encourageFontSize = (h * 0.05f).coerceIn(18f, 42f).sp
        val metricsScale = (h / 380f).coerceIn(1f, 2f)
        val controlsWidth = (w * 0.28f).coerceIn(240f, 400f).dp

        val edgePad = 16.dp
        val edgePadPx = with(LocalDensity.current) { edgePad.roundToPx() }
        val (lsGlyphAbove, lsGlyphBelow) = timerGlyphBounds(timerFontSize)
        val lsTimerTrimmedHeight = with(LocalDensity.current) {
            (lsGlyphAbove + lsGlyphBelow + 2 * edgePadPx).toDp()
        }

        // 3-row layout: top (timer+metrics), middle (HUD+controls), bottom (buttons)
        CompositionLocalProvider(LocalGlassParams provides glassParams) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(top = 0.dp, bottom = EdgePad),
        ) {
            // ROW 1: Timer + Metrics (wraps content)
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp),
                contentAlignment = Alignment.Center,
            ) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    AnimatedVisibility(
                        visibleState = timerVisible,
                        enter = fadeIn() + scaleIn(initialScale = 0.8f),
                        exit = fadeOut() + scaleOut(targetScale = 0.8f),
                    ) {
                        AnimatedContent(
                            targetState = encouragement != null,
                            transitionSpec = {
                                (scaleIn(
                                    animationSpec = spring(dampingRatio = 0.6f),
                                    initialScale = 0.85f,
                                ) + fadeIn()) togetherWith (scaleOut(
                                    targetScale = 0.85f,
                                ) + fadeOut()) using SizeTransform(clip = false) { _, _ -> snap() }
                            },
                            contentAlignment = Alignment.Center,
                            label = "hero-bounce-landscape",
                        ) { showEncouragement ->
                            if (showEncouragement) {
                                Text(
                                    text = glowText(encouragement ?: ""),
                                    color = Color(0xFF6BC89B),
                                    fontSize = encourageFontSize,
                                    fontWeight = FontWeight.Medium,
                                    fontFamily = TimerFontFamily,
                                    letterSpacing = (-0.03).em,
                                    textAlign = TextAlign.Center,
                                    style = TextStyle(
                                        shadow = Shadow(
                                            color = Color.Black.copy(alpha = 0.5f),
                                            offset = Offset(0f, 2f),
                                            blurRadius = 12f,
                                        ),
                                    ),
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(lsTimerTrimmedHeight)
                                        .wrapContentHeight(Alignment.CenterVertically),
                                )
                            } else {
                                Text(
                                    text = timerText(sess.elapsedDisplay),
                                    textAlign = TextAlign.Center,
                                    style = TextStyle(
                                        color = Color(0xFFE8E4DF),
                                        fontSize = timerFontSize,
                                        fontWeight = FontWeight.SemiBold,
                                        fontFamily = TimerFontFamily,
                                        lineHeight = timerFontSize,
                                        letterSpacing = (-0.03).em,
                                        fontFeatureSettings = "tnum",
                                        shadow = Shadow(
                                            color = Color.Black.copy(alpha = 0.5f),
                                            offset = Offset(0f, 2f),
                                            blurRadius = 12f,
                                        ),
                                    ),
                                    modifier = Modifier
                                        .layout { measurable, constraints ->
                                            val placeable = measurable.measure(constraints)
                                            val baseline = placeable[FirstBaseline]
                                            val trimTop = (baseline - lsGlyphAbove - edgePadPx).coerceAtLeast(0)
                                            val trimBottom = (placeable.height - baseline - lsGlyphBelow - edgePadPx).coerceAtLeast(0)
                                            layout(placeable.width, placeable.height - trimTop - trimBottom) {
                                                placeable.place(0, -trimTop)
                                            }
                                        }
                                        .clickable(
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

            MetricsRow(viewModel = viewModel, scale = metricsScale)

            // ROW 2: HUD + speed/incline controls (fills remaining space)
            Row(modifier = Modifier.weight(1f)) {
                Box(modifier = Modifier.weight(1f).fillMaxHeight()) {
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

                SpeedInclineControls(
                    viewModel = viewModel,
                    vertical = true,
                    fillHeight = true,
                    modifier = Modifier
                        .width(controlsWidth)
                        .fillMaxHeight()
                        .padding(end = 8.dp, top = 6.dp, bottom = 6.dp),
                )
            }

            // ROW 3: Stop/Resume buttons (wraps content, no internal padding)
            BottomBar(viewModel = viewModel, showControls = false, externalPadding = true)
        }
        } // CompositionLocalProvider
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
