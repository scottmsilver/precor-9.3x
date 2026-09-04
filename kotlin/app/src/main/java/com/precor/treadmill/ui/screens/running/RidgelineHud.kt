package com.precor.treadmill.ui.screens.running

import android.util.Log
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import android.view.HapticFeedbackConstants
import android.view.MotionEvent
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.pointerInteropFilter
import androidx.compose.foundation.Canvas
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.runtime.CompositionLocalProvider
import com.precor.treadmill.ui.theme.LegibleGlassPanel
import com.precor.treadmill.ui.theme.LegibleText
import com.precor.treadmill.ui.theme.LocalOpacityGroup
import com.precor.treadmill.ui.theme.LocalOverlayBackground
import com.precor.treadmill.ui.theme.OpacityGroup
import com.precor.treadmill.ui.theme.legibleOn
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import com.precor.treadmill.ui.viewmodel.VoiceState
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/**
 * Ridgeline HUD — landscape running console.
 *
 * Layout: top row fills height (LEFT = route map weight 1, RIGHT = ~298dp rail with two
 * stacked stepper cards), BELOW = full-width action bar (Pause/Resume + Stop). Metrics pill
 * overlays top-left of the map, "next" pill top-right.
 *
 * The HUD draws over the running screen's background photo (bg_lake), so its panels go
 * through [LegibleGlassPanel] (adaptive APCA scrim sampled from the photo behind each panel)
 * and value/label text through [LegibleText] / `.legibleOn(...)` against the panel's effective
 * background — integrating the console with the rest of the running screen's readability system.
 */
private const val DOUBLE_TAP_MS = 300L

/** Sync-diagnostics logcat tag: `adb logcat -s RidgelineSync` while a program runs. */
private const val SYNC_TAG = "RidgelineSync"

// PROTOTYPE: see-through map — no full panel scrim; the photo shows through the map
// area and scrims live only under the information (trail band, minimap strip).
const val SEE_THROUGH_MAP = true

@Composable
fun RidgelineHud(
    viewModel: TreadmillViewModel,
    onExitToHome: () -> Unit = {},
    // Permission-safe voice toggle lifted from AppNavigation (see BottomBar's mic button).
    onVoiceToggle: (() -> Unit)? = null,
    voiceState: VoiceState = VoiceState.IDLE,
    modifier: Modifier = Modifier,
) {
    val status by viewModel.status.collectAsState()
    val pgm by viewModel.derivedProgram.collectAsState()
    val sess by viewModel.derivedSession.collectAsState()
    var timerMode by remember { mutableStateOf(TimerMode.COUNT_DOWN) }
    var timerProgramRunning by remember { mutableStateOf(false) }
    LaunchedEffect(pgm.running) {
        timerMode = timerModeForProgramTransition(
            wasRunning = timerProgramRunning,
            isRunning = pgm.running,
            currentMode = timerMode,
        )
        timerProgramRunning = pgm.running
    }

    // Build the route from real planned intervals. Route position = planned SECONDS:
    // the layout follows program time, so interval boundaries are the program clock
    // and the dot advances steadily regardless of belt speed.
    val intervals = pgm.program?.intervals ?: emptyList()
    val route = remember(intervals) {
        RidgelineRoute(
            intervals.map { iv ->
                RouteInterval(
                    grade = iv.incline,
                    speed = iv.speed,
                    durSec = iv.duration,
                )
            },
        )
    }
    // Authoritative marker position from the server: the program clock itself
    // (interval start + elapsed) — structurally in sync with incline changes.
    val serverMd = if (pgm.completed) route.total
        else route.posAtProgram(pgm.currentInterval, pgm.intervalElapsed)

    // The program advances the marker only while it is actually running (and not paused).
    val advancing = pgm.running && !pgm.paused
    // Live belt speed (mph) — still shown in the metrics pill's power estimate.
    val curSpdMph = status.emuSpeed / 10.0

    // Smooth, frame-driven marker position. In the time domain the local advance is
    // just the wall clock (+dt while running), gently reconciled toward the server's
    // program clock so it never drifts. Re-keyed on the route so a new program resets.
    var animatedMd by remember(route) { mutableStateOf(serverMd) }
    // Latch live inputs so the long-lived frame loop always reads current values.
    val advancingState = rememberUpdatedState(advancing)
    val serverMdState = rememberUpdatedState(serverMd)
    LaunchedEffect(route) {
        var last = withFrameNanos { it }
        while (true) {
            val now = withFrameNanos { it }
            val dt = (now - last) / 1e9
            last = now
            // Local advance while the program is running (frozen when paused/stopped).
            if (advancingState.value && dt > 0) animatedMd += dt
            // Reconcile toward the server position. Snap on a large gap in seconds
            // (skip / seek / reset); otherwise ease so it glides.
            // easeTo: cur + (tgt-cur)*(1-e^-rate*dt).
            val gap = serverMdState.value - animatedMd
            animatedMd = if (kotlin.math.abs(gap) > 3.0) {
                serverMdState.value
            } else {
                animatedMd + gap * (1.0 - kotlin.math.exp(-0.9 * dt))
            }
            animatedMd = animatedMd.coerceIn(0.0, route.total)
        }
    }
    val markerDist = animatedMd
    val timerProgramPosition = countdownProgramPosition(
        advancing = advancing,
        completed = pgm.completed,
        serverPosition = serverMd,
        animatedPosition = markerDist,
    )
    val timer = runningTimer(
        countUpElapsedSeconds = sess.displayElapsed,
        programElapsedSeconds = timerProgramPosition,
        totalDurationSeconds = pgm.totalDuration,
        mode = timerMode,
    )

    // --- Sync diagnostics (adb logcat -s RidgelineSync) ---
    // Geometry once per route: what the map is being asked to draw (route position
    // is planned seconds; miles/vert are the derived organic detail).
    LaunchedEffect(route) {
        val segs = (0 until route.count).joinToString(" | ") { i ->
            "iv%d %.0f-%.0fs @%.1fmph g=%.1f%%".format(
                i, route.startOf(i), route.endOf(i), route.speedIdx(i), route.gradeIdx(i),
            )
        }
        Log.d(
            SYNC_TAG,
            "route: total=%.0fs dist=%.2fmi vert=%.1fft [%s]".format(
                route.total, route.totalMi, route.vertAt(route.total), segs,
            ),
        )
    }
    // Position once per server tick: where the server says the user is vs where the
    // marker is drawn. drawnIv is the segment the dot visually sits in — if it differs
    // from the server's interval, the dot is off the bend and this line shows by how far.
    LaunchedEffect(pgm.currentInterval, pgm.intervalElapsed.toInt(), pgm.running, pgm.paused) {
        Log.d(
            SYNC_TAG,
            ("pos: server iv=%d t=%.0fs run=%b pause=%b -> serverPos=%.1fs | " +
                "drawn pos=%.1fs (%.0f%% of route) drawnIv=%d vert=%.1fft").format(
                pgm.currentInterval, pgm.intervalElapsed, pgm.running, pgm.paused, serverMd,
                animatedMd, if (route.total > 0) animatedMd / route.total * 100.0 else 0.0,
                route.idxAt(animatedMd), route.vertAt(animatedMd),
            ),
        )
    }

    val density = LocalDensity.current

    // --- Tap-to-reveal skip controls (ported from the old ProgramHUD) ---
    // Single tap on the map reveals a centered prev/play-pause/next cluster (auto-hides after
    // 4s unless paused). Double-tap the left/right half skips to the previous/next interval.
    val view = LocalView.current
    val scope = rememberCoroutineScope()
    var overlayVisible by remember { mutableStateOf(false) }
    var autoHideKey by remember { mutableIntStateOf(0) }
    var skipFeedback by remember { mutableStateOf<String?>(null) }
    var lastDoubleTapTime by remember { mutableLongStateOf(0L) }
    var lastTapTime by remember { mutableLongStateOf(0L) }
    var lastTapSide by remember { mutableStateOf<String?>(null) }
    var singleTapJob by remember { mutableStateOf<Job?>(null) }
    val multiInterval = pgm.intervalCount > 1
    // Exit-to-home chip (Peloton-style, top-left): persistent while the workout is
    // stopped; while running it's hidden and a tap near the TOP of the map reveals
    // it for a few seconds.
    var exitRevealed by remember { mutableStateOf(false) }
    LaunchedEffect(exitRevealed) {
        if (exitRevealed) { delay(4000); exitRevealed = false }
    }
    val exitVisible = !pgm.running || exitRevealed

    // Auto-hide the overlay after 4s (kept open while paused); autoHideKey restarts it on skip.
    LaunchedEffect(overlayVisible, pgm.paused, autoHideKey) {
        if (overlayVisible && !pgm.paused) {
            delay(4000)
            overlayVisible = false
        }
    }
    // Clear the double-tap feedback flash after 600ms.
    LaunchedEffect(skipFeedback) {
        if (skipFeedback != null) {
            delay(600)
            skipFeedback = null
        }
    }
    // When the program stops (or is replaced), tear down any in-flight tap state and hide the
    // overlay. Otherwise a pending single-tap job — launched from the composition scope, which
    // outlives the pointerInput — could reveal the overlay for a stopped program, and its
    // skip/prev buttons would fire commands at whatever program is loaded next.
    LaunchedEffect(pgm.running) {
        if (!pgm.running) {
            singleTapJob?.cancel(); singleTapJob = null
            lastTapTime = 0; lastTapSide = null
            overlayVisible = false
        }
    }

    // Transparent root so the background photo (bg_lake, drawn behind by RunningScreen) shows
    // through; the map area applies its own photo-derived dark scrim (see RidgelineMap) and the
    // panels dim adaptively, integrating the console with the APCA readability system.
    Column(modifier = modifier.fillMaxSize()) {
        // Top bleed strip: a band of pure photo. The unobtrusive Home chip lives
        // here (left), so the timer's top edge stays level with the rail cards.
        Box(
            // start = gutter (14) + map inner padding (16): the chip's left edge
            // aligns with the timer pill's; nudged down a touch within the strip.
            modifier = Modifier.fillMaxWidth().height(40.dp).padding(start = 30.dp, top = 8.dp),
            contentAlignment = Alignment.CenterStart,
        ) {
            AnimatedExitChip(visible = exitVisible, onClick = onExitToHome)
        }
        // Top row: map PANEL (weight 1) + controls rail. Uniform padding + an 8dp gap so the
        // map, the controls, and (below) the action bar all read as aligned glass cards.
        Row(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(start = 14.dp, end = 14.dp, top = 0.dp, bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // --- MAP (glass panel normally; bare photo behind it in the
            // see-through prototype, where only the trail/strip carry scrims) ---
            val mapContent: @Composable () -> Unit = {
            BoxWithConstraints(
                modifier = Modifier
                    .fillMaxSize()
                    .pointerInput(pgm.running, pgm.intervalCount) {
                        if (!pgm.running) return@pointerInput
                        detectTapGestures { offset ->
                            if (overlayVisible) {
                                // Tap on the dark backdrop (the buttons consume their own taps) hides it.
                                overlayVisible = false
                                view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                            } else if (offset.y < size.height * 0.22f) {
                                // top strip: reveal the exit chip, not the skip overlay
                                exitRevealed = true
                                view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                            } else {
                                val side = if (offset.x / size.width > 0.5f) "right" else "left"
                                val now = System.currentTimeMillis()
                                if (now - lastTapTime < DOUBLE_TAP_MS && lastTapSide == side) {
                                    // Second tap on the same side within the window → skip that direction.
                                    singleTapJob?.cancel(); singleTapJob = null
                                    lastTapTime = 0; lastTapSide = null
                                    if (now - lastDoubleTapTime < 500) return@detectTapGestures
                                    lastDoubleTapTime = now
                                    if (multiInterval) {
                                        view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                                        skipFeedback = side
                                        if (side == "right") viewModel.skipInterval() else viewModel.prevInterval()
                                    }
                                } else {
                                    // First tap: wait out the double-tap window, then reveal the overlay.
                                    lastTapTime = now; lastTapSide = side
                                    singleTapJob?.cancel()
                                    singleTapJob = scope.launch {
                                        delay(DOUBLE_TAP_MS)
                                        lastTapTime = 0; lastTapSide = null
                                        overlayVisible = true
                                        view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                                    }
                                }
                            }
                        }
                    },
            ) {
                // Measured on-screen bounds (px, in map-canvas coords) of the metrics
                // pill, fed to the map so grade chips never collide with it.
                var metricsRect by remember { mutableStateOf<androidx.compose.ui.geometry.Rect?>(null) }
                val pad = with(density) { 16.dp.toPx() }

                RidgelineMap(
                    route = route,
                    markerPos = markerDist,
                    metricsPillRect = metricsRect,
                    modifier = Modifier.fillMaxSize(),
                )

                // Top-left overlay stack: timer pill just above the metrics pill —
                // ON the map, so neither burns layout height. IntrinsicSize.Max +
                // fillMaxWidth children = both boxes share the wider one's width.
                // The chip-dodge guard rect covers the whole stack.
                Column(
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(16.dp)
                        .width(IntrinsicSize.Max)
                        .onSizeChanged { sz ->
                            metricsRect = androidx.compose.ui.geometry.Rect(
                                left = pad, top = pad,
                                right = pad + sz.width, bottom = pad + sz.height,
                            )
                        },
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    TimerPanel(
                        timer = timer,
                        onToggle = { timerMode = timerMode.toggled() },
                        modifier = Modifier.fillMaxWidth(),
                    )
                    MetricsPill(
                        modifier = Modifier.fillMaxWidth(),
                        vert = sess.vertDisplay,
                        dist = sess.distDisplay,
                        // null when no live reading — the pill drops the row entirely.
                        hr = if (status.hrmConnected && status.heartRate > 0) status.heartRate.toString() else null,
                        cal = sess.caloriesDisplay,
                        // Countdown to the next transition (the finish, on the last
                        // interval) — only meaningful while the program runs. The interval
                        // index comes from the server, so bounds-check it (Postel) and drop
                        // the row once completed (running can linger true with 0:00 left).
                        next = if (pgm.running && !pgm.completed &&
                            pgm.currentInterval in 0 until route.count
                        ) formatNextChange(
                            nextChangeProgramPosition = route.endOf(pgm.currentInterval),
                            clock = NextChangeClock(
                                sessionElapsed = sess.displayElapsed,
                                programElapsed = timerProgramPosition,
                                programDuration = pgm.totalDuration,
                            ),
                            timeMark = timerMode.nextChangeTimeMark(),
                        ) else null,
                    )
                }

                // (The NEXT pill is gone — the minimap strip now carries the last/next
                // transition ticks with their program times.)

                // Double-tap skip feedback flashes (left / right halves).
                SkipFeedbackFlash(
                    visible = skipFeedback == "left",
                    forward = false,
                    modifier = Modifier.align(Alignment.CenterStart).padding(start = 40.dp),
                )
                SkipFeedbackFlash(
                    visible = skipFeedback == "right",
                    forward = true,
                    modifier = Modifier.align(Alignment.CenterEnd).padding(end = 40.dp),
                )

                // Tap-to-reveal control cluster: prev / play-pause / next, over a dark backdrop.
                SkipControlsOverlay(
                    visible = overlayVisible,
                    paused = pgm.paused,
                    multiInterval = multiInterval,
                    modifier = Modifier.matchParentSize(),
                    onPrev = {
                        // Guard: skip/prev only make sense on a live program. The reset effect
                        // hides the overlay when running goes false, but guard the command too
                        // so a tap racing that transition can't rewind a stopped program.
                        if (pgm.running) {
                            viewModel.prevInterval(); autoHideKey++
                            view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                        }
                    },
                    onPlayPause = {
                        viewModel.pauseProgram(); autoHideKey++
                        view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                    },
                    onNext = {
                        if (pgm.running) {
                            viewModel.skipInterval(); autoHideKey++
                            view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                        }
                    },
                )
            }
            } // map content
            if (SEE_THROUGH_MAP) {
                Box(modifier = Modifier.weight(1f).fillMaxHeight().clip(RoundedCornerShape(16.dp))) {
                    mapContent()
                }
            } else {
                LegibleGlassPanel(
                    accents = listOf(RidgelineTheme.accent),
                    modifier = Modifier.weight(1f).fillMaxHeight(),
                    shape = RoundedCornerShape(16.dp),
                ) { mapContent() }
            }

            // --- RAIL (fixed ~298dp): the ORIGINAL speed/incline controls (no count-up). ---
            // Top-aligned with the TIMER pill: it sits 16dp into the map, so the
            // rail pads down the same 16dp — one shared top datum.
            SpeedInclineControls(
                viewModel = viewModel,
                vertical = true,
                fillHeight = true,
                modifier = Modifier.width(298.dp).fillMaxHeight().padding(top = 16.dp),
            )
        }

        // --- ACTION BAR --- restore the ORIGINAL bottom buttons (Resume/Reset when paused,
        // Stop when running) by reusing the existing BottomBar; showControls=false since the
        // speed/incline controls live in the rail. Skip controls are NOT here — they live as a
        // tap-to-reveal overlay ON the map (see SkipControlsOverlay), matching the old HUD.
        BottomBar(
            viewModel = viewModel,
            showControls = false,
            externalPadding = true,
            uniformHeight = true,
            onVoiceToggle = onVoiceToggle,
            voiceState = voiceState,
            modifier = Modifier.padding(start = 14.dp, end = 14.dp, bottom = 12.dp),
        )
    }
}

/**
 * Tighten the wide cells monospace gives ',', '.' and ':' without breaking digit columns.
 * Ported from DirectionD.jsx tightNum(): kern punctuation by -0.1em.
 */
private fun tightNum(s: String): AnnotatedString = buildAnnotatedString {
    for (ch in s) {
        if (ch == ',' || ch == '.' || ch == ':') {
            withStyle(SpanStyle(letterSpacing = (-0.1).em)) { append(ch) }
        } else {
            append(ch)
        }
    }
}

@Composable
private fun MetricsPill(
    vert: String,
    dist: String,
    hr: String?,
    cal: String,
    next: NextChangeDisplay?,
    modifier: Modifier = Modifier,
) {
    // Values are RidgelineTheme.fg; let the panel dim the photo behind it so they clear APCA.
    LegibleGlassPanel(
        accents = listOf(RidgelineTheme.fg),
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 9.dp),
            verticalArrangement = Arrangement.spacedBy(7.dp),
        ) {
            MetricRow("ELEVATION", vert, "ft", big = true)
            MetricRow("DISTANCE", dist, "mi")
            // No HRM connected -> no HEART row at all (don't burn a row on "--").
            if (hr != null) MetricRow("HEART", hr, "bpm")
            MetricRow("CALORIES", cal, "cal")
            // Not running -> no NEXT row (nothing is coming).
            if (next != null) {
                MetricRow(
                    label = "NEXT IN",
                    value = next.text,
                    unit = "",
                    contentDescription = next.accessibilityDescription,
                )
            }
        }
    }
}

@Composable
private fun MetricRow(
    label: String,
    value: String,
    unit: String,
    big: Boolean = false,
    contentDescription: String? = null,
) {
    val bg = LocalOverlayBackground.current
    Column(
        modifier = if (contentDescription == null) Modifier else {
            Modifier.clearAndSetSemantics { this.contentDescription = contentDescription }
        },
        verticalArrangement = Arrangement.spacedBy(1.dp),
    ) {
        LegibleText(
            text = label,
            color = RidgelineTheme.dim,
            style = TextStyle(
                fontFamily = RidgelineLabelFamily,
                fontSize = 9.sp,
                letterSpacing = 1.2.sp,
                fontWeight = FontWeight.SemiBold,
            ),
        )
        Row {
            // tightNum() needs an AnnotatedString, which LegibleText can't take; solve the
            // color via legibleOn over the panel's effective background instead.
            // alignByBaseline: the unit sits ON the number's baseline (bottom-alignment
            // hung it below by its descent).
            Text( // legible-exempt: solved via legibleOn over the photo
                text = tightNum(value),
                color = RidgelineTheme.fg.legibleOn(bg, targetLc = 70.0),
                fontFamily = RidgelineMonoFamily,
                fontSize = if (big) 22.sp else 17.sp,
                fontWeight = FontWeight.Medium,
                modifier = Modifier.alignByBaseline(),
            )
            LegibleText(
                text = unit,
                color = RidgelineTheme.dim,
                // Directly adjacent — the color shift (fg number, dim unit) is the
                // separator; no gap needed.
                modifier = Modifier.alignByBaseline(),
                style = TextStyle(
                    fontFamily = RidgelineLabelFamily,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Medium,
                ),
            )
        }
    }
}

/**
 * Tap-to-reveal control cluster (prev / play-pause / next) over a dark backdrop, matching the
 * old HUD. Extracted into its own composable so [AnimatedVisibility] resolves to the plain
 * overload (no enclosing Row/Column receiver to shadow it).
 */
@Composable
private fun SkipControlsOverlay(
    visible: Boolean,
    paused: Boolean,
    multiInterval: Boolean,
    onPrev: () -> Unit,
    onPlayPause: () -> Unit,
    onNext: () -> Unit,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = visible,
        modifier = modifier,
        enter = fadeIn(),
        exit = fadeOut(),
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 0.4f), RoundedCornerShape(16.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(28.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (multiInterval) {
                    OverlayGlassButton(size = 64.dp, contentDescription = "Previous interval", onClick = onPrev) {
                        drawSkipGlyph(forward = false)
                    }
                }
                OverlayGlassButton(
                    size = 84.dp,
                    contentDescription = if (paused) "Resume" else "Pause",
                    onClick = onPlayPause,
                ) { drawPlayPauseGlyph(paused = paused) }
                if (multiInterval) {
                    OverlayGlassButton(size = 64.dp, contentDescription = "Skip to next interval", onClick = onNext) {
                        drawSkipGlyph(forward = true)
                    }
                }
            }
        }
    }
}

/**
 * A circular control in the tap-to-reveal overlay (prev / play-pause / next). A glass circle
 * that adapts its scrim over the photo like every other Ridgeline surface; [glyph] draws the
 * icon. Uses Compose `clickable` so it sits cleanly atop the map's tap-gesture detector.
 */
@Composable
private fun OverlayGlassButton(
    size: Dp,
    contentDescription: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    glyph: androidx.compose.ui.graphics.drawscope.DrawScope.() -> Unit,
) {
    LegibleGlassPanel(
        accents = listOf(RidgelineTheme.fg),
        modifier = modifier
            .size(size)
            .clip(CircleShape)
            .clickable(onClickLabel = contentDescription, onClick = onClick),
        shape = CircleShape,
    ) {
        Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Canvas(modifier = Modifier.size(size * 0.42f)) { glyph() }
        }
    }
}

/** Brief circular flash shown on a double-tap skip (left = previous, right = next). */
@Composable
private fun SkipFeedbackFlash(visible: Boolean, forward: Boolean, modifier: Modifier = Modifier) {
    AnimatedVisibility(
        visible = visible,
        modifier = modifier,
        enter = fadeIn() + scaleIn(initialScale = 0.6f),
        exit = fadeOut() + scaleOut(targetScale = 0.6f),
    ) {
        Box(
            modifier = Modifier
                .size(52.dp)
                .clip(CircleShape)
                .background(Color.Black.copy(alpha = 0.6f)),
            contentAlignment = Alignment.Center,
        ) {
            Canvas(modifier = Modifier.size(26.dp)) { drawSkipGlyph(forward) }
        }
    }
}

/** Skip-to-track glyph (triangle + bar); [forward] picks the direction. */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawSkipGlyph(forward: Boolean) {
    val c = RidgelineTheme.fg
    if (forward) {
        drawPath(Path().apply {
            moveTo(size.width * 0.08f, size.height * 0.15f)
            lineTo(size.width * 0.66f, size.height * 0.5f)
            lineTo(size.width * 0.08f, size.height * 0.85f)
            close()
        }, c)
        drawRect(c, Offset(size.width * 0.74f, size.height * 0.15f), Size(size.width * 0.13f, size.height * 0.7f))
    } else {
        drawRect(c, Offset(size.width * 0.13f, size.height * 0.15f), Size(size.width * 0.13f, size.height * 0.7f))
        drawPath(Path().apply {
            moveTo(size.width * 0.92f, size.height * 0.15f)
            lineTo(size.width * 0.34f, size.height * 0.5f)
            lineTo(size.width * 0.92f, size.height * 0.85f)
            close()
        }, c)
    }
}

/** Play triangle (when paused) or pause bars (when running). */
private fun androidx.compose.ui.graphics.drawscope.DrawScope.drawPlayPauseGlyph(paused: Boolean) {
    val c = RidgelineTheme.fg
    if (paused) {
        drawPath(Path().apply {
            moveTo(size.width * 0.18f, size.height * 0.08f)
            lineTo(size.width * 0.92f, size.height * 0.5f)
            lineTo(size.width * 0.18f, size.height * 0.92f)
            close()
        }, c)
    } else {
        val barW = size.width * 0.26f
        val gap = size.width * 0.16f
        val x1 = (size.width - 2 * barW - gap) / 2
        drawRect(c, Offset(x1, size.height * 0.12f), Size(barW, size.height * 0.76f))
        drawRect(c, Offset(x1 + barW + gap, size.height * 0.12f), Size(barW, size.height * 0.76f))
    }
}

/**
 * Compact session-timer pill just above the map (wrap-content, centered by the
 * caller). A counting clock needs stable digit widths — that's what tnum gives us
 * in the display face: tabular DIGITS without the mono's full-cell colon (which
 * set "0 : 00" with gaping gaps).
 */
/**
 * Unobtrusive Peloton-style exit affordance: a slim "← Home" chip that lives in the
 * top bleed strip. Visibility is managed by the caller (persistent when stopped,
 * tap-near-top reveal while running).
 */
@Composable
private fun AnimatedExitChip(visible: Boolean, onClick: () -> Unit) {
    // Standalone so the plain AnimatedVisibility overload binds (an enclosing
    // Column/Row scope would shadow it — same gotcha as the skip overlay).
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn() + scaleIn(initialScale = 0.92f),
        exit = fadeOut() + scaleOut(targetScale = 0.92f),
    ) {
        ExitHomeChip(onClick = onClick)
    }
}

@Composable
private fun ExitHomeChip(onClick: () -> Unit, modifier: Modifier = Modifier) {
    Row(
        modifier = modifier
            .clip(RoundedCornerShape(9.dp))
            .background(Color(0x990A0F12))
            .clickable(onClick = onClick)
            .padding(horizontal = 11.dp, vertical = 5.dp)
            .semantics { contentDescription = "Exit to home" },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        androidx.compose.material3.Icon(
            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
            contentDescription = null,
            tint = RidgelineTheme.fg.copy(alpha = 0.9f),
            modifier = Modifier.size(15.dp),
        )
        Text( // legible-exempt: fixed ivory on the chip's own dark scrim
            text = "Home",
            color = RidgelineTheme.fg.copy(alpha = 0.9f),
            modifier = Modifier.padding(start = 6.dp),
            fontFamily = RidgelineLabelFamily,
            fontSize = 13.sp,
            fontWeight = FontWeight.Medium,
            // Tight line box: the default font padding hung the text low, so the
            // arrow's shaft didn't split "Home" — kill the extra leading and the
            // Row's vertical centering lines the two up optically.
            lineHeight = 13.sp,
            style = androidx.compose.ui.text.TextStyle(
                platformStyle = androidx.compose.ui.text.PlatformTextStyle(includeFontPadding = false),
            ),
        )
    }
}

@Composable
private fun TimerPanel(
    timer: RunningTimer,
    onToggle: () -> Unit,
    modifier: Modifier = Modifier,
) {
    LegibleGlassPanel(
        accents = listOf(RidgelineTheme.fg),
        modifier = modifier
            .clickable(
                onClickLabel = if (timer.mode == TimerMode.COUNT_DOWN) {
                    "Show count-up timer"
                } else {
                    "Show countdown timer"
                },
                onClick = onToggle,
            )
            .semantics { contentDescription = timer.contentDescription },
        shape = RoundedCornerShape(12.dp),
    ) {
        // Centered so the pill can stretch to match the metrics pill's width.
        Box(modifier = Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            LegibleText(
                text = timer.text,
                color = RidgelineTheme.fg,
                targetLc = 75.0,
                modifier = Modifier.padding(horizontal = 22.dp, vertical = 6.dp),
                style = TextStyle(
                    fontFamily = RidgelineLabelFamily,
                    fontSize = 26.sp,
                    fontWeight = FontWeight.SemiBold,
                    fontFeatureSettings = "tnum",
                ),
            )
        }
    }
}

@Composable
private fun ActionBar(
    viewModel: TreadmillViewModel,
    status: com.precor.treadmill.ui.viewmodel.TreadmillStatus,
    pgm: com.precor.treadmill.ui.viewmodel.DerivedProgram,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val isRunning = status.emulate && (status.emuSpeed > 0 || (pgm.running && !pgm.paused))
    val paused = pgm.paused

    // Group the two buttons so they share one uniform opacity (consistent glass across the row).
    CompositionLocalProvider(LocalOpacityGroup provides remember { OpacityGroup() }) {
        Row(
            modifier = modifier
                .fillMaxWidth()
                .height(64.dp)
                .padding(horizontal = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Pause/Resume — primary, brand green as scrim; the white label is solved over the
            // photo (the green surface dims as much as APCA needs), flex 1.
            HudActionButton(
                text = (if (paused) "RESUME" else "PAUSE").uppercase(),
                brand = RidgelineTheme.accent,
                onClick = { viewModel.pauseProgram(); haptic(context, 25) },
                modifier = Modifier.weight(1f).fillMaxHeight(),
            )
            // Stop — danger, ~32% width, brand red as scrim (DirectionD/README:96-100).
            HudActionButton(
                text = "STOP",
                brand = Color(0xFFC9544A), // rgb(201,84,74) — solved to an opaque red surface
                onClick = {
                    if (isRunning) { viewModel.pauseProgram(); haptic(context, longArrayOf(50, 30, 50)) }
                    else viewModel.resetAll()
                },
                enabled = isRunning || paused,
                modifier = Modifier.weight(0.32f).fillMaxHeight(),
            )
        }
    }
}

/**
 * Action button whose brand color IS its scrim: [LegibleGlassPanel] raises the brand color's
 * opacity (the same APCA scrim math) until the white label clears over the photo behind it, so
 * the button reads as a solid green/red surface — adaptive over the photo (mirrors BottomBar).
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun HudActionButton(
    text: String,
    brand: Color,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    enabled: Boolean = true,
) {
    LegibleGlassPanel(
        accents = listOf(Color.White),
        scrimColor = brand,
        modifier = modifier.alpha(if (enabled) 1f else 0.4f),
        shape = RoundedCornerShape(16.dp),
        targetLc = 60.0,
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .let { m ->
                    if (enabled) m.pointerInteropFilter { event ->
                        if (event.action == MotionEvent.ACTION_UP) { onClick(); true } else event.action == MotionEvent.ACTION_DOWN
                    } else m
                },
            contentAlignment = Alignment.Center,
        ) {
            Text( // legible-exempt: inside LegibleGlassPanel (white label solved over the photo)
                text = text,
                color = Color.White,
                fontFamily = RidgelineLabelFamily,
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.18.em, // README:100 uppercase 0.18em, 700
            )
        }
    }
}
