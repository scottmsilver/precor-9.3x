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
import androidx.compose.ui.graphics.drawscope.clipRect
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

// --- Route model (finite, POSITION domain = planned program seconds) ---------
// Ported from makeRoute() in DirectionD.jsx (the finite/intervals branch), then
// re-domained: layout position is program TIME, so every interval gets screen
// length proportional to how long you actually run it, and interval boundaries
// (where the incline changes) are structurally the program's own clock — they
// can never drift from the route bends. A distance-domain layout let a 25s
// sprint own 68% of the track while a 75s recovery collapsed to a sliver.
// The organic detail (switchback phase, amplitude jitter, vert feet) is still
// integrated over PLANNED MILES internally, so the mile-tuned design constants
// keep their look.

/** One planned interval, in the route's domain. */
data class RouteInterval(val grade: Double, val speed: Double, val durSec: Double)

/** Finite climbing route assembled from planned intervals. */
class RidgelineRoute(intervals: List<RouteInterval>) {
    private val iv: List<RouteInterval> = if (intervals.isEmpty())
        listOf(RouteInterval(2.0, 4.0, 450.0)) else intervals
    // Cumulative boundaries in both domains: seconds (layout) and miles (detail).
    private val cum: DoubleArray = DoubleArray(iv.size + 1).also {
        for (i in iv.indices) it[i + 1] = it[i] + max(1.0, iv[i].durSec)
    }
    private val cumMi: DoubleArray = DoubleArray(iv.size + 1).also {
        for (i in iv.indices) it[i + 1] = it[i] + max(1.0, iv[i].durSec) * iv[i].speed / 3600.0
    }
    val count: Int = iv.size

    /** Route length in the layout domain: planned seconds. */
    val total: Double = cum[count]

    /** Planned miles over the whole route (for labels/diagnostics). */
    val totalMi: Double = cumMi[count]

    fun idxAt(pos: Double): Int {
        var i = 0
        while (i < count - 1 && cum[i + 1] <= pos) i++
        return i
    }

    fun gradeIdx(i: Int): Double = iv[min(i, count - 1)].grade
    fun speedIdx(i: Int): Double = iv[min(i, count - 1)].speed
    fun startOf(i: Int): Double = cum[min(i, count)]
    fun endOf(i: Int): Double = cum[min(i + 1, count)]

    fun gradeAt(pos: Double): Double = gradeIdx(idxAt(pos))

    /**
     * Exact program-position → route-position mapping. In the time domain this is
     * simply the program clock (interval start + elapsed, clamped), which is the
     * whole point: an incline change can't miss its bend.
     */
    fun posAtProgram(i: Int, elapsedSec: Double): Double {
        if (i < 0) return 0.0
        if (i >= count) return total
        return cum[i] + min(max(0.0, elapsedSec), cum[i + 1] - cum[i])
    }

    /** Planned miles covered by route position [pos] (piecewise per-interval speed). */
    fun distAt(pos: Double): Double {
        val p = pos.coerceIn(0.0, total)
        val i = idxAt(p)
        return cumMi[i] + (p - cum[i]) * speedIdx(i) / 3600.0
    }

    /** Vertical feet climbed to reach route position [pos]. */
    fun vertAt(pos: Double): Double {
        val p = pos.coerceIn(0.0, total)
        val i = idxAt(p)
        var v = 0.0
        for (k in 0 until i) v += (cumMi[k + 1] - cumMi[k]) * gradeIdx(k)
        v += (p - cum[i]) * speedIdx(i) / 3600.0 * gradeIdx(i)
        return v / 100.0 * 5280.0
    }

    // switchback turn-phase: turns accumulate per foot climbed, denser where steep.
    // Integrated over planned miles (mile-tuned constants), walked by position.
    fun phaseAt(pos: Double): Double {
        val p = pos.coerceIn(0.0, total)
        var ph = 0.0
        var i = 0
        while (i < count && cum[i] < p - 1e-9) {
            val end = min(endOf(i), p)
            val segMi = (end - cum[i]) * speedIdx(i) / 3600.0
            val g = gradeIdx(i)
            val vGain = segMi * g / 100.0 * 5280.0
            val noise = 0.82 + 0.36 * sin(i * 5.13 + 1.7)
            // Distance-driven base turn term so FLAT real-program routes still meander
            // into multiple switchbacks (design only ever fed steep synthetic routes,
            // where the grade-gated vGain term alone sufficed). BASE_TURNS_PER_MILE is
            // tuned so a flat 1-mi segment yields ~2-3 half-cycles of sin(phase).
            ph += SW_A * ((0.5 + g / 8.0) * noise * vGain + BASE_TURNS_PER_MILE * segMi)
            i++
        }
        return ph * phaseScale
    }

    // Short routes accumulate almost no natural phase (a 2-min program is ~0.1mi),
    // which would draw as a straight stub. Scale the whole route's phase up to a
    // floor of ~2.2π so even tiny routes sweep at least one full S-curve; long
    // routes (natural phase ≥ the floor) are untouched.
    private val phaseScale: Double

    init {
        var raw = 0.0
        for (i in 0 until count) {
            val segMi = cumMi[i + 1] - cumMi[i]
            val g = gradeIdx(i)
            val noise = 0.82 + 0.36 * sin(i * 5.13 + 1.7)
            raw += SW_A * ((0.5 + g / 8.0) * noise * (segMi * g / 100.0 * 5280.0) + BASE_TURNS_PER_MILE * segMi)
        }
        phaseScale = if (raw > 1e-9) max(1.0, MIN_TOTAL_PHASE / raw) else 1.0
    }

    /** Invert vertAt: route position (seconds) at a given elevation (feet climbed). */
    fun posAtElev(e: Double): Double {
        var v = 0.0
        for (i in 0 until count) {
            val segMi = cumMi[i + 1] - cumMi[i]
            val segV = segMi * gradeIdx(i) / 100.0 * 5280.0
            if (v + segV >= e || i == count - 1) {
                val frac = if (segV > 1e-9) ((e - v) / segV).coerceIn(0.0, 1.0) else 0.0
                return cum[i] + frac * (cum[i + 1] - cum[i])
            }
            v += segV
        }
        return total
    }

    companion object {
        const val SW_A = 0.030
        // Half-cycles per mile contributed independent of grade (keeps flat routes wavy).
        // 0.030 * 250 * 1mi ≈ 7.5 rad ≈ 2.4 half-cycles for a flat 1-mi segment.
        const val BASE_TURNS_PER_MILE = 250.0
        // Minimum total switchback sweep for the whole route (~2.2π ≈ one full S).
        const val MIN_TOTAL_PHASE = 7.0
    }
}

// Route-position (planned seconds) shown at once. 25 minutes per screen: a typical
// 45-60min program exceeds the window and therefore WINDOWS — the camera pans as you
// progress and the minimap's viewport box + leader lines activate — while shorter
// programs fit whole and always FILL the panel (no more collapsing a short/flat
// route into a stub against a fixed feet-of-climb window).
private const val POS_WINDOW = 1500.0

private fun lerp(a: Double, b: Double, t: Double) = a + (b - a) * t

// Throttle key for the path-vs-chip alignment validation log (draws run per frame).
private var lastAlignmentLogKey: Int = 0

/**
 * The Ridgeline route map: program-progress-vs-switchback plot. Vertical axis = route
 * position (planned program seconds — so the dot climbs steadily and every bend is an
 * incline change), horizontal = centerX + amp*sin(phase). Travelled portion dim, ahead
 * colored by grade. Contours mark real elevations crossed, pulsing accent marker at
 * current position, grade chips at interval boundaries, vertical minimap strip on the
 * right.
 *
 * @param markerPos current route position in planned seconds (drives the marker).
 */
@Composable
fun RidgelineMap(
    route: RidgelineRoute,
    markerPos: Double,
    modifier: Modifier = Modifier,
    metricsPillRect: Rect? = null,
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

    // Eased CAMERA PAN: the view window's lower bound eases to its target (the same
    // page/lead step the design computes) with an easeInOutCubic over ~1s, so the colored
    // map glides instead of snapping when progress nears the top edge.
    val targetLo = remember(route, markerPos) { computeTargetLo(route, markerPos) }
    val elevLo by animateFloatAsState(
        targetValue = targetLo.toFloat(),
        animationSpec = tween(1000, easing = CubicBezierEasing(0.65f, 0f, 0.35f, 1f)),
        label = "camera-pan",
    )

    // Effective background behind the Canvas for free-text contrast solving. With the
    // full panel scrim (classic mode) that's the composited panel background from the
    // running screen. In SEE_THROUGH mode there is no panel — every piece of canvas
    // text sits on its own dark island (chip pills, the trail scrim, the strip
    // backing), so solve against that island tone (codex review: the ambient local
    // was a speed-region estimate, wrong for the map area).
    val overlayBg = if (SEE_THROUGH_MAP) Color(0xFF11171B) else LocalOverlayBackground.current


    Canvas(modifier = modifier) {
        // Canvas does NOT clip children by default — contours draw past the panel
        // edge (x in [-30, W+30]) and were bleeding onto neighboring UI.
        clipRect(0f, 0f, size.width, size.height) {
            drawRidgeline(
                route, markerPos, pulseR, pulseA, elevLo.toDouble(),
                measurer, metricsPillRect, overlayBg,
            )
        }
    }
}

/** Window math shared with the draw pass: lower edge (route seconds) of the view window. */
private fun computeTargetLo(route: RidgelineRoute, markerPos: Double): Double {
    val fitsWhole = route.total <= POS_WINDOW * 1.12
    if (fitsWhole) return 0.0
    val md = markerPos.coerceIn(0.0, route.total)
    val EW = POS_WINDOW
    val PAGE = EW * 0.40
    val LEAD = EW * 0.42
    return min(
        max(0.0, kotlin.math.floor((md - LEAD) / PAGE) * PAGE),
        max(0.0, route.total - EW),
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
    val hasMini = route.total > POS_WINDOW * 1.12
    val fitsWhole = !hasMini
    // centerX/amplitude derived from the ACTUAL drawable width, not a fixed design canvas.
    // Wider than the design's ratios (centerX 600/638, ampBase 442/482 on W=1280):
    // steep programs squeezed the path into the left-center of the panel, so the
    // amplitude grows to fill the drawable width (chips clamp to stay on-canvas).
    val centerX = mapW * 0.50f
    val ampBase = mapW * (if (hasMini) 0.40f else 0.44f)

    val md = markerDist.coerceIn(0.0, route.total)
    // View window in route position (planned seconds). A whole-route view spans the
    // full route (2% headroom), so short programs FILL the panel instead of collapsing
    // against a fixed feet-of-climb window.
    val EW = if (fitsWhole) route.total * 1.02 else POS_WINDOW

    // Camera: lower edge is the eased value passed in (animated in the composable).
    // When the whole route fits, it rests at 0.
    val camLo = if (fitsWhole) 0.0 else elevLo
    val camHi = camLo + EW
    fun screenY(pos: Double): Float = (botY - ((pos - camLo) / EW) * (botY - topY)).toFloat()

    // Organic detail (amplitude jitter, phase wobble, grade smoothing) is tuned in the
    // design's MILE domain — feed it planned miles via distAt so the look is preserved.
    // Grade smoothing keeps its ±0.05mi reach by converting through the LOCAL interval
    // speed (0.05mi = 180/speed seconds), not a fixed time radius (codex review).
    fun worldX(pos: Double): Float {
        val u = route.distAt(pos)
        val smoothSec = 180.0 / max(0.5, route.speedIdx(route.idxAt(pos)))
        val g = (route.gradeAt(max(0.0, pos - smoothSec)) + route.gradeAt(pos) + route.gradeAt(pos + smoothSec)) / 3.0
        // amplitude eases narrower on steep pitches (design exact: 0.55 floor + organic
        // jitter). Relies on the base-turn term so flat routes still meander wide.
        // steep-pitch floor raised 0.55 -> 0.65 so steep routes still sweep wide
        val amp = ampBase * (0.65f + 0.35f * (1f - min(1f, (g / 16.0).toFloat()))) *
            (0.85f + 0.15f * sin(u * 1.7 + 0.4).toFloat())
        return centerX + amp * sin(route.phaseAt(pos) + 0.4 * sin(u * 1.23 + 0.7)).toFloat()
    }

    // --- background radial gradient (glow -> bg at 64%, centered 60%/8% top) ---
    // Design: radial-gradient(82% 78% at 60% 8%, glow 0%, bg 64%). Compose radial
    // gradients are circular, so use 82% of W as the radius (the dominant axis).
    // OPAQUE themed canvas — matches the design previews (no photo bleed-through). The map panel
    // IS the theme: solid bg with a soft glow toward the top, the route/contours read crisp on it.
    if (!SEE_THROUGH_MAP) {
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
    }

    // --- contours (constant-elevation bands, mapped through the route's climb) ---
    // Contours live at real elevations; posAtElev places each at the route position
    // where that elevation is first reached. Flat routes simply have few/none.
    val CFT = 38.0
    val vertLo = route.vertAt(camLo)
    val vertHi = route.vertAt(min(camHi, route.total))
    fun contourWavePath(ceV: Double): Path {
        val y = screenY(route.posAtElev(ceV))
        val path = Path()
        var first = true
        var x = -30f
        while (x <= W + 30f) {
            val xd = x.toDouble()
            val gx = ((x - centerX) / (mapW * 0.336f)).toDouble()
            val yy = (y - 46f * exp(-gx * gx).toFloat() +
                7f * sin(xd * 0.006 + ceV * 0.02).toFloat() +
                3f * sin(xd * 0.018 + ceV * 0.05).toFloat())
            if (first) { path.moveTo(x, yy); first = false } else path.lineTo(x, yy)
            x += 26f
        }
        return path
    }
    if (SHOW_CONTOURS) {
        var ce = kotlin.math.ceil(max(CFT, vertLo) / CFT) * CFT
        while (ce <= vertHi + 1) {
            val major = kotlin.math.abs(ce % 190.0) < CFT / 2
            drawPath(
                contourWavePath(ce),
                color = if (major) RidgelineTheme.contourMajor else RidgelineTheme.accentDim,
                alpha = if (major) 0.46f else 0.24f,
                style = Stroke(width = if (major) 1.4f else 1f),
            )
            ce += CFT
        }
    }

    // --- route: sample uniformly along the route POSITION visible in the window ---
    // (Elevation-indexed sampling skipped flat segments entirely — a 0% interval
    // contributed zero path. Position sampling gives every second of the program
    // its proportional share of track.)
    val M = 140
    // Uniform samples PLUS the exact interval boundaries, so the path's color change
    // (and the chip anchored there) sits precisely on the bend instead of up to one
    // sample step away.
    val samplePos = ArrayList<Double>(M + route.count + 2)
    for (k in 0..M) {
        val p0 = camLo + (k.toDouble() / M) * EW
        if (p0 > route.total + 1e-6) break
        samplePos.add(min(p0, route.total))
    }
    for (b in 1 until route.count) {
        val bp = route.startOf(b)
        if (bp > camLo && bp < min(camHi, route.total)) samplePos.add(bp)
    }
    samplePos.sort()
    // Validation capture: the EXACT point the path polyline uses at each interval
    // boundary, compared against the chip anchors afterwards (logged once per route/size).
    val boundarySet = HashSet<Double>().also {
        for (b in 1 until route.count) it.add(route.startOf(b))
    }
    val boundaryPathPts = HashMap<Double, Offset>()
    data class Seg(val key: String, val trav: Boolean, val g: Double, val pts: MutableList<Offset>)
    val segs = ArrayList<Seg>()
    var curS: Seg? = null
    // Legacy color-ramp segments: only built when that renderer is active (they're
    // per-frame allocations otherwise — codex review).
    for (d in samplePos) {
        if (d in boundarySet) boundaryPathPts[d] = Offset(worldX(d), screenY(d))
    }
    if (!MONO_THREAD_ROUTE) for (d in samplePos) {
        val p = Offset(worldX(d), screenY(d))
        val trav = d <= md
        val g = route.gradeAt(d)
        val key = if (trav) "T" else "G" + Math.round(g)
        val s = curS
        if (s == null || s.key != key) {
            s?.pts?.add(p)
            val ns = Seg(key, trav, g, mutableListOf(p))
            segs.add(ns); curS = ns
        } else s.pts.add(p)
    }
    if (MONO_THREAD_ROUTE) {
        // S1: the route is ONE elegant ivory thread — the terrain (hillshade +
        // emphasized contours) says what's steep, the line just says where you go.
        run {
            val split = samplePos.indexOfFirst { it > md }
            fun thread(from: Int, to: Int): Path {
                val p = Path()
                for (i in from..to) {
                    val x = worldX(samplePos[i]); val y = screenY(samplePos[i])
                    if (i == from) p.moveTo(x, y) else p.lineTo(x, y)
                }
                return p
            }
            val last = samplePos.size - 1
            val sp = if (split < 0) last else split.coerceAtLeast(1)
            if (SEE_THROUGH_MAP) {
                // localized scrim: a soft blurred dark band under the trail is the
                // ONLY thing dimming the photo along the route (Paint cached — the
                // pulse animation redraws every frame; codex review)
                drawIntoCanvas { c ->
                    c.nativeCanvas.drawPath(thread(0, last).asAndroidPath(), trailScrimPaint(dp))
                }
            }
            // soft shadow under the whole thread
            drawPath(thread(0, last), Color.Black, alpha = 0.40f,
                style = Stroke(width = 4.2f * dp, cap = StrokeCap.Round, join = StrokeJoin.Round))
            // travelled: dim; ahead: bright ivory
            drawPath(thread(0, sp), RidgelineTheme.fg, alpha = 0.38f,
                style = Stroke(width = 2.2f * dp, cap = StrokeCap.Round, join = StrokeJoin.Round))
            if (sp < last) drawPath(thread(sp, last), RidgelineTheme.fg, alpha = 0.95f,
                style = Stroke(width = 2.2f * dp, cap = StrokeCap.Round, join = StrokeJoin.Round))
        }
    } else for (sg in segs) {
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

    // --- auto-extend dashed ghost line: cue that the route continues above the window ---
    if (camHi < route.total - 0.5) {
        val tx = worldX(camHi)
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
    val mPos = Offset(worldX(md), screenY(md))
    val pillMargin = 12f * dp
    fun inflated(r: Rect?): Rect? = r?.let {
        Rect(it.left - pillMargin, it.top - pillMargin, it.right + pillMargin, it.bottom + pillMargin)
    }
    val metricsGuard = inflated(metricsPillRect)
    // De-clutter by REAL rect collision (not a vertical-gap heuristic): a naive
    // min-y-gap silently ate the chip of any interval starting shortly after the
    // previous one — on a 60-min route a 120s climb is ~19px tall, so every
    // "steep push" chip right after it was dropped. Chips now flip to the other
    // side of the bend when the natural side collides, and only skip when both
    // sides are blocked.
    val placedChipRects = ArrayList<Rect>()
    placedChipRects.add(Rect(mPos.x - 20f, mPos.y - 20f, mPos.x + 20f, mPos.y + 20f))
    // (boundary sec, anchor point drawn, grade) for the path-vs-chip validation log.
    val chipAnchors = ArrayList<Triple<Double, Offset, Double>>()
    var i = route.idxAt(camLo)
    var chipCount = 0
    while (chipCount < 40 && i < route.count) {
        val bs = route.startOf(i)
        if (bs > camHi) break
        if (bs >= camLo && bs >= md - 15.0) {
            val pos = Offset(worldX(bs), screenY(bs))
            run {
                val naturalSide = if (pos.x < centerX) -1 else 1
                val color = RidgelineTheme.mutedGradeColor(route.gradeIdx(i))
                // Geometry (dot, pill border) keeps the true grade color; the chip TEXT
                // shows BOTH values of the transition — incline and speed — each in its
                // theme color, solved against the photo+scrim so it stays legible.
                // Values set in the proportional display face (Space Grotesk), not the
                // mono: a monospace "." gets a full advance cell, so "7.5%" read as
                // "7 . 5 %". Times elsewhere keep the mono (fixed advance is a feature
                // for a counting clock, but a defect for static values).
                val gradeTl = measurer.measure(
                    "%.1f%%".format(route.gradeIdx(i)),
                    style = TextStyle(
                        color = color.legibleOn(overlayBg, targetLc = 60.0),
                        fontFamily = RidgelineLabelFamily,
                        fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                    ),
                )
                val spdTl = measurer.measure(
                    "%.1f".format(route.speedIdx(i)),
                    style = TextStyle(
                        color = RidgelineTheme.mutedSpeedColor(route.speedIdx(i)).legibleOn(overlayBg, targetLc = 60.0),
                        fontFamily = RidgelineLabelFamily,
                        fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                    ),
                )
                // Pill badge (design: rect h24 rx6, pillBg fill, 1px colored border,
                // "8% 3.0" text, anchor dot). Pill butts against the anchor dot at cx.
                val pillW = gradeTl.size.width + 6f + spdTl.size.width + 14f
                val pillTop = pos.y - 12f
                // Pill clamped onto the canvas: with the wider route sweep, extreme
                // bends would otherwise push a left/right pill off the drawable area.
                fun pillLeftFor(side: Int): Float {
                    val cx0 = pos.x + side * 22f
                    return (if (side < 0) cx0 - pillW else cx0)
                        .coerceIn(4f, max(4f, mapW - pillW - 4f))
                }
                fun chipRectFor(side: Int): Rect {
                    val pl = pillLeftFor(side)
                    // Extent covers the anchor dot AT THE BEND (pos.x) plus the pill.
                    return Rect(
                        left = min(pos.x - 5f, pl),
                        top = pillTop,
                        right = max(pos.x + 5f, pl + pillW),
                        bottom = pillTop + 24f,
                    )
                }
                fun blocked(r: Rect): Boolean {
                    val inflatedR = Rect(r.left - 4f, r.top - 4f, r.right + 4f, r.bottom + 4f)
                    return (metricsGuard?.overlaps(inflatedR) == true) ||
                        placedChipRects.any { it.overlaps(inflatedR) }
                }
                // Natural side first; flip if it collides; skip only if both blocked.
                var side = naturalSide
                var chipRect = chipRectFor(side)
                if (blocked(chipRect)) {
                    side = -naturalSide
                    chipRect = chipRectFor(side)
                }
                if (!blocked(chipRect)) {
                    val pillLeft = pillLeftFor(side)
                    placedChipRects.add(chipRect)
                    drawRoundRectCompat(pillLeft, pillTop, pillW, 24f, 6f, RidgelineTheme.pillBg)
                    drawRoundRect(
                        color = color,
                        topLeft = Offset(pillLeft, pillTop),
                        size = Size(pillW, 24f),
                        cornerRadius = androidx.compose.ui.geometry.CornerRadius(6f, 6f),
                        style = Stroke(width = 1f),
                    )
                    // Anchor dot sits EXACTLY on the bend (pos = the boundary's path
                    // point) — not offset toward the pill, which visually parked it on
                    // whichever segment happened to pass 22px to the side. Fixed ivory
                    // over a dark ring: a grade-colored dot camouflaged against the
                    // very segment it marks.
                    drawCircle(RidgelineTheme.bg, radius = 5.5f, center = pos)
                    drawCircle(RidgelineTheme.fg, radius = 3.5f, center = pos)
                    chipAnchors.add(Triple(bs, pos, route.gradeIdx(i)))
                    drawText( // legible-exempt: solved via legibleOn over the photo
                        gradeTl,
                        topLeft = Offset(pillLeft + 7f, pos.y - gradeTl.size.height / 2f),
                    )
                    drawText( // legible-exempt: solved via legibleOn over the photo
                        spdTl,
                        topLeft = Offset(pillLeft + 7f + gradeTl.size.width + 6f, pos.y - spdTl.size.height / 2f),
                    )
                }
            }
        }
        i++; chipCount++
    }

    // --- path-vs-chip alignment validation (adb logcat -s RidgelineSync) ---
    // Proves the invariant the chips rely on: the anchor dot is drawn at the SAME
    // point the path polyline passes through at that boundary. Logged once per
    // route/canvas-size combination (the draw pass runs every frame).
    run {
        val key = 31 * route.hashCode() + 31 * size.width.toInt() + size.height.toInt()
        if (key != lastAlignmentLogKey && chipAnchors.isNotEmpty()) {
            lastAlignmentLogKey = key
            for ((bpos, anchor, grade) in chipAnchors) {
                val pathPt = boundaryPathPts[bpos]
                val dx = pathPt?.let { kotlin.math.abs(it.x - anchor.x) } ?: Float.NaN
                val dy = pathPt?.let { kotlin.math.abs(it.y - anchor.y) } ?: Float.NaN
                val ok = pathPt != null && dx < 0.5f && dy < 0.5f
                android.util.Log.d(
                    "RidgelineSync",
                    "chip@%.0fs (%.1f%%): path=(%.1f,%.1f) dot=(%.1f,%.1f) d=(%.2f,%.2f) %s".format(
                        bpos, grade, pathPt?.x ?: Float.NaN, pathPt?.y ?: Float.NaN,
                        anchor.x, anchor.y, dx, dy, if (ok) "ALIGNED" else "MISALIGNED",
                    ),
                )
            }
        }
    }

    // --- finish flag (if within window) ---
    if (route.total <= camHi + 1) {
        val fx = worldX(route.total)
        val fy = screenY(route.total)
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
        // If the right-pointing flag + label would run off the map's drawable area
        // (whole-route views end near the top-right), mirror them to the left.
        val dir = if (fx + 26f * dp + finishTl.size.width + 12f > mapW) -1f else 1f
        drawLine(RidgelineTheme.fg, Offset(fx, fy), Offset(fx, fy - 30f * dp), strokeWidth = 2.5f)
        val flag = Path().apply {
            moveTo(fx, fy - 30f * dp)
            lineTo(fx + dir * 26f * dp, fy - 24f * dp)
            lineTo(fx, fy - 17f * dp)
            close()
        }
        drawPath(flag, RidgelineTheme.elev)
        drawCircle(RidgelineTheme.elev, radius = 5f, center = Offset(fx, fy))
        val labelLeft = if (dir < 0) fx - 12f - finishTl.size.width else fx + 12f
        drawText( // legible-exempt: solved via legibleOn over the photo
            finishTl,
            topLeft = Offset(labelLeft, fy + 22f - finishTl.size.height),
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
        // With the NEXT pill gone the strip owns the full right edge (the finish
        // flag's pole extends ~17px above mTop; topY leaves room for it).
        val mTop = topY
        val mBot = botY
        val mW = stripW
        // Full-route overview in the position domain (whole program, start → finish).
        val mStart = 0.0
        val mEnd = max(1.0, route.total)
        val span = max(1.0, mEnd - mStart)
        fun yOf(pos: Double): Float = (mBot - ((pos - mStart) / span) * (mBot - mTop)).toFloat()

        if (SEE_THROUGH_MAP) {
            // localized scrim behind the whole strip (its own dark island on the photo)
            drawRoundRectCompat(
                mx - mW / 2f - 7f, mTop - 10f, mW + 14f, mBot - mTop + 20f, 9f,
                Color(0xC9070B0E),
            )
        }
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
            val g = route.gradeAt(em)
            val trav = em <= md
            val yTop = yOf(e1)
            val hh = max(1f, yOf(e0) - yOf(e1) + 0.6f)
            drawRect(
                RidgelineTheme.mutedGradeColor(g),
                topLeft = Offset(mx - mW / 2f, yTop),
                size = Size(mW, hh),
                alpha = if (trav) 0.24f else 0.80f,
            )
        }
        // viewport box highlighting the detailed slice (only meaningful when windowed)
        if (hasMini) {
            val vTop = yOf(min(mEnd, camHi))
            val vBot = yOf(max(mStart, camLo))
            // Leader lines bridging the strip's viewport back to the switchback map.
            // Neutral ivory, not accent green — the frame is chrome, not route data
            // (green would read as "current/next" like the marker and next tick).
            val leaderX = mx - mW / 2f - 6f
            val mapRightEdge = mapW
            val dash = PathEffect.dashPathEffect(floatArrayOf(2f, 5f))
            drawLine(
                RidgelineTheme.fg, Offset(leaderX, vTop), Offset(mapRightEdge, topY),
                strokeWidth = 1f, alpha = 0.28f, pathEffect = dash,
            )
            drawLine(
                RidgelineTheme.fg, Offset(leaderX, vBot), Offset(mapRightEdge, botY),
                strokeWidth = 1f, alpha = 0.28f, pathEffect = dash,
            )
            // viewport-highlight fill + neutral 1.5px border
            drawRoundRectCompat(
                mx - mW / 2f - 6f, vTop, mW + 12f, max(10f, vBot - vTop), 4f,
                RidgelineTheme.fg.copy(alpha = 0.07f),
            )
            drawRoundRect(
                color = RidgelineTheme.fg.copy(alpha = 0.55f),
                topLeft = Offset(mx - mW / 2f - 6f, vTop),
                size = Size(mW + 12f, max(10f, vBot - vTop)),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(4f, 4f),
                style = Stroke(width = 1.5f),
            )
        }
        // finish flag at top
        val ftY = yOf(route.total)
        drawLine(RidgelineTheme.fg, Offset(mx, ftY), Offset(mx, ftY - 17f), strokeWidth = 2f, cap = StrokeCap.Round)
        val miniFlag = Path().apply {
            moveTo(mx + 1f, ftY - 17f)
            lineTo(mx + 12f, ftY - 13.5f)
            lineTo(mx + 1f, ftY - 10f)
            close()
        }
        drawPath(miniFlag, RidgelineTheme.elev)
        drawCircle(RidgelineTheme.elev, radius = 4.5f, center = Offset(mx, ftY))
        // --- last/next transition ticks (replaces the NEXT pill) ---
        // The boundary you just crossed (dim, time only) and the one coming up
        // (accent, time PLUS the incoming grade/speed — the actual change the tick
        // announces), left of the strip, so "where am I between transitions and
        // what's coming" reads at a glance.
        run {
            val curIvIdx = route.idxAt(md)
            val lastB = route.startOf(curIvIdx)
            val nextB = route.endOf(curIvIdx)
            // coerceAtLeast/AtMost (not coerceIn): an inverted range on a degenerate
            // tiny canvas must clamp, not throw (codex review).
            fun clampY(top: Float, h: Int): Float =
                top.coerceAtMost(mBot - h).coerceAtLeast(mTop)
            val labelRight = mx - mW / 2f - 10f
            fun transitionTick(pos: Double, isNext: Boolean) {
                val y = yOf(pos)
                val color = if (isNext) RidgelineTheme.accent else RidgelineTheme.dim
                drawLine(
                    color,
                    Offset(mx - mW / 2f - 5f, y),
                    Offset(mx + mW / 2f + 5f, y),
                    strokeWidth = if (isNext) 2f else 1.5f,
                    alpha = if (isNext) 1f else 0.7f,
                )
                // With the NEXT pill gone these labels are the only transition info on
                // screen — sized to be glanceable mid-run (next > last). Space Grotesk,
                // not the mono: these times are STATIC labels (a boundary's timestamp,
                // not a counting clock), so the mono's fixed-advance colon just reads
                // as "5 : 00".
                val timeTl = measurer.measure(
                    ridgelineFmtTime(pos),
                    style = TextStyle(
                        color = color.legibleOn(overlayBg, targetLc = if (isNext) 60.0 else 45.0),
                        fontFamily = RidgelineLabelFamily,
                        fontSize = if (isNext) 14.sp else 12.sp,
                        fontWeight = if (isNext) FontWeight.SemiBold else FontWeight.Normal,
                    ),
                )
                if (!isNext) {
                    drawText( // legible-exempt: solved via legibleOn over the photo
                        timeTl,
                        topLeft = Offset(labelRight - timeTl.size.width, clampY(y - timeTl.size.height / 2f, timeTl.size.height)),
                    )
                    return
                }
                // Upcoming interval's values — what actually changes at this boundary.
                val ni = min(route.idxAt(pos + 1.0), route.count - 1)
                val ng = route.gradeIdx(ni)
                val ns = route.speedIdx(ni)
                // Proportional display face for values (mono "." gets a full cell —
                // "7 . 5"); the time above keeps the mono for stable counting width.
                val gradeTl = measurer.measure(
                    "%.1f%%".format(ng),
                    style = TextStyle(
                        color = RidgelineTheme.mutedGradeColor(ng).legibleOn(overlayBg, targetLc = 60.0),
                        fontFamily = RidgelineLabelFamily,
                        fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
                    ),
                )
                val spdTl = measurer.measure(
                    "%.1f".format(ns),
                    style = TextStyle(
                        color = RidgelineTheme.mutedSpeedColor(ns).legibleOn(overlayBg, targetLc = 60.0),
                        fontFamily = RidgelineLabelFamily,
                        fontSize = 14.sp, fontWeight = FontWeight.SemiBold,
                    ),
                )
                // Two stacked lines straddling the tick: time above, "8% 3.0" below.
                val timeY = clampY(y - timeTl.size.height - 1f, timeTl.size.height)
                drawText( // legible-exempt: solved via legibleOn over the photo
                    timeTl,
                    topLeft = Offset(labelRight - timeTl.size.width, timeY),
                )
                val rowY = clampY(y + 2f, gradeTl.size.height)
                drawText( // legible-exempt: solved via legibleOn over the photo
                    spdTl,
                    topLeft = Offset(labelRight - spdTl.size.width, rowY),
                )
                drawText( // legible-exempt: solved via legibleOn over the photo
                    gradeTl,
                    topLeft = Offset(labelRight - spdTl.size.width - 6f - gradeTl.size.width, rowY),
                )
            }
            val lastVisible = lastB > 0.5                    // the start needs no tick
            val nextVisible = nextB < route.total - 0.5      // finish has its own flag
            // If both would collide (short interval), keep the upcoming one (its
            // two-line 14sp label needs the extra clearance).
            val collide = lastVisible && nextVisible &&
                kotlin.math.abs(yOf(lastB) - yOf(nextB)) < 44f
            if (lastVisible && !collide) transitionTick(lastB, isNext = false)
            if (nextVisible) transitionTick(nextB, isNext = true)
        }
        // current-position marker
        drawCircle(RidgelineTheme.bg, radius = 7f, center = Offset(mx, yOf(md)))
        drawCircle(RidgelineTheme.accent, radius = 5f, center = Offset(mx, yOf(md)))
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

// --- Route rendering mode flags -------------------------------------------
// MONO_THREAD_ROUTE: the route is a single ivory thread (mono, themed); grade
// information lives in the muted chips/strip markers. Flip false to restore the
// classic grade-color-ramp route segments.
private const val MONO_THREAD_ROUTE = true

// Background contour texture: dropped from the mono look (the quiet thread +
// muted markers carry the map alone); flip to bring the topo texture back.
private const val SHOW_CONTOURS = false






// Cached trail-scrim paint (see-through mode). Rebuilt only when density changes.
private var trailScrimPaintCache: android.graphics.Paint? = null
private var trailScrimPaintDp: Float = 0f
private fun trailScrimPaint(dp: Float): android.graphics.Paint {
    val cached = trailScrimPaintCache
    if (cached != null && trailScrimPaintDp == dp) return cached
    val p = android.graphics.Paint().apply {
        isAntiAlias = true
        style = android.graphics.Paint.Style.STROKE
        strokeWidth = 26f * dp
        strokeCap = android.graphics.Paint.Cap.ROUND
        strokeJoin = android.graphics.Paint.Join.ROUND
        color = android.graphics.Color.argb(165, 5, 8, 10)
        maskFilter = android.graphics.BlurMaskFilter(8f * dp, android.graphics.BlurMaskFilter.Blur.NORMAL)
    }
    trailScrimPaintCache = p
    trailScrimPaintDp = dp
    return p
}
