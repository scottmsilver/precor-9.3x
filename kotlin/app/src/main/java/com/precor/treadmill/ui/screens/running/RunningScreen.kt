package com.precor.treadmill.ui.screens.running

import android.content.res.Configuration
import android.graphics.BitmapFactory
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.Typeface
import androidx.compose.animation.*
import androidx.compose.animation.core.*
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.calculateStartPadding
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
import androidx.compose.ui.platform.LocalLayoutDirection
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
import com.precor.treadmill.data.preferences.ServerPreferences
import com.precor.treadmill.ui.theme.Backgrounds
import org.koin.compose.koinInject
import com.precor.treadmill.ui.components.ProgramBrowser
import com.precor.treadmill.ui.theme.GlassParams
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.LocalOverlayBackground
import com.precor.treadmill.ui.theme.LocalOverlayScrimTint
import com.precor.treadmill.ui.theme.LocalPhotoSampler
import com.precor.treadmill.ui.theme.PhotoSampler
import com.precor.treadmill.ui.theme.TimerFontFamily
import com.precor.treadmill.ui.theme.composeTextColor
import com.precor.treadmill.ui.theme.composeTintColor
import com.precor.treadmill.ui.theme.readability.AdvicePrior
import com.precor.treadmill.ui.theme.readability.NormRect
import com.precor.treadmill.ui.theme.readability.Role
import com.precor.treadmill.ui.theme.readability.Theme as ReadTheme
import com.precor.treadmill.ui.theme.readability.chooseTheme
import com.precor.treadmill.ui.theme.readability.composite
import com.precor.treadmill.ui.theme.readability.cropMapRect
import com.precor.treadmill.ui.theme.readability.fitRegion
import com.precor.treadmill.ui.theme.readability.Rgb
import com.precor.treadmill.ui.theme.readability.legibleTreatment
import com.precor.treadmill.ui.theme.readability.solveFreeText
import com.precor.treadmill.ui.theme.readability.apcaLc
import com.precor.treadmill.ui.theme.readability.CHARCOAL
import com.precor.treadmill.ui.theme.readability.IVORY
import com.precor.treadmill.ui.theme.readability.rgb
import com.precor.treadmill.ui.theme.readability.ensureLegible
import com.precor.treadmill.ui.theme.readability.sampleRegion
import com.precor.treadmill.ui.theme.readability.sampleRegionPixels
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

/**
 * Holds the readability decision for the running screen: one coherent photo-derived
 * Theme (tint + ivory/charcoal text + blur), the per-region scrim alphas that each
 * text block needs to clear its APCA target, and a bridging [GlassParams] so the
 * child panels that still read [LocalGlassParams] stay visually coherent.
 */
private data class RunReadability(
    val theme: ReadTheme,
    val scrims: Map<String, Double>,
    val glass: GlassParams,
    val sampler: PhotoSampler?,
    // Free-floating hero text (timer) solved against ITS measured background: its own polarity
    // (dark over a bright sky, light over dark) plus a scrim only if a mid-tone forces one.
    val freeTextColor: Color,
    val freeScrimAlpha: Double,
    // The encouragement message keeps its green identity but is luminance-nudged to stay legible
    // over the timer-region background.
    val encouragementColor: Color,
)

/**
 * Sample the local background under each text block of the running layout, pick one
 * coherent Theme via the readability engine, and compute the per-region scrim alphas.
 * Runs once (remembered) — no Gemini call; a neutral [AdvicePrior] still yields a
 * legible-by-construction result via APCA.
 */
@Composable
private fun rememberRunReadability(bgRes: Int): RunReadability {
    val context = LocalContext.current
    // The background is drawn full-screen with ContentScale.Crop, so sample the
    // pixels the user actually sees by mapping each block through the same crop.
    val config = LocalConfiguration.current
    val density = LocalDensity.current
    val containerW = with(density) { config.screenWidthDp.dp.toPx() }
    val containerH = with(density) { config.screenHeightDp.dp.toPx() }
    return remember(containerW, containerH, bgRes) {
        val opts = BitmapFactory.Options().apply { inSampleSize = 4 }
        val bmp = BitmapFactory.decodeResource(context.resources, bgRes, opts)
        if (bmp == null) {
            // decodeResource can return null — fall back to the engine's neutral theme.
            val neutral = chooseTheme(emptyList(), AdvicePrior()).theme
            return@remember RunReadability(neutral, emptyMap(), GlassParams.Default, null, Color(0xFFE8E4DF), 0.0, Color(0xFF6BC89B))
        }
        // Pull the pixels once; reused for block sampling AND the per-element PhotoSampler.
        val pxW = bmp.width
        val pxH = bmp.height
        val px = IntArray(pxW * pxH)
        bmp.getPixels(px, 0, pxW, 0, 0, pxW, pxH)
        bmp.recycle()
        val blocks = listOf(
            Triple("timer", Role.HERO, NormRect(0.30, 0.06, 0.40, 0.18)),
            Triple("speed", Role.BODY, NormRect(0.08, 0.30, 0.26, 0.12)),
            Triple("incline", Role.BODY, NormRect(0.37, 0.30, 0.26, 0.12)),
            Triple("distance", Role.BODY, NormRect(0.66, 0.30, 0.26, 0.12)),
            Triple("hint", Role.MUTED, NormRect(0.30, 0.84, 0.40, 0.08)),
        )
        val regions = blocks.map { (id, role, rect) ->
            sampleRegionPixels(px, pxW, pxH, cropMapRect(pxW, pxH, containerW, containerH, rect), id, role)
        }
        val choice = chooseTheme(regions, AdvicePrior())
        val perRegion = regions.associate { it.id to fitRegion(choice.theme, it).scrimAlpha }
        // Bridge to GlassParams for child panels (MetricsRow / HUD / controls / bottom bar)
        // that still read LocalGlassParams: drive their tint opacity from the metrics scrim
        // so they darken in step with the engine decision; keep the photo-derived blur.
        // Engine base opacity (a floor). Each panel raises it further for ITS OWN accents
        // over ITS OWN measured background via LegibleGlassPanel — a uniform per-panel scrim
        // (consistent in x/y within each panel), so darkening is tailored to where each
        // panel actually sits on the photo.
        val metricsScrim = perRegion["speed"] ?: choice.theme.baseScrimAlpha
        val panelOpacity = metricsScrim.toFloat().coerceIn(0.30f, 0.62f)
        // The effective color behind panel text = the engine tint composited over the
        // metrics-region photo at the panel opacity. Accent text is checked against this.
        val speedAvg = regions.first { it.id == "speed" }.avg
        val panelBgRgb = composite(speedAvg, choice.theme.tint, panelOpacity.toDouble())
        val panelBg = Color(
            panelBgRgb.r.toInt().coerceIn(0, 255),
            panelBgRgb.g.toInt().coerceIn(0, 255),
            panelBgRgb.b.toInt().coerceIn(0, 255),
        )
        val glass = GlassParams.Default.copy(
            blur = choice.theme.blurDp.dp,
            panelOpacity = panelOpacity,
            tint = choice.theme.composeTintColor(),
            textColor = choice.theme.composeTextColor(),
            panelBg = panelBg,
        )
        // Per-element sampler: each overlay widget measures the actual pixels behind it.
        val sampler = PhotoSampler(px, pxW, pxH, containerW, containerH)
        // Free-floating text (timer/encouragement/hint) over the raw photo: solve its own
        // polarity + scrim against the timer region (the brightest hero spot).
        val (freeRgb, freeScrim) = solveFreeText(
            regions.first { it.id == "timer" }.avg, choice.theme.tint, 75.0,
        )
        val freeColor = Color(
            freeRgb.r.toInt().coerceIn(0, 255),
            freeRgb.g.toInt().coerceIn(0, 255),
            freeRgb.b.toInt().coerceIn(0, 255),
        )
        val encRgb = ensureLegible(rgb(107, 200, 155), regions.first { it.id == "timer" }.avg, 60.0)
        val encouragementColor = Color(
            encRgb.r.toInt().coerceIn(0, 255),
            encRgb.g.toInt().coerceIn(0, 255),
            encRgb.b.toInt().coerceIn(0, 255),
        )
        RunReadability(choice.theme, perRegion, glass, sampler, freeColor, freeScrim, encouragementColor)
    }
}

@Composable
fun RunningScreen(
    viewModel: TreadmillViewModel,
    onVoiceToggle: (String?) -> Unit,
    onExitToHome: () -> Unit = {},
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
            onExitToHome = onExitToHome,
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

    val bgPrefs: ServerPreferences = koinInject()
    val bgKey by bgPrefs.backgroundImage.collectAsState(initial = "ridge")
    val bgRes = Backgrounds.resFor(bgKey)
    val readability = rememberRunReadability(bgRes)
    val theme = readability.theme
    // The free-floating hero timer carries no panel, so paint the engine's computed
    // per-region scrim behind it as a soft radial tint (the "gradient scrim" lever) —
    // this is what makes the timer clear its APCA target over a bright clearing.
    val timerScrimColor = theme.composeTintColor().copy(alpha = readability.freeScrimAlpha.toFloat())

    // 3-row layout: top (timer+metrics), middle (HUD), bottom (buttons)
    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Color.Black),
    ) {
        // Background image (full-bleed; per-region scrim handles legibility)
        Image(
            painter = painterResource(bgRes),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
        )

        CompositionLocalProvider(
            LocalGlassParams provides readability.glass,
            LocalOverlayBackground provides readability.glass.panelBg,
            LocalPhotoSampler provides readability.sampler,
            LocalOverlayScrimTint provides readability.glass.tint,
        ) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .windowInsetsPadding(WindowInsets.displayCutout.only(WindowInsetsSides.Top))
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

            // Engine-computed scrim behind the hero timer (soft radial, photo-tinted).
            Box(
                modifier = Modifier
                    .matchParentSize()
                    .blur(48.dp)
                    .background(
                        brush = Brush.radialGradient(
                            colors = listOf(timerScrimColor, Color.Transparent),
                        ),
                    ),
            )

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
                            Text( // legible-exempt: encouragementColor solved per-bg
                                text = glowText(encouragement ?: ""),
                                color = readability.encouragementColor,
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
                            Text( // legible-exempt: freeTextColor solves its own polarity
                                text = timerText(sess.elapsedDisplay),
                                textAlign = TextAlign.Center,
                                style = TextStyle(
                                    color = readability.freeTextColor,
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
    onExitToHome: () -> Unit,
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
        val bgPrefs: ServerPreferences = koinInject()
        val bgKey by bgPrefs.backgroundImage.collectAsState(initial = "ridge")
        val bgRes = Backgrounds.resFor(bgKey)
        val readability = rememberRunReadability(bgRes)
        val theme = readability.theme
        val timerScrimColor = theme.composeTintColor().copy(alpha = readability.freeScrimAlpha.toFloat())

        // Background image (full-bleed; per-region scrim handles legibility)
        Image(
            painter = painterResource(bgRes),
            contentDescription = null,
            contentScale = ContentScale.Crop,
            modifier = Modifier.fillMaxSize(),
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
        CompositionLocalProvider(
            LocalGlassParams provides readability.glass,
            LocalOverlayBackground provides readability.glass.panelBg,
            LocalPhotoSampler provides readability.sampler,
            LocalOverlayScrimTint provides readability.glass.tint,
        ) {
        // Ridgeline HUD owns the full landscape running console (map + rail + action bar).
        Column(
            modifier = Modifier
                .fillMaxSize()
                .windowInsetsPadding(WindowInsets.displayCutout.only(WindowInsetsSides.Top)),
        ) {
            RidgelineHud(
                viewModel = viewModel,
                onExitToHome = onExitToHome,
                modifier = Modifier.fillMaxSize(),
            )
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
        Text( // legible-exempt: on opaque pill
            text = "${if (deltaMinutes > 0) "+" else ""}${deltaMinutes}m",
            color = if (deltaMinutes > 0) Color(0xFF6BC89B) else Color(0x59E8E4DF), // legible-exempt: on opaque pill
            fontSize = 13.sp,
            fontWeight = FontWeight.SemiBold,
        )
    }
}
