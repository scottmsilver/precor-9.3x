package com.precor.treadmill.ui.screens.running

import android.view.MotionEvent
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.ExperimentalComposeUiApi
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.input.pointer.pointerInteropFilter
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LegibleGlassPanel
import com.precor.treadmill.ui.theme.LegibleText
import com.precor.treadmill.ui.theme.LocalGlassParams
import com.precor.treadmill.ui.theme.LocalOverlayBackground
import com.precor.treadmill.ui.theme.legibleOn
import com.precor.treadmill.ui.theme.touchFingertip
import com.precor.treadmill.ui.theme.touchFingerPad
import com.precor.treadmill.ui.util.haptic
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlinx.coroutines.delay

@OptIn(ExperimentalComposeUiApi::class)
@Composable
fun SpeedInclineControls(
    viewModel: TreadmillViewModel,
    vertical: Boolean = false,
    fillHeight: Boolean = false,
    modifier: Modifier = Modifier,
) {
    val status by viewModel.status.collectAsState()
    val context = LocalContext.current

    val speedAdjust = { delta: Int ->
        viewModel.adjustSpeed(delta)
        haptic(context, 15)
    }
    val inclineAdjust = { delta: Double ->
        viewModel.adjustIncline(delta)
        haptic(context, 15)
    }

    if (vertical) {
        Column(
            modifier = modifier
                .alpha(if (status.treadmillConnected) 1f else 0.3f),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            val panelModifier = if (fillHeight) Modifier.weight(1f).fillMaxWidth() else Modifier.fillMaxWidth()
            if (fillHeight) {
                // HUD rail: "chevron field" steppers — the whole card is button surface
                // (big fine halves + full-height coarse rail), value text overlaid.
                ChevronFieldPanel(
                    value = (status.emuSpeed / 10.0).let { "%.1f".format(it) },
                    label = "mph",
                    accentColor = Color(0xFF6BC89B),
                    smallDelta = 1.0, largeDelta = 10.0,
                    enabled = status.treadmillConnected,
                    onAdjust = { speedAdjust(it.toInt()) },
                    modifier = panelModifier,
                )
                ChevronFieldPanel(
                    value = formatIncline(status.emuIncline),
                    label = "% incline",
                    accentColor = Color(0xFFA69882),
                    smallDelta = 0.5, largeDelta = 1.0,
                    enabled = status.treadmillConnected,
                    onAdjust = inclineAdjust,
                    modifier = panelModifier,
                )
            } else {
                ControlPanel(
                    value = (status.emuSpeed / 10.0).let { "%.1f".format(it) },
                    label = "mph",
                    accentColor = Color(0xFF6BC89B),
                    smallDelta = 1.0, largeDelta = 10.0,
                    enabled = status.treadmillConnected,
                    onAdjust = { speedAdjust(it.toInt()) },
                    modifier = panelModifier,
                )
                ControlPanel(
                    value = formatIncline(status.emuIncline),
                    label = "% incline",
                    accentColor = Color(0xFFA69882),
                    smallDelta = 0.5, largeDelta = 1.0,
                    enabled = status.treadmillConnected,
                    onAdjust = inclineAdjust,
                    modifier = panelModifier,
                )
            }
        }
    } else {
        Row(
            modifier = modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp)
                .alpha(if (status.treadmillConnected) 1f else 0.3f),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            ControlPanel(
                value = (status.emuSpeed / 10.0).let { "%.1f".format(it) },
                label = "mph",
                accentColor = Color(0xFF6BC89B),
                smallDelta = 1.0, largeDelta = 10.0,
                enabled = status.treadmillConnected,
                onAdjust = { speedAdjust(it.toInt()) },
                modifier = Modifier.weight(1f),
            )
            ControlPanel(
                value = formatIncline(status.emuIncline),
                label = "% incline",
                accentColor = Color(0xFFA69882),
                smallDelta = 0.5, largeDelta = 1.0,
                enabled = status.treadmillConnected,
                onAdjust = inclineAdjust,
                modifier = Modifier.weight(1f),
            )
        }
    }
}

private fun formatIncline(value: Double): String {
    return "%.1f".format(value)
}

@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun ControlPanel(
    value: String,
    label: String,
    accentColor: Color,
    smallDelta: Double,
    largeDelta: Double,
    enabled: Boolean,
    onAdjust: (Double) -> Unit,
    modifier: Modifier = Modifier,
    fillHeight: Boolean = false,
) {
    val metricName = if (label.contains("incline", ignoreCase = true)) "incline" else "speed"
    val smallAmount = if (metricName == "speed") "%.1f mph".format(smallDelta / 10.0) else "%.1f%%".format(smallDelta)
    val largeAmount = if (metricName == "speed") "%.1f mph".format(largeDelta / 10.0) else "%.1f%%".format(largeDelta)

    val btnW = touchFingertip()
    val btnH = touchFingerPad()

    LegibleGlassPanel(
        accents = listOf(accentColor),
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
    ) {
    Row(
        modifier = Modifier.padding(5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(1.dp),
    ) {
        // Small buttons column
        Column(
            modifier = if (fillHeight) Modifier.fillMaxHeight() else Modifier,
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            RepeatButton(
                delta = smallDelta,
                enabled = enabled,
                onAdjust = onAdjust,
                isUp = true,
                color = accentColor,
                description = "Increase $metricName by $smallAmount",
                modifier = if (fillHeight) Modifier.weight(1f).width(btnW) else Modifier.size(btnW, btnH),
            )
            RepeatButton(
                delta = -smallDelta,
                enabled = enabled,
                onAdjust = onAdjust,
                isUp = false,
                color = accentColor,
                description = "Decrease $metricName by $smallAmount",
                modifier = if (fillHeight) Modifier.weight(1f).width(btnW) else Modifier.size(btnW, btnH),
            )
        }

        // Value display
        val valueFontSize = if (fillHeight) 40.sp else (btnH.value * 0.42f).sp
        val labelFontSize = if (fillHeight) 14.sp else (btnH.value * 0.16f).sp
        Column(
            modifier = Modifier
                .weight(1f)
                .then(if (fillHeight) Modifier.fillMaxHeight() else Modifier)
                .padding(vertical = 10.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            LegibleText(
                text = value,
                color = accentColor,
                targetLc = 70.0,
                style = TextStyle(
                    fontSize = valueFontSize,
                    fontWeight = FontWeight.SemiBold,
                    textAlign = TextAlign.Center,
                    lineHeight = (valueFontSize.value + 2).sp,
                ),
            )
            LegibleText(
                text = label,
                color = LocalGlassParams.current.textColor,
                style = TextStyle(fontSize = labelFontSize),
            )
        }

        // Large buttons column
        Column(
            modifier = if (fillHeight) Modifier.fillMaxHeight() else Modifier,
            verticalArrangement = Arrangement.spacedBy(3.dp),
        ) {
            RepeatButton(
                delta = largeDelta,
                enabled = enabled,
                onAdjust = onAdjust,
                isUp = true,
                color = accentColor,
                isDouble = true,
                description = "Increase $metricName by $largeAmount",
                modifier = if (fillHeight) Modifier.weight(1f).width(btnW) else Modifier.size(btnW, btnH),
            )
            RepeatButton(
                delta = -largeDelta,
                enabled = enabled,
                onAdjust = onAdjust,
                isUp = false,
                color = accentColor,
                isDouble = true,
                description = "Decrease $metricName by $largeAmount",
                modifier = if (fillHeight) Modifier.weight(1f).width(btnW) else Modifier.size(btnW, btnH),
            )
        }
    }
    }
}

/**
 * Button with hold-to-repeat: 400ms initial delay, 150ms repeat, 75ms after 5 repeats.
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun RepeatButton(
    delta: Double,
    enabled: Boolean,
    onAdjust: (Double) -> Unit,
    isUp: Boolean,
    color: Color,
    isDouble: Boolean = false,
    description: String = "",
    modifier: Modifier = Modifier,
) {
    var pressed by remember { mutableStateOf(false) }

    // Hold-to-repeat coroutine
    LaunchedEffect(pressed) {
        if (!pressed || !enabled) return@LaunchedEffect
        onAdjust(delta)
        delay(400) // initial delay
        var count = 0
        while (pressed) {
            onAdjust(delta)
            count++
            delay(if (count > 5) 75 else 150)
        }
    }

    Box(
        modifier = modifier
            .semantics { contentDescription = description }
            .background(
                color = Color(0x3D787880),
                shape = RoundedCornerShape(10.dp),
            )
            .pointerInteropFilter { event ->
                if (!enabled) return@pointerInteropFilter false
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> {
                        pressed = true
                        true
                    }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                        pressed = false
                        true
                    }
                    else -> false
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        // Chevron icon — scales proportionally with button size.
        // The stroke color is a widget on the photo too, so run it through the same
        // APCA guard as text before drawing it.
        val chevronColor = color.legibleOn(LocalOverlayBackground.current, targetLc = 70.0)
        Canvas(
            modifier = Modifier.fillMaxHeight(0.4f).aspectRatio(1f),
        ) {
            val w = size.width
            val h = size.height
            val sw = w * 0.11f
            val stroke = Stroke(
                width = sw,
                cap = StrokeCap.Round,
                join = StrokeJoin.Round,
            )
            val inset = sw / 2 + w * 0.05f

            if (isDouble) {
                val chevAmp = h * 0.18f
                val gap = h * 0.06f
                val totalH = chevAmp * 2 + gap
                val topY = (h - totalH) / 2

                if (isUp) {
                    val path1 = Path().apply {
                        moveTo(inset, topY + chevAmp)
                        lineTo(w / 2, topY)
                        lineTo(w - inset, topY + chevAmp)
                    }
                    drawPath(path1, chevronColor, style = stroke)
                    val path2 = Path().apply {
                        moveTo(inset, topY + chevAmp + gap + chevAmp)
                        lineTo(w / 2, topY + chevAmp + gap)
                        lineTo(w - inset, topY + chevAmp + gap + chevAmp)
                    }
                    drawPath(path2, chevronColor, style = stroke)
                } else {
                    val path1 = Path().apply {
                        moveTo(inset, topY)
                        lineTo(w / 2, topY + chevAmp)
                        lineTo(w - inset, topY)
                    }
                    drawPath(path1, chevronColor, style = stroke)
                    val path2 = Path().apply {
                        moveTo(inset, topY + chevAmp + gap)
                        lineTo(w / 2, topY + chevAmp + gap + chevAmp)
                        lineTo(w - inset, topY + chevAmp + gap)
                    }
                    drawPath(path2, chevronColor, style = stroke)
                }
            } else {
                val chevAmp = h * 0.22f
                val topY = (h - chevAmp) / 2
                val path = Path().apply {
                    if (isUp) {
                        moveTo(inset, topY + chevAmp)
                        lineTo(w / 2, topY)
                        lineTo(w - inset, topY + chevAmp)
                    } else {
                        moveTo(inset, topY)
                        lineTo(w / 2, topY + chevAmp)
                        lineTo(w - inset, topY)
                    }
                }
                drawPath(path, chevronColor, style = stroke)
            }
        }
    }
}

/**
 * "Chevron field" stepper for the HUD rail: the entire card is button surface.
 * Layout — big FINE halves on the left ~74% (tap top = up, bottom = down; each half
 * IS a giant ghosted chevron), and a full-height COARSE rail on the right ~26%
 * (double chevrons). The value text floats over the fine zone and ignores touches.
 * Press states light the pressed glyph and flood the zone with a faint accent tint.
 * Same hold-to-repeat physics as [RepeatButton] (400ms, then 150ms, 75ms after 5).
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun ChevronFieldPanel(
    value: String,
    label: String,
    accentColor: Color,
    smallDelta: Double,
    largeDelta: Double,
    enabled: Boolean,
    onAdjust: (Double) -> Unit,
    modifier: Modifier = Modifier,
) {
    val metricName = if (label.contains("incline", ignoreCase = true)) "incline" else "speed"
    val smallAmount = if (metricName == "speed") "%.1f mph".format(smallDelta / 10.0) else "%.1f%%".format(smallDelta)
    val largeAmount = if (metricName == "speed") "%.1f mph".format(largeDelta / 10.0) else "%.1f%%".format(largeDelta)

    LegibleGlassPanel(
        accents = listOf(accentColor),
        modifier = modifier,
        shape = RoundedCornerShape(16.dp),
    ) {
        // Rest-state glyphs are NEUTRAL ivory on both cards (one material across the
        // rail); the metric's accent appears only on press (lit glyph + flood).
        // Both run through the APCA guard — they're widgets on the photo.
        val bg = LocalOverlayBackground.current
        val restColor = Color(0xFFEEF4F1).legibleOn(bg, targetLc = 60.0)
        val pressColor = accentColor.legibleOn(bg, targetLc = 70.0)
        Box(modifier = Modifier.fillMaxSize()) {
            Row(modifier = Modifier.fillMaxSize()) {
                // --- fine halves (the big targets) ---
                Column(modifier = Modifier.weight(0.74f).fillMaxHeight()) {
                    FieldZone(
                        delta = smallDelta, enabled = enabled, onAdjust = onAdjust,
                        isUp = true, isDouble = false, restColor = restColor, pressColor = pressColor,
                        description = "Increase $metricName by $smallAmount",
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                    )
                    FieldZone(
                        delta = -smallDelta, enabled = enabled, onAdjust = onAdjust,
                        isUp = false, isDouble = false, restColor = restColor, pressColor = pressColor,
                        description = "Decrease $metricName by $smallAmount",
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                    )
                }
                // seam between fine and coarse
                Box(
                    modifier = Modifier
                        .fillMaxHeight()
                        .width(1.dp)
                        .padding(vertical = 12.dp)
                        .background(Color.White.copy(alpha = 0.085f)),
                )
                // --- coarse rail (explicit double-chevron buttons, full height) ---
                Column(modifier = Modifier.weight(0.26f).fillMaxHeight()) {
                    FieldZone(
                        delta = largeDelta, enabled = enabled, onAdjust = onAdjust,
                        isUp = true, isDouble = true, restColor = restColor, pressColor = pressColor,
                        description = "Increase $metricName by $largeAmount",
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                    )
                    FieldZone(
                        delta = -largeDelta, enabled = enabled, onAdjust = onAdjust,
                        isUp = false, isDouble = true, restColor = restColor, pressColor = pressColor,
                        description = "Decrease $metricName by $largeAmount",
                        modifier = Modifier.weight(1f).fillMaxWidth(),
                    )
                }
            }
            // --- value overlay: floats over the FINE zone only; no pointer modifiers,
            // so touches pass straight through to the zones underneath. Offset -10dp:
            // the 56sp line box + hanging unit label made the centered ink block sag
            // ~8px below the card midline (design review); this rebalances the gaps.
            Column(
                modifier = Modifier.fillMaxHeight().fillMaxWidth(0.74f).offset(y = (-10).dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                LegibleText(
                    text = value,
                    color = accentColor,
                    targetLc = 70.0,
                    style = TextStyle(
                        // Display face, not the mono — the mono's full-cell decimal reads
                        // as "0 . 0" at this size (same reasoning as the map chips).
                        fontFamily = RidgelineLabelFamily,
                        fontSize = 56.sp,
                        fontWeight = FontWeight.SemiBold,
                        textAlign = TextAlign.Center,
                        fontFeatureSettings = "tnum",
                        shadow = androidx.compose.ui.graphics.Shadow(
                            color = Color.Black.copy(alpha = 0.55f),
                            blurRadius = 18f,
                        ),
                    ),
                )
                LegibleText(
                    text = label,
                    color = LocalGlassParams.current.textColor,
                    style = TextStyle(fontSize = 13.sp, fontFamily = RidgelineLabelFamily),
                )
            }
        }
    }
}

/**
 * One pressable zone of the chevron field. The glyph is the affordance: neutral ivory
 * ghost at rest (fine 24% / coarse 32% — tuned for photo-glass, not black glass), lit
 * in the metric's accent at 80% while held, with a faint accent flood on the zone.
 * Geometry per design review: one stroke family (12.5% of glyph height), ~100° apexes,
 * butt caps on the big glyph (round caps left 6dp blobs), fine glyph sized by HEIGHT
 * (40% of the half) so it never crowds the value. Hold-to-repeat matches [RepeatButton].
 */
@OptIn(ExperimentalComposeUiApi::class)
@Composable
private fun FieldZone(
    delta: Double,
    enabled: Boolean,
    onAdjust: (Double) -> Unit,
    isUp: Boolean,
    isDouble: Boolean,
    restColor: Color,
    pressColor: Color,
    description: String,
    modifier: Modifier = Modifier,
) {
    var pressed by remember { mutableStateOf(false) }

    LaunchedEffect(pressed) {
        if (!pressed || !enabled) return@LaunchedEffect
        onAdjust(delta)
        delay(400)
        var count = 0
        while (pressed) {
            onAdjust(delta)
            count++
            delay(if (count > 5) 75 else 150)
        }
    }

    val glyphAlpha by animateFloatAsState(
        targetValue = if (pressed) 0.80f else if (isDouble) 0.32f else 0.24f,
        animationSpec = tween(120),
        label = "chev-glyph",
    )
    val glyphColor by animateColorAsState(
        targetValue = if (pressed) pressColor else restColor,
        animationSpec = tween(120),
        label = "chev-color",
    )
    val floodAlpha by animateFloatAsState(
        targetValue = if (pressed) 0.10f else 0f,
        animationSpec = tween(120),
        label = "chev-flood",
    )

    Box(
        modifier = modifier
            .semantics { contentDescription = description }
            .background(if (isDouble) Color.White.copy(alpha = 0.03f) else Color.Transparent)
            .background(pressColor.copy(alpha = floodAlpha))
            .pointerInteropFilter { event ->
                if (!enabled) return@pointerInteropFilter false
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> { pressed = true; true }
                    MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { pressed = false; true }
                    else -> false
                }
            },
        // ONE alignment system (design review): every glyph anchors to the card's
        // top/bottom edge — the coarse pair's padding is chosen so its ink center
        // registers on the fine glyph's ink center (a shared "up row" / "down row"
        // datum across the seam), instead of centering in its half 39px off-row.
        contentAlignment = when {
            isUp -> Alignment.TopCenter
            else -> Alignment.BottomCenter
        },
    ) {
        if (isDouble) {
            // coarse: 32dp box ≈ 0.6× the fine glyph (φ-ish subordinate, stroke family
            // 12.5% of height, gap/amp = 0.4). Padding 30dp puts its ink center at
            // ~46dp from the card edge — same row as the fine glyph's center.
            Canvas(
                modifier = Modifier
                    .padding(top = if (isUp) 30.dp else 0.dp, bottom = if (isUp) 0.dp else 30.dp)
                    .size(32.dp, 32.dp),
            ) {
                val w = size.width; val h = size.height
                val sw = h * 0.125f
                val stroke = Stroke(width = sw, cap = StrokeCap.Round, join = StrokeJoin.Round)
                val inset = sw / 2
                val amp = h * 0.36f; val gap = h * 0.14f
                val topY = (h - (amp * 2 + gap)) / 2
                fun chev(y0: Float) = Path().apply {
                    if (isUp) { moveTo(inset, y0 + amp); lineTo(w / 2, y0); lineTo(w - inset, y0 + amp) }
                    else { moveTo(inset, y0); lineTo(w / 2, y0 + amp); lineTo(w - inset, y0) }
                }
                drawPath(chev(topY), glyphColor, alpha = glyphAlpha, style = stroke)
                drawPath(chev(topY + amp + gap), glyphColor, alpha = glyphAlpha, style = stroke)
            }
        } else {
            // fine: glyph sized by HEIGHT (44% of the half) so wide cards don't blow it
            // up past the value; ~100° apex (rise/run ≈ 0.84); butt caps kill the dots.
            // Edge padding 12dp backs the open side off the value (optical nudge).
            Canvas(
                modifier = Modifier
                    .padding(top = if (isUp) 12.dp else 0.dp, bottom = if (isUp) 0.dp else 12.dp)
                    .fillMaxHeight(0.44f)
                    .aspectRatio(150f / 72f, matchHeightConstraintsFirst = true),
            ) {
                val w = size.width; val h = size.height
                val sw = h * 0.125f
                val stroke = Stroke(width = sw, cap = StrokeCap.Butt, join = StrokeJoin.Round)
                val rise = h * 0.72f
                val inset = (w / 2f - rise / 0.84f).coerceAtLeast(sw / 2f)
                val path = Path().apply {
                    if (isUp) { moveTo(inset, h * 0.86f); lineTo(w / 2, h * 0.14f); lineTo(w - inset, h * 0.86f) }
                    else { moveTo(inset, h * 0.14f); lineTo(w / 2, h * 0.86f); lineTo(w - inset, h * 0.14f) }
                }
                drawPath(path, glyphColor, alpha = glyphAlpha, style = stroke)
            }
        }
    }
}
