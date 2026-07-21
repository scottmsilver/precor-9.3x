package com.precor.treadmill.ui.screens.running

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.asAndroidPath
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LocalOverlayBackground
import com.precor.treadmill.ui.theme.legibleOn
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.sin

// --- Route model (finite, distance domain in miles) -------------------------
// Ported from makeRoute() in DirectionD.jsx (the finite/intervals branch).

/** One planned interval, in the route's domain. */
data class RouteInterval(val grade: Double, val speed: Double, val len: Double)

/** Finite climbing route assembled from planned intervals. */
class RidgelineRoute(intervals: List<RouteInterval>) {
    private val iv: List<RouteInterval> = if (intervals.isEmpty())
        listOf(RouteInterval(2.0, 4.0, 0.5)) else intervals
    private val cum: DoubleArray = DoubleArray(iv.size + 1).also {
        for (i in iv.indices) it[i + 1] = it[i] + iv[i].len
    }
    val count: Int = iv.size
    val total: Double = cum[count]

    fun idxAt(d: Double): Int {
        var i = 0
        while (i < count - 1 && cum[i + 1] <= d) i++
        return i
    }

    fun gradeIdx(i: Int): Double = iv[min(i, count - 1)].grade
    fun speedIdx(i: Int): Double = iv[min(i, count - 1)].speed
    fun startOf(i: Int): Double = cum[min(i, count)]
    fun endOf(i: Int): Double = cum[min(i + 1, count)]

    fun gradeAt(d: Double): Double = gradeIdx(idxAt(d))

    /** Vertical feet climbed to reach distance d. */
    fun vertAt(d: Double): Double {
        var v = 0.0; var i = 0; var start = 0.0; var guard = 0
        while (start < d - 1e-9 && guard++ < 6000) {
            val end = min(endOf(i), d)
            v += (end - start) * gradeIdx(i) / 100.0 * 5280.0
            start = end; i++
            if (i >= count) break
        }
        return v
    }

    /** Planned elapsed seconds to reach distance d (uses planned speeds). */
    fun timeAtDist(d: Double): Double {
        var t = 0.0; var i = 0; var start = 0.0; var guard = 0
        while (start < d - 1e-9 && guard++ < 6000) {
            val end = min(endOf(i), d)
            val spd = max(0.1, speedIdx(i))
            t += (end - start) / spd * 3600.0
            start = end; i++
            if (i >= count) break
        }
        return t
    }

    // switchback turn-phase: turns accumulate per foot climbed, denser where steep.
    fun phaseAt(d: Double): Double {
        var i = 0; var dStart = 0.0; var ph = 0.0; var guard = 0
        while (dStart < d - 1e-9 && guard++ < 8000) {
            val g = gradeIdx(i)
            val dEnd = endOf(i)
            val end = min(dEnd, d)
            val vGain = (end - dStart) * g / 100.0 * 5280.0
            val noise = 0.82 + 0.36 * sin(i * 5.13 + 1.7)
            // Distance-driven base turn term so FLAT real-program routes still meander
            // into multiple switchbacks (design only ever fed steep synthetic routes,
            // where the grade-gated vGain term alone sufficed). BASE_TURNS_PER_MILE is
            // tuned so a flat 1-mi segment yields ~2-3 half-cycles of sin(phase).
            ph += SW_A * ((0.5 + g / 8.0) * noise * vGain + BASE_TURNS_PER_MILE * (end - dStart))
            dStart = end; i++
            if (i >= count) break
        }
        return ph
    }

    /** Invert vertAt: distance (miles) at a given elevation (feet climbed). */
    fun distAtElev(e: Double): Double {
        var i = 0; var dStart = 0.0; var v = 0.0; var guard = 0
        while (guard++ < 8000) {
            val g = gradeIdx(i)
            val dEnd = endOf(i)
            val segLen = dEnd - dStart
            val segV = segLen * g / 100.0 * 5280.0
            if (v + segV >= e || i >= count - 1) {
                val frac = if (segV > 1e-9) (e - v) / segV else 0.0
                return dStart + max(0.0, min(segLen, frac * segLen))
            }
            v += segV; dStart = dEnd; i++
        }
        return dStart
    }

    companion object {
        const val SW_A = 0.030
        // Half-cycles per mile contributed independent of grade (keeps flat routes wavy).
        // 0.030 * 250 * 1mi ≈ 7.5 rad ≈ 2.4 half-cycles for a flat 1-mi segment.
        const val BASE_TURNS_PER_MILE = 250.0
    }
}

// Feet of climb shown at once. Lowered from the design's 540 so that typical (shorter)
// treadmill programs exceed the window and therefore WINDOW: the camera pans as you climb
// and the minimap's viewport box + leader lines activate, instead of staying dormant on
// routes whose whole climb fits on screen.
private const val ELEV_WINDOW = 220.0

private fun lerp(a: Double, b: Double, t: Double) = a + (b - a) * t

/**
 * The Ridgeline route map: elevation-vs-switchback plot. Vertical axis = feet climbed,
 * horizontal = centerX + amp*sin(phase). Travelled portion dim, ahead colored by grade.
 * Contours as faint polylines, pulsing accent marker at current position, grade chips at
 * interval boundaries, vertical minimap strip on the right.
 *
 * @param markerDistMiles current distance along the route (drives the marker position).
 */
@Composable
fun RidgelineMap(
    route: RidgelineRoute,
    markerDistMiles: Double,
    modifier: Modifier = Modifier,
    metricsPillRect: Rect? = null,
    nextPillRect: Rect? = null,
) {
    val measurer = rememberTextMeasurer()

    // Pulsing position-ring: radius 8->19->8 and opacity 0.5->0->0.5 over 2.4s, infinite.
    // Two independent reversing tweens; both values are READ inside the draw lambda below
    // so Compose redraws the Canvas every frame.
    val pulseT = rememberInfiniteTransition(label = "marker-pulse")
    val pulseR by pulseT.animateFloat(
        initialValue = 8f,
        targetValue = 19f,
        animationSpec = infiniteRepeatable(tween(2400), RepeatMode.Reverse),
        label = "pulse-r",
    )
    val pulseA by pulseT.animateFloat(
        initialValue = 0.5f,
        targetValue = 0f,
        animationSpec = infiniteRepeatable(tween(2400), RepeatMode.Reverse),
        label = "pulse-a",
    )

    // Eased CAMERA PAN: the elevation window's lower bound eases to its target (the same
    // page/lead step the design computes) with an easeInOutCubic over ~1s, so the colored
    // map glides instead of snapping when the climb nears the top edge.
    val targetLo = remember(route, markerDistMiles) { computeTargetLo(route, markerDistMiles) }
    val elevLo by animateFloatAsState(
        targetValue = targetLo.toFloat(),
        animationSpec = tween(1000, easing = CubicBezierEasing(0.65f, 0f, 0.35f, 1f)),
        label = "camera-pan",
    )

    // Effective background behind the Canvas (photo composited with the map scrim, provided by
    // the running screen). Canvas free-text colors are solved against it via legibleOn so they
    // stay legible over the now-visible photo.
    val overlayBg = LocalOverlayBackground.current

    Canvas(modifier = modifier) {
        drawRidgeline(
            route, markerDistMiles, pulseR, pulseA, elevLo.toDouble(),
            measurer, metricsPillRect, nextPillRect, overlayBg,
        )
    }
}

/** Window math shared with the draw pass: lower edge (feet) of the elevation window. */
private fun computeTargetLo(route: RidgelineRoute, markerDist: Double): Double {
    val totalVert = route.vertAt(route.total)
    val fitsWhole = totalVert <= ELEV_WINDOW * 1.12
    if (fitsWhole) return 0.0
    val md = markerDist.coerceIn(0.0, route.total)
    val E = route.vertAt(max(0.0, md))
    val EW = ELEV_WINDOW
    val PAGE = EW * 0.40
    val LEAD = EW * 0.42
    return min(
        max(0.0, kotlin.math.floor((E - LEAD) / PAGE) * PAGE),
        max(0.0, totalVert - EW),
    )
}

private fun DrawScope.drawRidgeline(
    route: RidgelineRoute,
    markerDist: Double,
    pulseR: Float,
    pulseA: Float,
    elevLo: Double,
    measurer: TextMeasurer,
    metricsPillRect: Rect?,
    nextPillRect: Rect?,
    overlayBg: Color,
) {
    val W = size.width
    val H = size.height
    val dp = this.density   // px per dp (DrawScope is a Density)

    // Vertical elevation strip hugs the RIGHT EDGE of the map canvas (target: flush, thin,
    // small consistent margin) rather than floating mid-gap before the stepper rail.
    val stripW = 12f * dp
    val stripX = W - 16f * dp - stripW / 2f   // strip centerline ~16dp in from the map's right edge
    val mapW = stripX - stripW / 2f - 14f * dp   // drawable width for the switchback path

    val topY = 74f / 800f * H
    val botY = H - 50f * dp

    val totalVert = route.vertAt(route.total)
    val hasMini = totalVert > ELEV_WINDOW * 1.12
    val fitsWhole = !hasMini
    // centerX/amplitude derived from the ACTUAL drawable width, not a fixed design canvas.
    // Ratios match design (centerX 600/638, ampBase 442/482 on W=1280), branched on hasMini.
    val centerX = mapW * (if (hasMini) 0.469f else 0.498f)
    val ampBase = mapW * (if (hasMini) 0.345f else 0.377f)

    val md = markerDist.coerceIn(0.0, route.total)
    val E = route.vertAt(max(0.0, md))
    val EW = if (fitsWhole) max(160.0, totalVert) else ELEV_WINDOW

    // Elevation camera: lower edge is the eased value passed in (animated in the composable).
    // When the whole climb fits, it rests at 0.
    val elevLoLocal = if (fitsWhole) 0.0 else elevLo
    val elevHi = elevLoLocal + EW
    fun screenY(e: Double): Float = (botY - ((e - elevLoLocal) / EW) * (botY - topY)).toFloat()

    fun worldX(d: Double): Float {
        val g = (route.gradeAt(max(0.0, d - 0.05)) + route.gradeAt(max(0.0, d)) + route.gradeAt(d + 0.05)) / 3.0
        // amplitude eases narrower on steep pitches (design exact: 0.55 floor + organic
        // d-jitter). Relies on #1 base-turn term so flat routes still meander wide.
        val amp = ampBase * (0.55f + 0.45f * (1f - min(1f, (g / 16.0).toFloat()))) *
            (0.85f + 0.15f * sin(d * 1.7 + 0.4).toFloat())
        return centerX + amp * sin(route.phaseAt(d) + 0.4 * sin(d * 1.23 + 0.7)).toFloat()
    }

    // --- background radial gradient (glow -> bg at 64%, centered 60%/8% top) ---
    // Design: radial-gradient(82% 78% at 60% 8%, glow 0%, bg 64%). Compose radial
    // gradients are circular, so use 82% of W as the radius (the dominant axis).
    // OPAQUE themed canvas — matches the design previews (no photo bleed-through). The map panel
    // IS the theme: solid bg with a soft glow toward the top, the route/contours read crisp on it.
    drawRect(color = RidgelineTheme.bg, size = Size(W, H)) // solid base — no photo shows through
    drawRect(
        brush = Brush.radialGradient(
            colorStops = arrayOf(
                0f to RidgelineTheme.glow,
                0.64f to RidgelineTheme.bg,
                1f to RidgelineTheme.bg,
            ),
            center = Offset(W * 0.60f, H * 0.08f),
            radius = W * 0.82f,
        ),
        size = Size(W, H),
    )

    // --- amber summit radial (dPeak): warm glow top-right, behind the route ---
    // Design: <radialGradient cx=0.59 cy=0.10 r=0.6> rgba(255,179,92,0.16)->transparent,
    // painted over the top 360px of the map.
    run {
        val peakTop = min(H, 360f * dp)
        drawRect(
            brush = Brush.radialGradient(
                colorStops = arrayOf(
                    0f to RidgelineTheme.elev.copy(alpha = 0.16f),
                    1f to RidgelineTheme.elev.copy(alpha = 0f),
                ),
                center = Offset(W * 0.59f, H * 0.10f),
                radius = W * 0.6f,
            ),
            size = Size(W, peakTop),
        )
    }

    // --- contours (constant-elevation bands) ---
    val CFT = 38.0
    var ce = kotlin.math.ceil(elevLoLocal / CFT) * CFT
    while (ce <= elevHi + 1) {
        val y = screenY(ce)
        val major = kotlin.math.abs(ce % 190.0) < CFT / 2
        val path = Path()
        var first = true
        var x = -30f
        while (x <= W + 30f) {
            val xd = x.toDouble()
            val gx = ((x - centerX) / (mapW * 0.336f)).toDouble()
            val yy = (y - 46f * exp(-gx * gx).toFloat() +
                7f * sin(xd * 0.006 + ce * 0.02).toFloat() +
                3f * sin(xd * 0.018 + ce * 0.05).toFloat())
            if (first) { path.moveTo(x, yy); first = false } else path.lineTo(x, yy)
            x += 26f
        }
        drawPath(
            path,
            color = if (major) RidgelineTheme.contourMajor else RidgelineTheme.accentDim,
            alpha = if (major) 0.46f else 0.24f,
            style = Stroke(width = if (major) 1.4f else 1f),
        )
        ce += CFT
    }

    // --- route: sample across the elevation window, group into colored runs ---
    val M = 140
    data class Seg(val key: String, val trav: Boolean, val g: Double, val pts: MutableList<Offset>)
    val segs = ArrayList<Seg>()
    var curS: Seg? = null
    for (k in 0..M) {
        val e = elevLoLocal + (k.toDouble() / M) * EW
        if (e > totalVert + 0.5) break
        val d = route.distAtElev(e)
        val p = Offset(worldX(d), screenY(e))
        val trav = e <= E
        val g = route.gradeAt(d)
        val key = if (trav) "T" else "G" + Math.round(g)
        val s = curS
        if (s == null || s.key != key) {
            s?.pts?.add(p)
            val ns = Seg(key, trav, g, mutableListOf(p))
            segs.add(ns); curS = ns
        } else s.pts.add(p)
    }
    for (sg in segs) {
        if (sg.pts.size < 2) continue
        val path = Path()
        path.moveTo(sg.pts[0].x, sg.pts[0].y)
        for (i in 1 until sg.pts.size) path.lineTo(sg.pts[i].x, sg.pts[i].y)
        if (!sg.trav) {
            // soft Gaussian glow underlay for ahead segments — design uses
            // feGaussianBlur stdDeviation=3.4 on a 7.5-wide stroke at opacity 0.26.
            // Rendered via a native Paint with a BlurMaskFilter so it reads as a soft
            // band, not a hard double-stroke.
            drawIntoCanvas { canvas ->
                val glow = android.graphics.Paint().apply {
                    isAntiAlias = true
                    style = android.graphics.Paint.Style.STROKE
                    strokeWidth = 7.5f * dp
                    strokeCap = android.graphics.Paint.Cap.ROUND
                    strokeJoin = android.graphics.Paint.Join.ROUND
                    color = RidgelineTheme.gradeColor(sg.g).copy(alpha = 0.26f).toArgb()
                    maskFilter = android.graphics.BlurMaskFilter(
                        3.4f * dp, android.graphics.BlurMaskFilter.Blur.NORMAL,
                    )
                }
                canvas.nativeCanvas.drawPath(path.asAndroidPath(), glow)
            }
        }
        drawPath(
            path,
            color = if (sg.trav) RidgelineTheme.trailDim else RidgelineTheme.gradeColor(sg.g),
            alpha = if (sg.trav) 0.62f else 1f,
            style = Stroke(
                width = if (sg.trav) 5f * dp else 4.6f * dp,
                cap = StrokeCap.Round, join = StrokeJoin.Round,
            ),
        )
    }

    // --- auto-extend dashed ghost line: cue that the climb continues above the window ---
    if (elevHi < totalVert - 0.5) {
        val tx = worldX(route.distAtElev(elevHi))
        val ghost = Path().apply {
            moveTo(tx, topY)
            lineTo(tx - 34f * dp, topY - 22f * dp)
            lineTo(tx - 92f * dp, topY - 40f * dp)
        }
        drawPath(
            ghost,
            color = RidgelineTheme.dim2,
            style = Stroke(
                width = 3f * dp,
                cap = StrokeCap.Round,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(2f * dp, 8f * dp)),
            ),
        )
    }

    // --- grade chips at interval boundaries (ahead of marker) ---
    // De-clutter: skip a chip if it is within MIN_CHIP_GAP (vertical px) of the
    // marker or of a chip we already placed, AND skip any chip whose drawn extent
    // falls inside (or within ~12dp of) the metrics-pill or NEXT-pill rect, so the
    // chips never collide with the overlay pills (matches the target).
    val mPos = Offset(worldX(md), screenY(E))
    val minChipGap = 34f * dp
    val pillMargin = 12f * dp
    fun inflated(r: Rect?): Rect? = r?.let {
        Rect(it.left - pillMargin, it.top - pillMargin, it.right + pillMargin, it.bottom + pillMargin)
    }
    val metricsGuard = inflated(metricsPillRect)
    val nextGuard = inflated(nextPillRect)
    val placedChipY = ArrayList<Float>()
    placedChipY.add(mPos.y)
    var i = route.idxAt(route.distAtElev(elevLoLocal))
    var chipCount = 0
    while (chipCount < 40 && i < route.count) {
        val bs = route.startOf(i)
        val be = route.vertAt(bs)
        if (be > elevHi) break
        if (be >= elevLoLocal && bs >= md - 0.02) {
            val pos = Offset(worldX(bs), screenY(be))
            val tooClose = placedChipY.any { kotlin.math.abs(it - pos.y) < minChipGap }
            if (!tooClose) {
                val side = if (pos.x < centerX) -1 else 1
                val color = RidgelineTheme.gradeColor(route.gradeIdx(i))
                // Geometry (dot, pill border) keeps the true grade color; the chip TEXT is
                // solved against the photo+scrim background so it stays legible.
                val textColor = color.legibleOn(overlayBg, targetLc = 60.0)
                val label = "${Math.round(route.gradeIdx(i))}%"
                val tl = measurer.measure(
                    label,
                    style = TextStyle(
                        color = textColor, fontFamily = RidgelineMonoFamily,
                        fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                    ),
                )
                val cx = pos.x + side * 22f
                // Pill badge (design: rect h24 rx6, pillBg fill, 1px colored border,
                // centered text, anchor dot). Pill butts against the anchor dot at cx.
                val pillW = tl.size.width + 14f
                val pillLeft = if (side < 0) cx - pillW else cx
                val pillTop = pos.y - 12f
                val textLeft = pillLeft + (pillW - tl.size.width) / 2f
                // Full drawn extent of the chip: dot at cx + pill.
                val chipRect = Rect(
                    left = min(cx - 4f, pillLeft),
                    top = pillTop,
                    right = max(cx + 4f, pillLeft + pillW),
                    bottom = pillTop + 24f,
                )
                val hitsPill = (metricsGuard?.overlaps(chipRect) == true) ||
                    (nextGuard?.overlaps(chipRect) == true)
                if (!hitsPill) {
                    placedChipY.add(pos.y)
                    drawRoundRectCompat(pillLeft, pillTop, pillW, 24f, 6f, RidgelineTheme.pillBg)
                    drawRoundRect(
                        color = color,
                        topLeft = Offset(pillLeft, pillTop),
                        size = Size(pillW, 24f),
                        cornerRadius = androidx.compose.ui.geometry.CornerRadius(6f, 6f),
                        style = Stroke(width = 1f),
                    )
                    drawCircle(color, radius = 4f, center = Offset(cx, pos.y))
                    drawText( // legible-exempt: solved via legibleOn over the photo
                        tl,
                        topLeft = Offset(textLeft, pos.y - tl.size.height / 2f),
                    )
                }
            }
        }
        i++; chipCount++
    }

    // --- finish flag (if within window) ---
    if (totalVert <= elevHi + 1) {
        val fx = worldX(route.total)
        val fy = screenY(totalVert)
        drawLine(RidgelineTheme.fg, Offset(fx, fy), Offset(fx, fy - 30f * dp), strokeWidth = 2.5f)
        val flag = Path().apply {
            moveTo(fx, fy - 30f * dp)
            lineTo(fx + 26f * dp, fy - 24f * dp)
            lineTo(fx, fy - 17f * dp)
            close()
        }
        drawPath(flag, RidgelineTheme.elev)
        drawCircle(RidgelineTheme.elev, radius = 5f, center = Offset(fx, fy))
        // "FINISH · N,NNN ft" amber mono label
        val finishLabel = "FINISH · ${"%,d".format(Math.round(totalVert))} ft"
        val finishTl = measurer.measure(
            finishLabel,
            style = TextStyle(
                // amber summit color, solved against the photo+scrim so it stays legible.
                color = RidgelineTheme.elev.legibleOn(overlayBg, targetLc = 60.0),
                fontFamily = RidgelineMonoFamily,
                fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
            ),
        )
        drawText( // legible-exempt: solved via legibleOn over the photo
            finishTl,
            topLeft = Offset(fx + 12f, fy + 22f - finishTl.size.height),
        )
    }

    // --- position marker (pulsing accent ring + dot) ---
    // pulseR / pulseA are read here so Compose redraws the Canvas as they animate.
    drawCircle(
        RidgelineTheme.accent, radius = pulseR, center = mPos,
        alpha = pulseA, style = Stroke(width = 1.5f),
    )
    drawCircle(RidgelineTheme.bg, radius = 9.5f, center = mPos)
    drawCircle(RidgelineTheme.accent, radius = 7f, center = mPos)

    // --- vertical elevation strip (ALWAYS shown) at the right edge of the map ---
    // Full-route grade samples; travelled cells dimmer, finish flag at top, current
    // position marker, "0 ft" label at bottom. Anchored just left of the stepper rail.
    run {
        val mx = stripX
        // Start the strip BELOW the top-right NEXT pill (it shares the right edge), so the two
        // don't overlap; fall back to topY when the pill isn't measured yet. The finish flag's pole
        // is drawn ~17px ABOVE mTop, so add that clearance too or the flag tucks under the pill.
        val mTop = nextPillRect?.let { (it.bottom + 10f * dp + 18f).toFloat() } ?: topY
        val mBot = botY
        val mW = stripW
        val mStart = 0.0
        val mEnd = max(1.0, totalVert)
        val span = max(1.0, mEnd - mStart)
        fun yOf(e: Double): Float = (mBot - ((e - mStart) / span) * (mBot - mTop)).toFloat()

        // track background (rounded) + subtle 1px T.line border
        drawRoundRectCompat(
            mx - mW / 2f - 1f, mTop - 1f, mW + 2f, mBot - mTop + 2f, mW / 2f,
            RidgelineTheme.pillBg,
        )
        drawRoundRect(
            color = RidgelineTheme.line,
            topLeft = Offset(mx - mW / 2f - 1f, mTop - 1f),
            size = Size(mW + 2f, mBot - mTop + 2f),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(mW / 2f, mW / 2f),
            style = Stroke(width = 1f),
        )
        val K = 48
        for (k in 0 until K) {
            val e0 = mStart + (k.toDouble() / K) * span
            val e1 = mStart + ((k + 1).toDouble() / K) * span
            val em = (e0 + e1) / 2.0
            val g = route.gradeAt(route.distAtElev(em))
            val trav = em <= E
            val yTop = yOf(e1)
            val hh = max(1f, yOf(e0) - yOf(e1) + 0.6f)
            drawRect(
                RidgelineTheme.gradeColor(g),
                topLeft = Offset(mx - mW / 2f, yTop),
                size = Size(mW, hh),
                alpha = if (trav) 0.26f else 0.95f,
            )
        }
        // viewport box highlighting the detailed slice (only meaningful when windowed)
        if (hasMini) {
            val vTop = yOf(min(mEnd, elevHi))
            val vBot = yOf(max(mStart, elevLoLocal))
            // Leader lines bridging the strip's viewport back to the switchback map.
            val leaderX = mx - mW / 2f - 6f
            val mapRightEdge = mapW
            val dash = PathEffect.dashPathEffect(floatArrayOf(2f, 5f))
            drawLine(
                RidgelineTheme.accent, Offset(leaderX, vTop), Offset(mapRightEdge, topY),
                strokeWidth = 1f, alpha = 0.35f, pathEffect = dash,
            )
            drawLine(
                RidgelineTheme.accent, Offset(leaderX, vBot), Offset(mapRightEdge, botY),
                strokeWidth = 1f, alpha = 0.35f, pathEffect = dash,
            )
            // viewport-highlight fill + bright-green 1.5px border
            drawRoundRectCompat(
                mx - mW / 2f - 6f, vTop, mW + 12f, max(10f, vBot - vTop), 4f,
                RidgelineTheme.accent.copy(alpha = 0.09f),
            )
            drawRoundRect(
                color = RidgelineTheme.accent,
                topLeft = Offset(mx - mW / 2f - 6f, vTop),
                size = Size(mW + 12f, max(10f, vBot - vTop)),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(4f, 4f),
                style = Stroke(width = 1.5f),
            )
        }
        // finish flag at top
        val ftY = yOf(totalVert)
        drawLine(RidgelineTheme.fg, Offset(mx, ftY), Offset(mx, ftY - 17f), strokeWidth = 2f, cap = StrokeCap.Round)
        val miniFlag = Path().apply {
            moveTo(mx + 1f, ftY - 17f)
            lineTo(mx + 12f, ftY - 13.5f)
            lineTo(mx + 1f, ftY - 10f)
            close()
        }
        drawPath(miniFlag, RidgelineTheme.elev)
        drawCircle(RidgelineTheme.elev, radius = 4.5f, center = Offset(mx, ftY))
        // current-position marker
        drawCircle(RidgelineTheme.bg, radius = 7f, center = Offset(mx, yOf(E)))
        drawCircle(RidgelineTheme.accent, radius = 5f, center = Offset(mx, yOf(E)))
        // "0 ft" at bottom
        val zeroTl = measurer.measure(
            "0 ft",
            style = TextStyle(
                color = RidgelineTheme.dim2.legibleOn(overlayBg, targetLc = 45.0),
                fontFamily = RidgelineMonoFamily,
                fontSize = 10.sp,
                fontFeatureSettings = "tnum",
            ),
        )
        drawText( // legible-exempt: solved via legibleOn over the photo
            zeroTl,
            topLeft = Offset(mx - zeroTl.size.width / 2f, mBot + 6f),
        )
    }
}

/** Rounded-rect fill helper (Compose drawRoundRect needs CornerRadius import; keep it local). */
private fun DrawScope.drawRoundRectCompat(
    x: Float, y: Float, w: Float, h: Float, radius: Float, color: Color,
) {
    drawRoundRect(
        color = color,
        topLeft = Offset(x, y),
        size = Size(w, h),
        cornerRadius = androidx.compose.ui.geometry.CornerRadius(radius, radius),
    )
}
