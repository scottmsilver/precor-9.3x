package com.precor.treadmill.ui.screens.running

import android.util.Log
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
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlin.math.max

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

@Composable
fun RidgelineHud(
    viewModel: TreadmillViewModel,
    modifier: Modifier = Modifier,
) {
    val status by viewModel.status.collectAsState()
    val pgm by viewModel.derivedProgram.collectAsState()
    val sess by viewModel.derivedSession.collectAsState()

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
                    durSec = max(1.0, iv.duration.toDouble()),
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
        // Top row: map PANEL (weight 1) + controls rail. Uniform padding + an 8dp gap so the
        // map, the controls, and (below) the action bar all read as aligned glass cards.
        Row(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // --- MAP (now inside a glass panel like every other element) ---
            LegibleGlassPanel(
                accents = listOf(RidgelineTheme.accent),
                modifier = Modifier.weight(1f).fillMaxHeight(),
                shape = RoundedCornerShape(16.dp),
            ) {
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
                // Measured on-screen bounds (px, in map-canvas coords) of the two
                // overlay pills, fed to the map so grade chips never collide with them.
                var metricsRect by remember { mutableStateOf<androidx.compose.ui.geometry.Rect?>(null) }
                var nextRect by remember { mutableStateOf<androidx.compose.ui.geometry.Rect?>(null) }
                val pad = with(density) { 16.dp.toPx() }
                val canvasW = with(density) { maxWidth.toPx() }

                RidgelineMap(
                    route = route,
                    markerPos = markerDist,
                    metricsPillRect = metricsRect,
                    nextPillRect = nextRect,
                    modifier = Modifier.fillMaxSize(),
                )

                // Metrics pill, top-left
                MetricsPill(
                    vert = sess.vertDisplay,
                    dist = sess.distDisplay,
                    hr = if (status.hrmConnected && status.heartRate > 0) status.heartRate.toString() else "--",
                    // Estimated power (design sim formula): speed*16 + incline*9 + 40.
                    watts = Math.round(curSpdMph * 16.0 + status.emuIncline * 9.0 + 40.0).toString(),
                    modifier = Modifier
                        .align(Alignment.TopStart)
                        .padding(16.dp)
                        .onSizeChanged { sz ->
                            metricsRect = androidx.compose.ui.geometry.Rect(
                                left = pad, top = pad,
                                right = pad + sz.width, bottom = pad + sz.height,
                            )
                        },
                )

                // Next pill, top-right — right edge sits at (canvasWidth - pad).
                NextPill(
                    route = route,
                    markerDist = markerDist,
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(16.dp)
                        .onSizeChanged { sz ->
                            val rightEdge = canvasW - pad
                            nextRect = androidx.compose.ui.geometry.Rect(
                                left = rightEdge - sz.width, top = pad,
                                right = rightEdge, bottom = pad + sz.height,
                            )
                        },
                )

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
            } // map LegibleGlassPanel

            // --- RAIL (fixed ~298dp): the ORIGINAL speed/incline controls (no count-up). ---
            SpeedInclineControls(
                viewModel = viewModel,
                vertical = true,
                fillHeight = true,
                modifier = Modifier.width(298.dp).fillMaxHeight(),
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
            modifier = Modifier.padding(start = 8.dp, end = 8.dp, bottom = 8.dp),
        )
    }
}

/**
 * Tighten the wide cells monospace gives ',' and '.' without breaking digit columns.
 * Ported from DirectionD.jsx tightNum(): kern punctuation by -0.1em.
 */
private fun tightNum(s: String): AnnotatedString = buildAnnotatedString {
    for (ch in s) {
        if (ch == ',' || ch == '.') {
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
    hr: String,
    watts: String,
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
            MetricRow("HEART", hr, "bpm")
            MetricRow("POWER", watts, "w")
        }
    }
}

@Composable
private fun MetricRow(label: String, value: String, unit: String, big: Boolean = false) {
    val bg = LocalOverlayBackground.current
    Column(verticalArrangement = Arrangement.spacedBy(1.dp)) {
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
        Row(verticalAlignment = Alignment.Bottom) {
            // tightNum() needs an AnnotatedString, which LegibleText can't take; solve the
            // color via legibleOn over the panel's effective background instead.
            Text( // legible-exempt: solved via legibleOn over the photo
                text = tightNum(value),
                color = RidgelineTheme.fg.legibleOn(bg, targetLc = 70.0),
                fontFamily = RidgelineMonoFamily,
                fontSize = if (big) 22.sp else 17.sp,
                fontWeight = FontWeight.Medium,
                style = TextStyle(fontFeatureSettings = "tnum"),
            )
            LegibleText(
                text = " $unit",
                color = RidgelineTheme.dim,
                modifier = Modifier.padding(bottom = 2.dp),
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

@Composable
private fun NextPill(route: RidgelineRoute, markerDist: Double, modifier: Modifier = Modifier) {
    // Single-interval / constant program: show "Profile" + "constant" (grade/speed from idx 0).
    val single = route.count == 1
    // Coerced index for grade/speed lookups; unclamped for the ETA so the last interval
    // shows the finish time (startOf clamps to total), not the start of the last segment.
    val ni = (route.idxAt(markerDist) + 1).coerceAtMost(route.count - 1)
    val niRaw = route.idxAt(markerDist) + 1
    val ng = if (single) route.gradeIdx(0) else route.gradeIdx(ni)
    val ns = if (single) route.speedIdx(0) else route.speedIdx(ni)
    // Route position IS planned seconds, so the boundary position is the "at" time.
    val nextAt = route.startOf(niRaw)
    val gradeC = RidgelineTheme.gradeColor(ng)
    val speedC = RidgelineTheme.speedColor(ns)

    // Accents = the colored value glyphs (grade, speed) plus the fg numerics, so the panel
    // dims the photo enough for them to clear APCA.
    LegibleGlassPanel(
        accents = listOf(gradeC, speedC, RidgelineTheme.fg),
        modifier = modifier,
        shape = RoundedCornerShape(12.dp),
    ) {
        val bg = LocalOverlayBackground.current
        Column(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 11.dp),
            horizontalAlignment = Alignment.End,
        ) {
            Row(verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                LegibleText(
                    text = (if (single) "profile" else "next").uppercase(),
                    color = RidgelineTheme.dim,
                    style = TextStyle(
                        fontFamily = RidgelineLabelFamily,
                        fontSize = 11.sp,
                        letterSpacing = 1.6.sp,
                        fontWeight = FontWeight.SemiBold,
                    ),
                )
                if (single) {
                    LegibleText(
                        text = "constant",
                        color = RidgelineTheme.fg,
                        style = TextStyle(
                            fontFamily = RidgelineMonoFamily,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                        ),
                    )
                } else {
                    Row(verticalAlignment = Alignment.Bottom) {
                        LegibleText(
                            text = "at ",
                            color = RidgelineTheme.dim,
                            style = TextStyle(
                                fontFamily = RidgelineLabelFamily,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.SemiBold,
                            ),
                        )
                        LegibleText(
                            text = ridgelineFmtTime(nextAt),
                            color = RidgelineTheme.fg,
                            style = TextStyle(
                                fontFamily = RidgelineMonoFamily,
                                fontSize = 13.sp,
                                fontWeight = FontWeight.SemiBold,
                                fontFeatureSettings = "tnum",
                            ),
                        )
                    }
                }
            }
            Row(verticalAlignment = Alignment.Bottom, modifier = Modifier.padding(top = 8.dp)) {
                // grade colored by grade — keep the hue, solve contrast via legibleOn
                LegibleText(
                    text = "${Math.round(ng)}",
                    color = gradeC,
                    targetLc = 70.0,
                    style = TextStyle(
                        fontFamily = RidgelineMonoFamily,
                        fontSize = 30.sp,
                        fontWeight = FontWeight.SemiBold,
                        fontFeatureSettings = "tnum",
                    ),
                )
                LegibleText(
                    text = "%",
                    color = RidgelineTheme.dim,
                    modifier = Modifier.padding(start = 1.dp, bottom = 3.dp),
                    style = TextStyle(fontFamily = RidgelineLabelFamily, fontSize = 14.sp),
                )
                Box(
                    modifier = Modifier
                        .padding(horizontal = 16.dp)
                        .height(24.dp)
                        .width(1.dp)
                        .background(RidgelineTheme.line),
                )
                // speed colored by its own hardness; tightNum needs AnnotatedString → legibleOn
                Text( // legible-exempt: solved via legibleOn over the photo
                    text = tightNum("%.1f".format(ns)),
                    color = speedC.legibleOn(bg, targetLc = 70.0),
                    fontFamily = RidgelineMonoFamily,
                    fontSize = 24.sp,
                    fontWeight = FontWeight.SemiBold,
                    style = TextStyle(fontFeatureSettings = "tnum"),
                )
                LegibleText(
                    text = " mph",
                    color = RidgelineTheme.dim,
                    modifier = Modifier.padding(bottom = 2.dp),
                    style = TextStyle(fontFamily = RidgelineLabelFamily, fontSize = 13.sp),
                )
            }
        }
    }
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun StepperCard(
    value: String,
    unit: String,
    valueColor: Color,
    enabled: Boolean,
    onFine: (Boolean) -> Unit,
    onCoarse: (Boolean) -> Unit,
    fineUpDesc: String,
    fineDownDesc: String,
    coarseUpDesc: String,
    coarseDownDesc: String,
    modifier: Modifier = Modifier,
    overridden: Boolean = false,
) {
    // Glass tile: the panel dims the photo behind the card just enough for the big colored
    // value to clear APCA, so the card reads as a dimmed glass tile instead of a near-invisible
    // 3% white wash over the bright photo.
    LegibleGlassPanel(
        accents = listOf(valueColor),
        modifier = modifier,
        shape = RoundedCornerShape(18.dp),
    ) {
        val bg = LocalOverlayBackground.current
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            // Fine (single chevron) on LEFT — keep chevrons in the value hue, solved for contrast.
            val chevColor = valueColor.legibleOn(bg, targetLc = 60.0)
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                HudRepeatButton(up = true, isDouble = false, enabled = enabled, color = chevColor, contentDescription = fineUpDesc) { onFine(true) }
                HudRepeatButton(up = false, isDouble = false, enabled = enabled, color = chevColor, contentDescription = fineDownDesc) { onFine(false) }
            }
            // Center value
            Column(
                modifier = Modifier.weight(1f),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                LegibleText(
                    text = value,
                    color = valueColor,
                    targetLc = 70.0,
                    style = TextStyle(
                        fontFamily = RidgelineMonoFamily,
                        fontSize = 46.sp,
                        fontWeight = FontWeight.SemiBold,
                        textAlign = TextAlign.Center,
                        fontFeatureSettings = "tnum",
                    ),
                )
                LegibleText(
                    text = unit,
                    color = RidgelineTheme.dim,
                    style = TextStyle(fontSize = 13.sp, fontFamily = RidgelineLabelFamily),
                )
                if (overridden) {
                    LegibleText(
                        text = "MANUAL",
                        color = RidgelineTheme.accent,
                        modifier = Modifier
                            .padding(top = 4.dp)
                            .background(RidgelineTheme.accent.copy(alpha = 0.16f), RoundedCornerShape(4.dp))
                            .padding(horizontal = 6.dp, vertical = 1.dp),
                        style = TextStyle(
                            fontFamily = RidgelineLabelFamily,
                            fontSize = 9.sp,
                            fontWeight = FontWeight.SemiBold,
                            letterSpacing = 1.2.sp,
                        ),
                    )
                }
            }
            // Coarse (double chevron) on RIGHT
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                HudRepeatButton(up = true, isDouble = true, enabled = enabled, color = chevColor, contentDescription = coarseUpDesc) { onCoarse(true) }
                HudRepeatButton(up = false, isDouble = true, enabled = enabled, color = chevColor, contentDescription = coarseDownDesc) { onCoarse(false) }
            }
        }
    }
}

/** Hold-to-repeat button: 400ms initial, 150ms repeat, 75ms after 5 repeats (matches existing). */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun HudRepeatButton(
    up: Boolean,
    isDouble: Boolean,
    enabled: Boolean,
    color: Color,
    contentDescription: String,
    onStep: () -> Unit,
) {
    val context = LocalContext.current
    var pressed by remember { mutableStateOf(false) }

    LaunchedEffect(pressed) {
        if (!pressed || !enabled) return@LaunchedEffect
        onStep(); haptic(context, 15)
        delay(400)
        var count = 0
        while (pressed) {
            onStep(); haptic(context, 15)
            count++
            delay(if (count > 5) 75 else 150)
        }
    }

    Box(
        modifier = Modifier
            .size(60.dp, 56.dp)
            .semantics { this.contentDescription = contentDescription }
            .background(Color.White.copy(alpha = 0.05f), RoundedCornerShape(14.dp))
            .pointerInteropFilter { event ->
                if (!enabled) return@pointerInteropFilter false
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> { pressed = true; true }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { pressed = false; true }
                    else -> false
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Canvas(modifier = Modifier.size(26.dp, 20.dp)) {
            val w = size.width
            val h = size.height
            val sw = w * 0.10f
            val stroke = Stroke(width = sw, cap = StrokeCap.Round, join = StrokeJoin.Round)
            val inset = sw / 2 + w * 0.08f
            fun chevron(topY: Float, amp: Float): Path = Path().apply {
                if (up) {
                    moveTo(inset, topY + amp); lineTo(w / 2, topY); lineTo(w - inset, topY + amp)
                } else {
                    moveTo(inset, topY); lineTo(w / 2, topY + amp); lineTo(w - inset, topY)
                }
            }
            if (isDouble) {
                val amp = h * 0.28f
                val gap = h * 0.10f
                val totalH = amp * 2 + gap
                val t0 = (h - totalH) / 2
                drawPath(chevron(t0, amp), color, style = stroke)
                drawPath(chevron(t0 + amp + gap, amp), color, style = stroke)
            } else {
                val amp = h * 0.34f
                drawPath(chevron((h - amp) / 2, amp), color, style = stroke)
            }
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
