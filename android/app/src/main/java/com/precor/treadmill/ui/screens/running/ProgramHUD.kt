package com.precor.treadmill.ui.screens.running

import android.view.HapticFeedbackConstants
import androidx.compose.animation.*
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.foundation.Canvas
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.draw.scale
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.LayoutCoordinates
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private const val DOUBLE_TAP_MS = 300L

@Composable
fun ProgramHUD(
    viewModel: TreadmillViewModel,
    modifier: Modifier = Modifier,
) {
    val pgm by viewModel.derivedProgram.collectAsState()
    val view = LocalView.current
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    var overlayVisible by remember { mutableStateOf(false) }
    var autoHideKey by remember { mutableIntStateOf(0) }

    // Double-tap skip feedback ("left" or "right")
    var skipFeedback by remember { mutableStateOf<String?>(null) }

    // Double-tap debounce cooldown
    var lastDoubleTapTime by remember { mutableLongStateOf(0L) }

    // Double-tap detection state
    var lastTapTime by remember { mutableLongStateOf(0L) }
    var lastTapSide by remember { mutableStateOf<String?>(null) }
    var singleTapJob by remember { mutableStateOf<Job?>(null) }

    // Press feedback state
    var pressedButton by remember { mutableStateOf<String?>(null) }
    val prevScale by animateFloatAsState(
        targetValue = if (pressedButton == "prev") 0.88f else 1f,
        animationSpec = spring(dampingRatio = 0.6f, stiffness = 800f),
        label = "prevScale",
    )
    val nextScale by animateFloatAsState(
        targetValue = if (pressedButton == "next") 0.88f else 1f,
        animationSpec = spring(dampingRatio = 0.6f, stiffness = 800f),
        label = "nextScale",
    )
    var pauseBounds by remember { mutableStateOf(Rect.Zero) }
    val pauseScale by animateFloatAsState(
        targetValue = if (pressedButton == "pause") 0.88f else 1f,
        animationSpec = spring(dampingRatio = 0.6f, stiffness = 800f),
        label = "pauseScale",
    )

    // Button bounds in card coordinates for hit testing
    var prevBounds by remember { mutableStateOf(Rect.Zero) }
    var nextBounds by remember { mutableStateOf(Rect.Zero) }
    var cardCoords by remember { mutableStateOf<LayoutCoordinates?>(null) }
    var cardSize by remember { mutableStateOf(IntSize.Zero) }

    // Auto-hide overlay after 4s (unless paused); autoHideKey restarts timer on skip
    LaunchedEffect(overlayVisible, pgm.paused, autoHideKey) {
        if (overlayVisible && !pgm.paused) {
            delay(4000)
            overlayVisible = false
        }
    }

    // Clear skip feedback after 600ms
    LaunchedEffect(skipFeedback) {
        if (skipFeedback != null) {
            delay(600)
            skipFeedback = null
        }
    }

    // Compute proportional button sizing from card dimensions (convert to dp first)
    val cardHDp = with(density) { cardSize.height.toDp() }
    val cardWDp = with(density) { cardSize.width.toDp() }
    val skipSize = (cardHDp.value * 0.16f).coerceIn(48f, 72f).dp
    val skipFontSize = (cardHDp.value * 0.07f).coerceIn(18f, 30f).sp
    val btnGap = (cardWDp.value * 0.06f).coerceIn(16f, 36f).dp
    val pauseSize = (cardHDp.value * 0.25f).coerceIn(64f, 100f).dp
    val pauseFontSize = (cardHDp.value * 0.10f).coerceIn(24f, 42f).sp
    val playIconSize = pauseSize * 0.5f
    val skipIconSize = skipSize * 0.45f

    // Elevation profile card — matches web ProgramHUD layout
    Box(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp, vertical = 6.dp),
    ) {
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(
                    color = Color(0xFF1E1D1B),
                    shape = RoundedCornerShape(16.dp),
                )
                .border(
                    width = 1.dp,
                    color = Color.White.copy(alpha = 0.25f),
                    shape = RoundedCornerShape(16.dp),
                )
                .onGloballyPositioned { cardCoords = it }
                .onSizeChanged { cardSize = it }
                .pointerInput(pgm.running, pgm.intervalCount) {
                    if (!pgm.running) return@pointerInput
                    detectTapGestures { offset ->
                        if (overlayVisible) {
                            when {
                                pauseBounds != Rect.Zero && pauseBounds.contains(offset) -> {
                                    view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                                    pressedButton = "pause"
                                    scope.launch {
                                        delay(120)
                                        viewModel.pauseProgram()
                                        pressedButton = null
                                        // If was playing -> now paused -> overlay stays (auto-hide LaunchedEffect handles it)
                                        // If was paused -> now playing -> auto-hide will kick in
                                    }
                                }
                                prevBounds != Rect.Zero && prevBounds.contains(offset) -> {
                                    view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                                    pressedButton = "prev"
                                    val target = pgm.currentInterval
                                    scope.launch {
                                        delay(120)
                                        viewModel.prevInterval()
                                        autoHideKey++ // restart auto-hide, keep overlay
                                        pressedButton = null
                                        if (target > 0) {
                                            viewModel.showMessage("Back to $target of ${pgm.intervalCount}", 1500)
                                        }
                                    }
                                }
                                nextBounds != Rect.Zero && nextBounds.contains(offset) -> {
                                    view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                                    pressedButton = "next"
                                    val target = pgm.currentInterval + 2
                                    scope.launch {
                                        delay(120)
                                        viewModel.skipInterval()
                                        autoHideKey++ // restart auto-hide, keep overlay
                                        pressedButton = null
                                        if (target <= pgm.intervalCount) {
                                            viewModel.showMessage("Skipping to $target of ${pgm.intervalCount}", 1500)
                                        }
                                    }
                                }
                                else -> {
                                    overlayVisible = false
                                    view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                                }
                            }
                        } else {
                            val relX = offset.x / size.width
                            val side = if (relX > 0.5f) "right" else "left"
                            val now = System.currentTimeMillis()

                            if (now - lastTapTime < DOUBLE_TAP_MS && lastTapSide == side) {
                                singleTapJob?.cancel()
                                singleTapJob = null
                                lastTapTime = 0
                                lastTapSide = null
                                // Debounce: ignore double-taps within 500ms of the last one
                                if (now - lastDoubleTapTime < 500) return@detectTapGestures
                                lastDoubleTapTime = now
                                view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                                skipFeedback = side
                                if (side == "right") {
                                    val target = pgm.currentInterval + 2
                                    viewModel.skipInterval()
                                    if (target <= pgm.intervalCount) {
                                        viewModel.showMessage("Skipping to $target of ${pgm.intervalCount}", 1500)
                                    }
                                } else {
                                    val target = pgm.currentInterval
                                    viewModel.prevInterval()
                                    if (target > 0) {
                                        viewModel.showMessage("Back to $target of ${pgm.intervalCount}", 1500)
                                    }
                                }
                            } else {
                                lastTapTime = now
                                lastTapSide = side
                                singleTapJob?.cancel()
                                singleTapJob = scope.launch {
                                    delay(DOUBLE_TAP_MS)
                                    lastTapTime = 0
                                    lastTapSide = null
                                    overlayVisible = true
                                    view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
                                }
                            }
                        }
                    }
                },
        ) {
            // Elevation profile fills the card
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(start = 4.dp, end = 4.dp, top = 6.dp, bottom = 2.dp),
            ) {
                ElevationProfile(
                    viewModel = viewModel,
                )
            }

            // Position counter overlay (top-right)
            if (pgm.intervalCount > 1) {
                Box(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(top = 8.dp, end = 10.dp)
                        .background(
                            color = Color(0xFF1E1D1B).copy(alpha = 0.6f),
                            shape = RoundedCornerShape(4.dp),
                        )
                        .padding(horizontal = 8.dp, vertical = 2.dp),
                ) {
                    Text(
                        text = "${pgm.currentInterval + 1} of ${pgm.intervalCount}",
                        color = Color(0x59E8E4DF),
                        fontSize = 11.sp,
                    )
                }
            }

            // Double-tap skip feedback — left
            AnimatedVisibility(
                visible = skipFeedback == "left",
                modifier = Modifier
                    .align(Alignment.CenterStart)
                    .padding(start = 32.dp),
                enter = fadeIn() + scaleIn(initialScale = 0.6f),
                exit = fadeOut() + scaleOut(targetScale = 0.6f),
            ) {
                Box(
                    modifier = Modifier
                        .size(48.dp)
                        .clip(CircleShape)
                        .background(Color.Black.copy(alpha = 0.6f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Canvas(modifier = Modifier.size(24.dp)) {
                        val c = Color.White.copy(alpha = 0.85f)
                        drawRect(c, Offset(size.width * 0.08f, size.height * 0.15f), Size(size.width * 0.12f, size.height * 0.7f))
                        drawPath(Path().apply {
                            moveTo(size.width * 0.9f, size.height * 0.15f)
                            lineTo(size.width * 0.28f, size.height * 0.5f)
                            lineTo(size.width * 0.9f, size.height * 0.85f)
                            close()
                        }, c)
                    }
                }
            }

            // Double-tap skip feedback — right
            AnimatedVisibility(
                visible = skipFeedback == "right",
                modifier = Modifier
                    .align(Alignment.CenterEnd)
                    .padding(end = 32.dp),
                enter = fadeIn() + scaleIn(initialScale = 0.6f),
                exit = fadeOut() + scaleOut(targetScale = 0.6f),
            ) {
                Box(
                    modifier = Modifier
                        .size(48.dp)
                        .clip(CircleShape)
                        .background(Color.Black.copy(alpha = 0.6f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Canvas(modifier = Modifier.size(24.dp)) {
                        val c = Color.White.copy(alpha = 0.85f)
                        drawPath(Path().apply {
                            moveTo(size.width * 0.1f, size.height * 0.15f)
                            lineTo(size.width * 0.72f, size.height * 0.5f)
                            lineTo(size.width * 0.1f, size.height * 0.85f)
                            close()
                        }, c)
                        drawRect(c, Offset(size.width * 0.80f, size.height * 0.15f), Size(size.width * 0.12f, size.height * 0.7f))
                    }
                }
            }

            // Dark backdrop + YouTube-style circular controls
            AnimatedVisibility(
                visible = overlayVisible,
                modifier = Modifier.matchParentSize(),
                enter = fadeIn(),
                exit = fadeOut(),
            ) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(
                            color = Color.Black.copy(alpha = 0.4f),
                            shape = RoundedCornerShape(16.dp),
                        ),
                    contentAlignment = Alignment.Center,
                ) {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(btnGap),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        val iconColor = Color.White.copy(alpha = 0.9f)

                        // Skip Previous — glass circle
                        if (pgm.intervalCount > 1) {
                            Box(
                                modifier = Modifier
                                    .scale(prevScale)
                                    .shadow(8.dp, CircleShape)
                                    .size(skipSize)
                                    .clip(CircleShape)
                                    .border(1.dp, Color.White.copy(alpha = 0.18f), CircleShape)
                                    .background(
                                        if (pressedButton == "prev") Color.White.copy(alpha = 0.16f)
                                        else Color.White.copy(alpha = 0.10f),
                                    )
                                    .onGloballyPositioned { coords ->
                                        val card = cardCoords ?: return@onGloballyPositioned
                                        val topLeft = card.localPositionOf(coords, Offset.Zero)
                                        prevBounds = Rect(topLeft, Size(coords.size.width.toFloat(), coords.size.height.toFloat()))
                                    },
                                contentAlignment = Alignment.Center,
                            ) {
                                Canvas(modifier = Modifier.size(skipIconSize)) {
                                    // Vertical bar
                                    drawRect(iconColor, Offset(size.width * 0.08f, size.height * 0.15f), Size(size.width * 0.12f, size.height * 0.7f))
                                    // Left triangle
                                    drawPath(Path().apply {
                                        moveTo(size.width * 0.9f, size.height * 0.15f)
                                        lineTo(size.width * 0.28f, size.height * 0.5f)
                                        lineTo(size.width * 0.9f, size.height * 0.85f)
                                        close()
                                    }, iconColor)
                                }
                            }
                        }

                        // Play/Pause — glass circle, large center
                        Box(
                            modifier = Modifier
                                .scale(pauseScale)
                                .shadow(12.dp, CircleShape)
                                .size(pauseSize)
                                .clip(CircleShape)
                                .border(1.dp, Color.White.copy(alpha = 0.22f), CircleShape)
                                .background(
                                    if (pressedButton == "pause") Color.White.copy(alpha = 0.18f)
                                    else Color.White.copy(alpha = 0.10f),
                                )
                                .onGloballyPositioned { coords ->
                                    val card = cardCoords ?: return@onGloballyPositioned
                                    val topLeft = card.localPositionOf(coords, Offset.Zero)
                                    pauseBounds = Rect(topLeft, Size(coords.size.width.toFloat(), coords.size.height.toFloat()))
                                },
                            contentAlignment = Alignment.Center,
                        ) {
                            Canvas(modifier = Modifier.size(playIconSize)) {
                                if (pgm.paused) {
                                    // Play triangle — offset slightly right for optical centering
                                    drawPath(Path().apply {
                                        moveTo(size.width * 0.18f, size.height * 0.08f)
                                        lineTo(size.width * 0.92f, size.height * 0.5f)
                                        lineTo(size.width * 0.18f, size.height * 0.92f)
                                        close()
                                    }, iconColor)
                                } else {
                                    // Pause bars
                                    val barW = size.width * 0.26f
                                    val gap = size.width * 0.16f
                                    val x1 = (size.width - 2 * barW - gap) / 2
                                    drawRoundRect(iconColor, Offset(x1, size.height * 0.12f), Size(barW, size.height * 0.76f), CornerRadius(4f))
                                    drawRoundRect(iconColor, Offset(x1 + barW + gap, size.height * 0.12f), Size(barW, size.height * 0.76f), CornerRadius(4f))
                                }
                            }
                        }

                        // Skip Next — glass circle
                        if (pgm.intervalCount > 1) {
                            Box(
                                modifier = Modifier
                                    .scale(nextScale)
                                    .shadow(8.dp, CircleShape)
                                    .size(skipSize)
                                    .clip(CircleShape)
                                    .border(1.dp, Color.White.copy(alpha = 0.18f), CircleShape)
                                    .background(
                                        if (pressedButton == "next") Color.White.copy(alpha = 0.16f)
                                        else Color.White.copy(alpha = 0.10f),
                                    )
                                    .onGloballyPositioned { coords ->
                                        val card = cardCoords ?: return@onGloballyPositioned
                                        val topLeft = card.localPositionOf(coords, Offset.Zero)
                                        nextBounds = Rect(topLeft, Size(coords.size.width.toFloat(), coords.size.height.toFloat()))
                                    },
                                contentAlignment = Alignment.Center,
                            ) {
                                Canvas(modifier = Modifier.size(skipIconSize)) {
                                    // Right triangle
                                    drawPath(Path().apply {
                                        moveTo(size.width * 0.1f, size.height * 0.15f)
                                        lineTo(size.width * 0.72f, size.height * 0.5f)
                                        lineTo(size.width * 0.1f, size.height * 0.85f)
                                        close()
                                    }, iconColor)
                                    // Vertical bar
                                    drawRect(iconColor, Offset(size.width * 0.80f, size.height * 0.15f), Size(size.width * 0.12f, size.height * 0.7f))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
