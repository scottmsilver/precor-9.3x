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
import androidx.compose.ui.graphics.SolidColor
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

// Route-position (planned seconds) shown at once. 10 minutes per screen: programs
// beyond ~11min WINDOW — the camera pans as you progress and the minimap's viewport
// box + leader lines activate — while shorter programs fit whole and always FILL the
// panel. (Was 25min, then 15; the switchbacks read squashed with more in view.)
internal const val POS_WINDOW = 600.0

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
    // Every chip measures two short strings per frame and the map can hold ~40 chips.
    // The default cache is 8 entries, so the whole set would miss on every frame and
    // re-run text layout inside the draw pass; size it past the worst case instead.
    val measurer = rememberTextMeasurer(cacheSize = 128)

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
internal fun computeTargetLo(route: RidgelineRoute, markerPos: Double): Double {
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
    // Path geometry lives in a plain object so a test can reproduce the exact frame the
    // user sees (RidgelineLabelStabilityTest walks a real program through it).
    val geom = RidgelineGeometry(route, centerX, ampBase, camLo, EW, topY, botY)
    fun screenY(pos: Double): Float = geom.screenY(pos)
    fun worldX(pos: Double): Float = geom.worldX(pos)

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
            // Organic top edge: when the window cuts the route, the thread (and its
            // scrim) dissolves over a short ~44dp run-out right at the cut instead of
            // stopping at a straight line. When the whole route fits, the real
            // endpoint stays crisp.
            val cutTop = camHi < route.total - 0.5
            val fade = 44f * dp
            fun fadeBrush(c: Color): Brush = if (!cutTop) SolidColor(c) else Brush.verticalGradient(
                0f to c.copy(alpha = 0f), 1f to c,
                startY = topY, endY = topY + fade,
            )
            if (SEE_THROUGH_MAP) {
                // localized scrim: a soft blurred dark band under the trail is the
                // ONLY thing dimming the photo along the route (Paint cached — the
                // pulse animation redraws every frame; codex review)
                drawIntoCanvas { c ->
                    val paint = if (cutTop) trailScrimSlot.get(dp, topY, topY + fade, 165)
                    else trailScrimSlot.get(dp, Float.NaN, Float.NaN, 165)
                    c.nativeCanvas.drawPath(thread(0, last).asAndroidPath(), paint)
                }
            }
            // soft shadow under the whole thread
            drawPath(thread(0, last), fadeBrush(Color.Black), alpha = 0.40f,
                style = Stroke(width = 6.2f * dp, cap = StrokeCap.Round, join = StrokeJoin.Round))
            // travelled: dim; ahead: bright ivory
            drawPath(thread(0, sp), fadeBrush(RidgelineTheme.fg), alpha = 0.38f,
                style = Stroke(width = 3.4f * dp, cap = StrokeCap.Round, join = StrokeJoin.Round))
            if (sp < last) drawPath(thread(sp, last), fadeBrush(RidgelineTheme.fg), alpha = 0.95f,
                style = Stroke(width = 3.4f * dp, cap = StrokeCap.Round, join = StrokeJoin.Round))
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
        // Bend toward the map's horizontal center (a fixed leftward hook parked the
        // dashes under the Home chip / timer stack).
        val tipY = topY - 40f * dp
        val ghost = Path().apply {
            moveTo(tx, topY)
            lineTo(tx + (centerX - tx) * 0.45f, topY - 22f * dp)
            lineTo(tx + (centerX - tx) * 0.9f, tipY)
        }
        // The dashes (and their scrim) dissolve toward the tip — a hard dash-end
        // with a dark backing read as a squared-off object floating in the sky.
        if (SEE_THROUGH_MAP) {
            // same localized scrim as the trail — without it the dashes vanished
            // into bright sky and the "path continues" cue was unreadable
            drawIntoCanvas { c ->
                c.nativeCanvas.drawPath(
                    ghost.asAndroidPath(),
                    ghostScrimSlot.get(dp, tipY - 10f * dp, topY + 14f * dp, 120),
                )
            }
        }
        val ghostColor = if (SEE_THROUGH_MAP) RidgelineTheme.fg.copy(alpha = 0.55f) else RidgelineTheme.dim2
        drawPath(
            ghost,
            brush = Brush.verticalGradient(
                0f to ghostColor.copy(alpha = 0f), 1f to ghostColor,
                startY = tipY - 10f * dp, endY = topY + 14f * dp,
            ),
            style = Stroke(
                width = 3f * dp,
                cap = StrokeCap.Round,
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(2f * dp, 8f * dp)),
            ),
        )
    }

    // --- grade chips at interval boundaries ---
    // Transition labels are STICKY: a chip that is on screen stays on screen until it
    // scrolls out of the camera window. Collisions (the position marker, the metrics
    // pill, another chip) MOVE a chip — side flip first, then a vertical nudge with a
    // leader line back to the bend — they never delete it. The old rule dropped any
    // blocked chip, and since the marker carries its own 40px guard rect that meant the
    // label of the bend you were running toward popped out exactly as you reached it,
    // then never returned (the `md - 15s` cull finished it off). A label that sits a few
    // px off its bend is strictly better than one that vanishes mid-run.
    val mPos = Offset(worldX(md), screenY(md))
    val pillMargin = 12f * dp
    fun inflated(r: Rect?): Rect? = r?.let {
        Rect(it.left - pillMargin, it.top - pillMargin, it.right + pillMargin, it.bottom + pillMargin)
    }
    val metricsGuard = inflated(metricsPillRect)
    // (boundary sec, anchor point drawn, grade) for the path-vs-chip validation log.
    val chipAnchors = ArrayList<Triple<Double, Offset, Double>>()

    // Pass 1: measure every boundary in the window (travelled ones included — a chip
    // you just ran past fades its chrome but keeps its text until it scrolls away).
    data class ChipDraw(
        val candidate: ChipCandidate,
        val pos: Offset,
        val grade: Double,
        val gradeTl: androidx.compose.ui.text.TextLayoutResult,
        val spdTl: androidx.compose.ui.text.TextLayoutResult,
        val travelled: Boolean,
    )
    val chipDraws = ArrayList<ChipDraw>()
    var i = route.idxAt(camLo)
    var chipCount = 0
    while (chipCount < 40 && i < route.count) {
        val bs = route.startOf(i)
        if (bs > camHi) break
        if (bs >= camLo) {
            val pos = Offset(worldX(bs), screenY(bs))
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
            chipDraws.add(
                ChipDraw(
                    candidate = ChipCandidate(bs, pos.x, pos.y, pillW),
                    pos = pos,
                    grade = route.gradeIdx(i),
                    gradeTl = gradeTl,
                    spdTl = spdTl,
                    travelled = bs < md,
                ),
            )
        }
        i++; chipCount++
    }

    // Pass 2: place them (pure geometry, unit-tested in RidgelineChipLayoutTest).
    // Priority order, NOT route order: what's coming up claims space first (nearest
    // bend ahead of you wins), then what you already ran through, most recent first.
    // Route order would let a transition you crossed five minutes ago crowd out the
    // climb you're about to hit.
    val byPriority = chipDraws.sortedWith(
        compareBy({ it.travelled }, { if (it.travelled) -it.candidate.key else it.candidate.key }),
    )
    val slots = layoutTransitionChips(
        candidates = byPriority.map { it.candidate },
        centerX = centerX,
        mapW = mapW,
        markerRect = Rect(mPos.x - 20f, mPos.y - 20f, mPos.x + 20f, mPos.y + 20f),
        metricsGuard = metricsGuard,
        topBound = topY,
        botBound = botY,
    ).associateBy { it.key }

    // Pass 3: draw. A chip behind the marker keeps its (legibility-solved) text but
    // drops its chrome to the travelled-trail weight, so the eye still reads forward.
    for (c in chipDraws) {
        val slot = slots[c.candidate.key] ?: continue
        val pillLeft = slot.pillLeft
        val pillTop = slot.pillTop
        val textY = pillTop + CHIP_H / 2f
        val chromeAlpha = if (c.travelled) 0.45f else 1f
        // Leader line back to the bend when the chip had to step aside.
        if (slot.offBend) {
            val edgeX = if (pillLeft > c.pos.x) pillLeft else pillLeft + c.candidate.pillW
            drawLine(
                RidgelineTheme.fg,
                start = c.pos,
                end = Offset(edgeX, textY),
                strokeWidth = 1f,
                alpha = 0.35f * chromeAlpha,
            )
        }
        drawRoundRectCompat(
            pillLeft, pillTop, c.candidate.pillW, CHIP_H, 6f,
            RidgelineTheme.pillBg.copy(alpha = RidgelineTheme.pillBg.alpha * chromeAlpha),
        )
        drawRoundRect(
            color = RidgelineTheme.mutedGradeColor(c.grade),
            topLeft = Offset(pillLeft, pillTop),
            size = Size(c.candidate.pillW, CHIP_H),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(6f, 6f),
            style = Stroke(width = 1f),
            alpha = chromeAlpha,
        )
        // Anchor dot sits EXACTLY on the bend (pos = the boundary's path point) — not
        // offset toward the pill, which visually parked it on whichever segment happened
        // to pass 22px to the side. Fixed ivory over a dark ring: a grade-colored dot
        // camouflaged against the very segment it marks.
        drawCircle(RidgelineTheme.bg, radius = 5.5f, center = c.pos, alpha = chromeAlpha)
        drawCircle(RidgelineTheme.fg, radius = 3.5f, center = c.pos, alpha = chromeAlpha)
        chipAnchors.add(Triple(c.candidate.key, c.pos, c.grade))
        drawText( // legible-exempt: solved via legibleOn over the photo
            c.gradeTl,
            topLeft = Offset(pillLeft + 7f, textY - c.gradeTl.size.height / 2f),
        )
        drawText( // legible-exempt: solved via legibleOn over the photo
            c.spdTl,
            topLeft = Offset(pillLeft + 7f + c.gradeTl.size.width + 6f, textY - c.spdTl.size.height / 2f),
        )
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
        // (no finish flag on the strip — the strip's top edge IS the finish, and
        // the main map already flies the flag)
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
            /**
             * Draws a tick + its label; returns the label block's bottom edge so the
             * next caller can dodge it. [avoidBelow] pushes the label down past an
             * already-drawn block — the two never trade places (last is always below
             * next on the strip), so this can't loop.
             */
            fun transitionTick(
                pos: Double,
                isNext: Boolean,
                avoidBelow: Float = Float.NEGATIVE_INFINITY,
            ): Float {
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
                    val tlY = clampY(
                        max(y - timeTl.size.height / 2f, avoidBelow),
                        timeTl.size.height,
                    )
                    // Its own little island — the label sits on bare photo left of the
                    // strip and washed out over bright water/sky without one.
                    if (SEE_THROUGH_MAP) drawRoundRectCompat(
                        labelRight - timeTl.size.width - 5f, tlY - 2f,
                        timeTl.size.width + 10f, timeTl.size.height + 4f,
                        6f, Color(0xC9070B0E),
                    )
                    drawText( // legible-exempt: solved via legibleOn over the photo
                        timeTl,
                        topLeft = Offset(labelRight - timeTl.size.width, tlY),
                    )
                    return tlY + timeTl.size.height
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
                val rowY = clampY(y + 2f, gradeTl.size.height)
                // One island behind the whole two-line block (same washout fix as above).
                if (SEE_THROUGH_MAP) {
                    val rowW = spdTl.size.width + 6f + gradeTl.size.width
                    val blockW = max(timeTl.size.width.toFloat(), rowW)
                    drawRoundRectCompat(
                        labelRight - blockW - 6f, timeY - 3f,
                        blockW + 11f, (rowY + gradeTl.size.height + 3f) - (timeY - 3f),
                        7f, Color(0xC9070B0E),
                    )
                }
                drawText( // legible-exempt: solved via legibleOn over the photo
                    timeTl,
                    topLeft = Offset(labelRight - timeTl.size.width, timeY),
                )
                drawText( // legible-exempt: solved via legibleOn over the photo
                    spdTl,
                    topLeft = Offset(labelRight - spdTl.size.width, rowY),
                )
                drawText( // legible-exempt: solved via legibleOn over the photo
                    gradeTl,
                    topLeft = Offset(labelRight - spdTl.size.width - 6f - gradeTl.size.width, rowY),
                )
                return rowY + gradeTl.size.height
            }
            val lastVisible = lastB > 0.5                    // the start needs no tick
            val nextVisible = nextB < route.total - 0.5      // finish has its own flag
            // Upcoming transition first (it owns its spot), then the one just crossed
            // pushed clear of it. On a short interval these two used to collide and the
            // "last" label was simply deleted — so it appeared, then vanished as soon as
            // you stepped into a brief interval. Now it slides down instead.
            val nextBottom = if (nextVisible) transitionTick(nextB, isNext = true)
            else Float.NEGATIVE_INFINITY
            if (lastVisible) {
                transitionTick(lastB, isNext = false, avoidBelow = nextBottom + 5f)
            }
        }
        // current-position marker
        drawCircle(RidgelineTheme.bg, radius = 7f, center = Offset(mx, yOf(md)))
        drawCircle(RidgelineTheme.accent, radius = 5f, center = Offset(mx, yOf(md)))
    }
}

/**
 * The map's path geometry for one frame: route position → screen point. Extracted from
 * the draw pass so tests can replay a real program through the exact same math.
 *
 * Organic detail (amplitude jitter, phase wobble, grade smoothing) is tuned in the
 * design's MILE domain — it is fed planned miles via distAt so the look is preserved.
 * Grade smoothing keeps its ±0.05mi reach by converting through the LOCAL interval
 * speed (0.05mi = 180/speed seconds), not a fixed time radius (codex review).
 */
internal class RidgelineGeometry(
    private val route: RidgelineRoute,
    private val centerX: Float,
    private val ampBase: Float,
    val camLo: Double,
    val ew: Double,
    private val topY: Float,
    private val botY: Float,
) {
    val camHi: Double get() = camLo + ew

    fun screenY(pos: Double): Float = (botY - ((pos - camLo) / ew) * (botY - topY)).toFloat()

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
}

// --- transition chip placement (pure geometry) -----------------------------
// Split out of the draw pass so the "labels never disappear" property is a unit
// test (RidgelineChipLayoutTest) rather than something you can only catch by
// staring at a moving treadmill.

/** Chip pill height, px. */
internal const val CHIP_H = 24f

/** Vertical nudge granularity and reach when a chip has to step aside, px. */
private const val CHIP_NUDGE_STEP = 7f
private const val CHIP_NUDGE_MAX = 84f

/**
 * Displacements a blocked chip may try, nearest-first. Vertical alone isn't enough: a
 * chip pinned under the metrics pill can only escape downward, and a few px of the
 * marker's guard rect is enough to seal that one exit — so it needs to be able to step
 * sideways too. Cost weights sideways movement a little cheaper than vertical because
 * the map is tall and narrow, and the ordering means an unobstructed chip still lands
 * exactly on its bend (0,0 is first).
 */
private val CHIP_OFFSETS: List<Pair<Float, Float>> = buildList {
    // The long reaches matter for one real case: a bend that falls BEHIND the metrics
    // stack in the top-left corner. There is no nearby free space at all, so the label
    // slides out to the right of the pill — same height on the map, so it still reads as
    // that bend's elevation — with a leader line home. Sideways is costed below vertical
    // for exactly that reason: the map's vertical axis is meaningful, its width is slack.
    val dxs = listOf(0f, -20f, 20f, -40f, 40f, -70f, 70f, -110f, 110f, -170f, 170f, -250f, 250f, -340f, 340f)
    var s = 0
    while (s * CHIP_NUDGE_STEP <= CHIP_NUDGE_MAX) {
        val dys = if (s == 0) listOf(0f) else listOf(-s * CHIP_NUDGE_STEP, s * CHIP_NUDGE_STEP)
        for (dy in dys) for (dx in dxs) add(dx to dy)
        s++
    }
}.sortedBy { (dx, dy) -> kotlin.math.abs(dy) + 0.45f * kotlin.math.abs(dx) }

/** A transition chip wanting a spot: its bend on the path plus the pill's width. */
internal data class ChipCandidate(
    val key: Double,
    val anchorX: Float,
    val anchorY: Float,
    val pillW: Float,
)

/** Where a chip's pill ended up. [offBend] means it stepped aside — draw a leader line. */
internal data class ChipSlot(
    val key: Double,
    val pillLeft: Float,
    val pillTop: Float,
    val dx: Float,
    val dy: Float,
) {
    val offBend: Boolean get() = dx != 0f || dy != 0f
}

/**
 * Place transition chips so they never vanish while you run.
 *
 * Each candidate takes the natural side of its bend, flips to the other side if that
 * collides, then walks progressively larger vertical nudges (alternating up/down) until
 * it finds clear space inside [topBound]..[botBound]. Candidates are placed in route
 * order, so an earlier bend keeps its spot and later ones move — placement is a pure
 * function of the frame's geometry, so it doesn't jitter between frames.
 *
 * A candidate is dropped only if nothing within ±[CHIP_NUDGE_MAX] fits on the canvas.
 */
internal fun layoutTransitionChips(
    candidates: List<ChipCandidate>,
    centerX: Float,
    mapW: Float,
    markerRect: Rect,
    metricsGuard: Rect?,
    topBound: Float,
    botBound: Float,
): List<ChipSlot> {
    val placed = ArrayList<Rect>(candidates.size + 1)
    placed.add(markerRect)
    val out = ArrayList<ChipSlot>(candidates.size)

    for (c in candidates) {
        fun pillLeftFor(side: Int, dx: Float): Float {
            val cx0 = c.anchorX + side * 22f
            return ((if (side < 0) cx0 - c.pillW else cx0) + dx)
                .coerceIn(4f, max(4f, mapW - c.pillW - 4f))
        }
        // Clamp onto the canvas rather than rejecting: during a camera pan a cluster of
        // bends crowds the top edge, and rejecting every out-of-bounds spot left those
        // chips with nowhere to go for a few frames — a visible flicker. Clamped chips
        // pile up at the edge and then slide apart horizontally.
        fun topFor(dy: Float): Float = (c.anchorY - CHIP_H / 2f + dy)
            .coerceIn(topBound, max(topBound, botBound - CHIP_H))
        // The anchor dot joins the footprint only while the pill is ON the bend; a
        // displaced pill is tied back by a hairline leader instead, and reserving the
        // whole span would keep it colliding with the very thing it stepped away from.
        fun leftFor(pl: Float, onBend: Boolean) = if (onBend) min(c.anchorX - 5f, pl) else pl
        fun rightFor(pl: Float, onBend: Boolean) =
            if (onBend) max(c.anchorX + 5f, pl + c.pillW) else pl + c.pillW
        // This chip can only ever land inside this vertical band, so only already-placed
        // rects overlapping the band can possibly collide with it. Filtering once per
        // chip keeps the worst case sane: the offset search is ~750 probes, and scanning
        // every placed rect on each probe would put a five-figure comparison count inside
        // a 60fps draw pass.
        val lo = max(topBound, botBound - CHIP_H)
        val reachTop = (c.anchorY - CHIP_H / 2f - CHIP_NUDGE_MAX).coerceIn(topBound, lo) - 4f
        val reachBot = (c.anchorY - CHIP_H / 2f + CHIP_NUDGE_MAX).coerceIn(topBound, lo) + CHIP_H + 4f
        val nearby = placed.filter { it.bottom >= reachTop && it.top <= reachBot }

        // Probing is allocation-free (plain float bounds, no Rect per attempt): the search
        // can run hundreds of probes per chip and this runs inside the draw pass, so a
        // Rect per probe would be real GC pressure at 60fps. Only the winner allocates.
        fun usable(l: Float, t: Float, r: Float, b: Float): Boolean {
            val il = l - 4f; val it = t - 4f; val ir = r + 4f; val ib = b + 4f
            val g = metricsGuard
            if (g != null && g.left < ir && il < g.right && g.top < ib && it < g.bottom) return false
            for (p in nearby) {
                if (p.left < ir && il < p.right && p.top < ib && it < p.bottom) return false
            }
            return true
        }

        val natural = if (c.anchorX < centerX) -1 else 1
        val sides = intArrayOf(natural, -natural)
        var slot: ChipSlot? = null
        search@ for ((dx, dy) in CHIP_OFFSETS) {
            val onBend = dx == 0f && dy == 0f
            val top = topFor(dy)
            for (side in sides) {
                val pl = pillLeftFor(side, dx)
                val l = leftFor(pl, onBend)
                val r = rightFor(pl, onBend)
                if (usable(l, top, r, top + CHIP_H)) {
                    placed.add(Rect(l, top, r, top + CHIP_H))
                    slot = ChipSlot(c.key, pl, top, dx, dy)
                    break@search
                }
            }
        }
        slot?.let { out.add(it) }
    }
    return out
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






// Cached scrim paints (see-through mode), one slot per drawing (trail, ghost) so
// the two callers don't thrash each other's cache every frame. Rebuilt only when
// density or the fade anchors change. The vertical shader fades the scrim from
// transparent at `fadeTopY` to `baseAlpha` at `fadeBotY` (pass NaN for no fade).
private class ScrimPaintSlot {
    private var paint: android.graphics.Paint? = null
    private var dp = 0f
    private var fadeTopY = 0f
    private var fadeBotY = 0f

    fun get(dp: Float, fadeTopY: Float, fadeBotY: Float, baseAlpha: Int): android.graphics.Paint {
        val cached = paint
        if (cached != null && this.dp == dp &&
            this.fadeTopY.toRawBits() == fadeTopY.toRawBits() &&
            this.fadeBotY.toRawBits() == fadeBotY.toRawBits()
        ) return cached
        val p = android.graphics.Paint().apply {
            isAntiAlias = true
            style = android.graphics.Paint.Style.STROKE
            strokeWidth = 26f * dp
            strokeCap = android.graphics.Paint.Cap.ROUND
            strokeJoin = android.graphics.Paint.Join.ROUND
            color = android.graphics.Color.argb(baseAlpha, 5, 8, 10)
            maskFilter = android.graphics.BlurMaskFilter(8f * dp, android.graphics.BlurMaskFilter.Blur.NORMAL)
            if (!fadeTopY.isNaN()) {
                shader = android.graphics.LinearGradient(
                    0f, fadeTopY, 0f, fadeBotY,
                    android.graphics.Color.argb(0, 5, 8, 10),
                    android.graphics.Color.argb(baseAlpha, 5, 8, 10),
                    android.graphics.Shader.TileMode.CLAMP,
                )
            }
        }
        paint = p
        this.dp = dp
        this.fadeTopY = fadeTopY
        this.fadeBotY = fadeBotY
        return p
    }
}

private val trailScrimSlot = ScrimPaintSlot()
private val ghostScrimSlot = ScrimPaintSlot()
