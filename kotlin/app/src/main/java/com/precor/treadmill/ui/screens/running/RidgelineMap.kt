package com.precor.treadmill.ui.screens.running

import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.theme.LocalOverlayBackground
import com.precor.treadmill.ui.theme.legibleOn
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToInt
import kotlin.math.sin

// --- Route model (finite, POSITION domain = planned program seconds) ---------
// Ported from makeRoute() in DirectionD.jsx (the finite/intervals branch), then
// re-domained: layout position is program TIME, so every interval gets screen
// length proportional to how long you actually run it, and interval boundaries
// (where the incline changes) are structurally the program's own clock — they
// can never drift from the route bends. A distance-domain layout let a 25s
// sprint own 68% of the track while a 75s recovery collapsed to a sliver.
// Distance-domain detail (vert feet, amplitude jitter) is still integrated over
// PLANNED MILES internally. The switchback phase is NOT: it accumulates per second
// as a function of grade alone, because zigzag density is the map's only steepness
// signal and it must not be diluted by how fast you happen to be running.

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
     * Grade at [pos] softened across interval bends, so switchback width eases into
     * a new pitch without averaging a short pitch away through its midpoint.
     */
    fun smoothedGradeAt(pos: Double): Double {
        val p = pos.coerceIn(0.0, total)
        val i = idxAt(p)
        val g = gradeIdx(i)
        val reach = ((endOf(i) - startOf(i)) * SMOOTH_FRAC)
            .coerceIn(SMOOTH_MIN_SEC, SMOOTH_MAX_SEC)
        val wIn = ((reach - (p - startOf(i))) / (2.0 * reach)).coerceIn(0.0, 0.5)
        val wOut = ((reach - (endOf(i) - p)) / (2.0 * reach)).coerceIn(0.0, 0.5)
        val prev = if (i > 0) gradeIdx(i - 1) else g
        val next = if (i < count - 1) gradeIdx(i + 1) else g
        return g * (1.0 - wIn - wOut) + prev * wIn + next * wOut
    }

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

    // Switchback phase accumulates per second from grade alone. Small bounded jitter
    // keeps the line organic without letting speed or interval position dominate the
    // steepness signal.
    fun phaseAt(pos: Double): Double {
        val p = pos.coerceIn(0.0, total)
        var ph = 0.0
        var organicPhase = 0.0
        var i = 0
        while (i < count && cum[i] < p - 1e-9) {
            val end = min(endOf(i), p)
            val elapsed = end - cum[i]
            val duration = endOf(i) - startOf(i)
            // The wobble returns to zero at both interval boundaries, so it shapes
            // the line locally without changing the interval's grade-only net rate.
            if (elapsed < duration - 1e-9) {
                val rate = turnRate(gradeIdx(i))
                // Scaling amplitude by duration*rate/pi makes the wobble derivative
                // exactly (jitter-1)*rate*cos(...): at most ±8% of forward motion.
                organicPhase = (jitter(i) - 1.0) * duration * rate / PI *
                    sin(PI * elapsed / duration)
            }
            ph += elapsed * turnRate(gradeIdx(i))
            i++
        }
        // The minimum-route-sweep scale applies only to grade-derived phase. Keeping
        // the bounded wobble outside prevents a short route from amplifying it into
        // multi-radian reversals.
        return ph * phaseScale + organicPhase
    }

    // Short routes accumulate almost no natural phase (a 2-min program is ~0.1mi),
    // which would draw as a straight stub. Scale the whole route's phase up to a
    // floor of ~2.2π so even tiny routes sweep at least one full S-curve; long
    // routes (natural phase ≥ the floor) are untouched.
    private val phaseScale: Double

    init {
        var raw = 0.0
        for (i in 0 until count) raw +=
            (cum[i + 1] - cum[i]) * turnRate(gradeIdx(i))
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
        // Minimum total switchback sweep for the whole route (~2.2π ≈ one full S).
        const val MIN_TOTAL_PHASE = 7.0
        const val GRADE_REF = 15.0
        const val TURN_RATE_FLAT = 0.008
        const val TURN_RATE_GAIN = 0.040
        const val SMOOTH_FRAC = 0.18
        const val SMOOTH_MIN_SEC = 2.0
        const val SMOOTH_MAX_SEC = 20.0

        fun turnRate(gradePct: Double): Double =
            TURN_RATE_FLAT + TURN_RATE_GAIN *
                (gradePct.coerceIn(0.0, GRADE_REF) / GRADE_REF)

        /** Bounded organic coefficient used by a zero-net phase wobble per interval. */
        fun jitter(i: Int): Double = 1.0 + 0.08 * sin(i * 5.13 + 1.7)
    }
}

/** A measured text payload plus the width consumed by that payload. */
internal data class MeasuredTransitionText<T>(val value: T, val width: Float)

/** Stable, route-derived label content prepared outside the animated draw callback. */
internal data class PreparedTransitionLabel<T>(
    val intervalIndex: Int,
    val key: Double,
    val gradeValue: Double,
    val gradeText: String,
    val speedText: String,
    val gradeColor: Color,
    val speedColor: Color,
    val grade: MeasuredTransitionText<T>,
    val speed: MeasuredTransitionText<T>,
    val gradeOffset: Float,
    val speedOffset: Float,
    val pillW: Float,
)

private class BoundedLru<K, V>(private val capacity: Int) {
    private val values = LinkedHashMap<K, V>(capacity, 0.75f, true)

    fun getOrPut(key: K, create: () -> V): V {
        values[key]?.let { return it }
        return create().also {
            values[key] = it
            if (values.size > capacity) values.remove(values.entries.iterator().next().key)
        }
    }
}

private data class TransitionTextKey(val text: String, val color: Color, val maxWidthBits: Int?)

/** Cheap route content plus bounded, viewport-driven measured-label caches. */
internal class TransitionLabelModel<T>(
    val route: RidgelineRoute,
    private val maxPillWidth: Float,
    private val contents: List<TransitionLabelContent>,
    private val measure: (text: String, color: Color, maxWidth: Float?) -> MeasuredTransitionText<T>,
) {
    private val measuredText = BoundedLru<TransitionTextKey, MeasuredTransitionText<T>>(256)
    private val preparedLabels = BoundedLru<Int, PreparedTransitionLabel<T>>(96)

    val labels: List<PreparedTransitionLabel<T>> = object : AbstractList<PreparedTransitionLabel<T>>() {
        override val size: Int get() = route.count
        override fun get(index: Int): PreparedTransitionLabel<T> = labelAt(index)
    }

    private fun measured(text: String, color: Color, maxWidth: Float?): MeasuredTransitionText<T> {
        val key = TransitionTextKey(text, color, maxWidth?.toRawBits())
        return measuredText.getOrPut(key) { measure(text, color, maxWidth) }
    }

    private fun labelAt(i: Int): PreparedTransitionLabel<T> = preparedLabels.getOrPut(i) {
        val content = contents[i]
        val safePillWidth = max(1f, maxPillWidth)
        val sidePadding = min(7f, safePillWidth / 4f)
        val gap = min(6f, max(0f, safePillWidth - sidePadding * 2f))
        val contentWidth = max(0f, safePillWidth - sidePadding * 2f - gap)
        val naturalGrade = measured(content.gradeText, content.gradeColor, null)
        val naturalSpeed = measured(content.speedText, content.speedColor, null)
        val naturalTotal = naturalGrade.width + naturalSpeed.width
        val (grade, speed) = if (naturalTotal <= contentWidth) {
            naturalGrade to naturalSpeed
        } else {
            val pixelBudget = floor(contentWidth).toInt().coerceAtLeast(0)
            val gradePixels = if (naturalTotal <= 0f) pixelBudget / 2 else
                floor(pixelBudget * naturalGrade.width / naturalTotal).toInt().coerceIn(0, pixelBudget)
            measured(content.gradeText, content.gradeColor, gradePixels.toFloat()) to
                measured(content.speedText, content.speedColor, (pixelBudget - gradePixels).toFloat())
        }
        val gradeOffset = sidePadding
        val speedOffset = gradeOffset + grade.width + gap
        PreparedTransitionLabel(
            intervalIndex = i,
            key = route.startOf(i),
            gradeValue = content.gradeValue,
            gradeText = content.gradeText,
            speedText = content.speedText,
            gradeColor = content.gradeColor,
            speedColor = content.speedColor,
            grade = grade,
            speed = speed,
            gradeOffset = gradeOffset,
            speedOffset = speedOffset,
            pillW = min(safePillWidth, speedOffset + speed.width + sidePadding),
        )
    }
}

internal data class TransitionLabelContent(
    val gradeValue: Double,
    val gradeText: String,
    val speedText: String,
    val gradeColor: Color,
    val speedColor: Color,
)

/**
 * Prepare immutable strings/colors cheaply; measured labels are loaded by viewport and
 * retained in a 96-label LRU (enough for the server-contract 61-label maximum window).
 * Individual layouts are interned in a 256-entry route/style-scoped LRU.
 * [measure] is generic so JVM tests exercise this exact pipeline with representative
 * widths while production supplies cached [androidx.compose.ui.text.TextLayoutResult]s.
 */
internal fun <T> prepareTransitionLabelModel(
    route: RidgelineRoute,
    maxPillWidth: Float,
    gradeColorFor: (intervalIndex: Int) -> Color,
    speedColorFor: (intervalIndex: Int) -> Color,
    measure: (text: String, color: Color, maxWidth: Float?) -> MeasuredTransitionText<T>,
): TransitionLabelModel<T> {
    val contents = ArrayList<TransitionLabelContent>(route.count)
    for (i in 0 until route.count) {
        contents.add(
            TransitionLabelContent(
                route.gradeIdx(i), "%.1f%%".format(route.gradeIdx(i)), "%.1f".format(route.speedIdx(i)),
                gradeColorFor(i), speedColorFor(i),
            ),
        )
    }
    return TransitionLabelModel(route, maxPillWidth, contents, measure)
}

internal enum class RidgelineStaticLabelKind { FINISH, LAST_TIME, NEXT_TIME, GRADE, SPEED }
private data class StaticTextKey(val kind: RidgelineStaticLabelKind, val text: String, val color: Color)

internal data class PreparedTransitionTick<T>(
    val lastTime: T,
    val nextTime: T,
    val grade: T,
    val speed: T,
)

internal data class StaticTransitionContent(
    val time: String,
    val grade: String,
    val speed: String,
    val gradeColor: Color,
    val speedColor: Color,
)

/** Lazily measures only finish and the current last/next minimap transition. */
internal class PreparedRidgelineStaticLabels<T>(
    private val finishLoader: () -> T,
    private val contents: List<StaticTransitionContent>,
    private val lastTimeColor: Color,
    private val nextTimeColor: Color,
    private val measure: (kind: RidgelineStaticLabelKind, text: String, color: Color) -> T,
) {
    private var preparedFinish: T? = null
    private val ticks = BoundedLru<Int, PreparedTransitionTick<T>>(4)
    private val measuredText = BoundedLru<StaticTextKey, T>(64)
    private fun measured(kind: RidgelineStaticLabelKind, text: String, color: Color): T =
        measuredText.getOrPut(StaticTextKey(kind, text, color)) { measure(kind, text, color) }
    val finish: T get() = preparedFinish ?: finishLoader().also { preparedFinish = it }
    val transitions: List<PreparedTransitionTick<T>> = object : AbstractList<PreparedTransitionTick<T>>() {
        override val size: Int get() = contents.size
        override fun get(index: Int): PreparedTransitionTick<T> = ticks.getOrPut(index) {
            val c = contents[index]
            PreparedTransitionTick(
                measured(RidgelineStaticLabelKind.LAST_TIME, c.time, lastTimeColor),
                measured(RidgelineStaticLabelKind.NEXT_TIME, c.time, nextTimeColor),
                measured(RidgelineStaticLabelKind.GRADE, c.grade, c.gradeColor),
                measured(RidgelineStaticLabelKind.SPEED, c.speed, c.speedColor),
            )
        }
    }
}

internal fun <T> prepareRidgelineStaticLabels(
    route: RidgelineRoute,
    finishColor: Color,
    lastTimeColor: Color,
    nextTimeColor: Color,
    gradeColorFor: (intervalIndex: Int) -> Color,
    speedColorFor: (intervalIndex: Int) -> Color,
    measure: (kind: RidgelineStaticLabelKind, text: String, color: Color) -> T,
): PreparedRidgelineStaticLabels<T> {
    val contents = ArrayList<StaticTransitionContent>(route.count)
    for (i in 0 until route.count) {
        contents.add(
            StaticTransitionContent(
                ridgelineFmtTime(route.startOf(i)),
                "%.1f%%".format(route.gradeIdx(i)),
                "%.1f".format(route.speedIdx(i)),
                gradeColorFor(i), speedColorFor(i),
            ),
        )
    }
    return PreparedRidgelineStaticLabels(
        finishLoader = {
            measure(
                RidgelineStaticLabelKind.FINISH,
                "FINISH · ${"%,d".format(Math.round(route.vertAt(route.total)))} ft",
                finishColor,
            )
        },
        contents = contents,
        lastTimeColor = lastTimeColor,
        nextTimeColor = nextTimeColor,
        measure = measure,
    )
}

// Route-position (planned seconds) shown at once. 10 minutes per screen: programs
// beyond ~11min WINDOW — the camera pans as you progress and the minimap's viewport
// box + leader lines activate — while shorter programs fit whole and always FILL the
// panel. (Was 25min, then 15; the switchbacks read squashed with more in view.)
internal const val POS_WINDOW = 600.0

/** Interior route boundaries in (camLo, camHi), scanning only the visible interval range. */
internal fun visibleInteriorBoundaries(route: RidgelineRoute, camLo: Double, camHi: Double): List<Double> {
    val out = ArrayList<Double>()
    var i = max(1, route.idxAt(camLo))
    while (i < route.count && route.startOf(i) <= camLo) i++
    while (i < route.count) {
        val boundary = route.startOf(i)
        if (boundary >= camHi) break
        out.add(boundary)
        i++
    }
    return out
}

private fun lerp(a: Double, b: Double, t: Double) = a + (b - a) * t

private const val AMP_FLAT = 1.00f
private const val AMP_STEEP = 0.35f
private const val STEEP_STOPS = 64
private const val STEEP_TINT_MAX = 0.92f
private const val STEEP_GLOW_ALPHA = 0.5f

/** Monotonic switchback width, clamped to the treadmill's practical grade range. */
internal fun switchbackAmpFactor(gradePct: Double): Float {
    val t = (gradePct.coerceIn(0.0, RidgelineRoute.GRADE_REF) /
        RidgelineRoute.GRADE_REF).toFloat()
    return AMP_FLAT + (AMP_STEEP - AMP_FLAT) * t
}

/** Width left for route geometry after the right-side strip and its margins. */
private fun ridgelineMapWidth(canvasWidth: Float, density: Float): Float =
    max(1f, canvasWidth - 42f * density)

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
    // All layouts are retained by route-scoped models below, rather than an LRU that a
    // valid 601-transition route could churn during animated draw frames.
    val measurer = rememberTextMeasurer(cacheSize = 0)

    // Pulsing position-ring: radius 8->19->8 and opacity 0.5->0->0.5 over 2.4s, infinite.
    // Two independent reversing tweens; both values are READ inside the draw lambda below
    // so Compose redraws the Canvas every frame.
    val pulseT = rememberInfiniteTransition(label = "marker-pulse")
    val pulseR = pulseT.animateFloat(
        initialValue = 8f,
        targetValue = 19f,
        animationSpec = infiniteRepeatable(tween(2400), RepeatMode.Reverse),
        label = "pulse-r",
    )
    val pulseA = pulseT.animateFloat(
        initialValue = 0.5f,
        targetValue = 0f,
        animationSpec = infiniteRepeatable(tween(2400), RepeatMode.Reverse),
        label = "pulse-a",
    )

    // Eased CAMERA PAN: the view window's lower bound eases to its target (the same
    // page/lead step the design computes) with an easeInOutCubic over ~1s, so the colored
    // map glides instead of snapping when progress nears the top edge.
    val targetLo = remember(route, markerPos) { computeTargetLo(route, markerPos) }
    val elevLo = animateFloatAsState(
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

    BoxWithConstraints(modifier = modifier) {
        val density = LocalDensity.current
        val canvasWidthPx = with(density) { maxWidth.toPx() }
        val preparedLabels = remember(route, overlayBg, measurer, canvasWidthPx) {
            prepareTransitionLabelModel(
                route = route,
                maxPillWidth = max(1f, ridgelineMapWidth(canvasWidthPx, density.density) - 8f),
                gradeColorFor = { i ->
                    RidgelineTheme.mutedGradeColor(route.gradeIdx(i)).legibleOn(overlayBg, targetLc = 60.0)
                },
                speedColorFor = { i ->
                    RidgelineTheme.mutedSpeedColor(route.speedIdx(i)).legibleOn(overlayBg, targetLc = 60.0)
                },
                measure = { text, color, maxWidth ->
                    val constraints = maxWidth?.let { Constraints(maxWidth = max(0, it.roundToInt())) }
                        ?: Constraints()
                    val layout = measurer.measure(
                        text,
                        style = TextStyle(
                            color = color,
                            fontFamily = RidgelineLabelFamily,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.SemiBold,
                        ),
                        overflow = TextOverflow.Ellipsis,
                        softWrap = false,
                        maxLines = 1,
                        constraints = constraints,
                    )
                    MeasuredTransitionText(layout, layout.size.width.toFloat())
                },
            )
        }
        val staticLabels = remember(route, overlayBg, measurer) {
            prepareRidgelineStaticLabels(
                route = route,
                finishColor = RidgelineTheme.elev.legibleOn(overlayBg, targetLc = 60.0),
                lastTimeColor = RidgelineTheme.dim.legibleOn(overlayBg, targetLc = 45.0),
                nextTimeColor = RidgelineTheme.accent.legibleOn(overlayBg, targetLc = 60.0),
                gradeColorFor = { i ->
                    RidgelineTheme.mutedGradeColor(route.gradeIdx(i)).legibleOn(overlayBg, targetLc = 60.0)
                },
                speedColorFor = { i ->
                    RidgelineTheme.mutedSpeedColor(route.speedIdx(i)).legibleOn(overlayBg, targetLc = 60.0)
                },
                measure = { kind, text, color ->
                    val isLast = kind == RidgelineStaticLabelKind.LAST_TIME
                    val layout = measurer.measure(
                        text,
                        style = TextStyle(
                            color = color,
                            fontFamily = if (kind == RidgelineStaticLabelKind.FINISH)
                                RidgelineMonoFamily else RidgelineLabelFamily,
                            fontSize = if (isLast) 12.sp else 14.sp,
                            fontWeight = if (isLast) FontWeight.Normal else FontWeight.SemiBold,
                        ),
                        softWrap = false,
                        maxLines = 1,
                    )
                    layout
                },
            )
        }
        val labelFrameCache = remember(preparedLabels) {
            TransitionLabelFrameCache<androidx.compose.ui.text.TextLayoutResult>()
        }
        // Paint ownership follows the route just like the label caches. Camera/canvas
        // changes update the slot key; marker-pulse redraws reuse its shader and blur.
        val steepnessPaint = remember(route) { SteepnessPaintSlot() }
        Canvas(modifier = Modifier.fillMaxSize()) {
            // Canvas does NOT clip children by default — contours draw past the panel
            // edge (x in [-30, W+30]) and were bleeding onto neighboring UI.
            clipRect(0f, 0f, size.width, size.height) {
                drawRidgeline(
                    route, markerPos, pulseR.value, pulseA.value, elevLo.value.toDouble(),
                    metricsPillRect, preparedLabels, staticLabels, labelFrameCache,
                    steepnessPaint,
                )
            }
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
    metricsPillRect: Rect?,
    preparedLabels: TransitionLabelModel<androidx.compose.ui.text.TextLayoutResult>,
    staticLabels: PreparedRidgelineStaticLabels<androidx.compose.ui.text.TextLayoutResult>,
    labelFrameCache: TransitionLabelFrameCache<androidx.compose.ui.text.TextLayoutResult>,
    steepnessPaint: SteepnessPaintSlot,
) {
    val W = size.width
    val H = size.height
    val dp = this.density   // px per dp (DrawScope is a Density)

    // Vertical elevation strip hugs the RIGHT EDGE of the map canvas (target: flush, thin,
    // small consistent margin) rather than floating mid-gap before the stepper rail.
    val stripW = 12f * dp
    val stripX = W - 16f * dp - stripW / 2f   // strip centerline ~16dp in from the map's right edge
    val mapW = ridgelineMapWidth(W, dp)   // drawable width for the switchback path

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
    val visibleBoundaries = visibleInteriorBoundaries(route, camLo, min(camHi, route.total))
    val samplePos = ArrayList<Double>(M + visibleBoundaries.size + 2)
    for (k in 0..M) {
        val p0 = camLo + (k.toDouble() / M) * EW
        if (p0 > route.total + 1e-6) break
        samplePos.add(min(p0, route.total))
    }
    samplePos.addAll(visibleBoundaries)
    samplePos.sort()
    // Validation capture: the EXACT point the path polyline uses at each interval
    // boundary, compared against the chip anchors afterwards (logged once per route/size).
    val boundarySet = visibleBoundaries.toHashSet()
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
            // Ahead trail warms from ivory toward elevation amber with grade. The
            // matching high-grade halo adds weight without lowering photo contrast.
            steepnessPaint.update(route, camLo, EW, W, H, topY, botY, cutTop, fade, dp)
            val threadBrush = steepnessPaint.brush
            val glowPaint = steepnessPaint.glowPaint
            if (sp < last && glowPaint != null) drawIntoCanvas { canvas ->
                canvas.nativeCanvas.drawPath(thread(sp, last).asAndroidPath(), glowPaint)
            }
            // Travelled trail remains deliberately dim; ahead retains full strength.
            drawPath(thread(0, sp), threadBrush, alpha = 0.38f,
                style = Stroke(width = 3.4f * dp, cap = StrokeCap.Round, join = StrokeJoin.Round))
            if (sp < last) drawPath(thread(sp, last), threadBrush, alpha = 0.95f,
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

    // Animated frames reuse the route/size-scoped text model and only project, place,
    // and draw the visible subset.
    val markerRect = Rect(mPos.x - 20f, mPos.y - 20f, mPos.x + 20f, mPos.y + 20f)
    val labelFrame = labelFrameCache.layout(
        model = preparedLabels,
        geometry = geom,
        markerPos = md,
        centerX = centerX,
        mapW = mapW,
        markerRect = markerRect,
        metricsGuard = metricsGuard,
        topBound = topY,
        botBound = botY,
    )
    val slots = labelFrame.layout.slots.associateBy { it.key }

    // Draw. A chip behind the marker keeps its (legibility-solved) text but
    // drops its chrome to the travelled-trail weight, so the eye still reads forward.
    for (c in labelFrame.visible) {
        val slot = slots[c.candidate.key] ?: continue
        val label = c.label
        val pos = Offset(c.candidate.anchorX, c.candidate.anchorY)
        val pillLeft = slot.pillLeft
        val pillTop = slot.pillTop
        val textY = pillTop + CHIP_H / 2f
        val chromeAlpha = transitionChipAlpha(c.travelled)
        // Leader line back to the bend when the chip had to step aside.
        if (slot.offBend) {
            val edgeX = if (pillLeft > pos.x) pillLeft else pillLeft + label.pillW
            drawLine(
                RidgelineTheme.fg,
                start = pos,
                end = Offset(edgeX, textY),
                strokeWidth = 1f,
                alpha = 0.35f * chromeAlpha,
            )
        }
        drawRoundRectCompat(
            pillLeft, pillTop, label.pillW, CHIP_H, 6f,
            RidgelineTheme.pillBg.copy(alpha = RidgelineTheme.pillBg.alpha * chromeAlpha),
        )
        drawRoundRect(
            color = label.gradeColor,
            topLeft = Offset(pillLeft, pillTop),
            size = Size(label.pillW, CHIP_H),
            cornerRadius = androidx.compose.ui.geometry.CornerRadius(6f, 6f),
            style = Stroke(width = 1f),
            alpha = chromeAlpha,
        )
        // Anchor dot sits EXACTLY on the bend (pos = the boundary's path point) — not
        // offset toward the pill, which visually parked it on whichever segment happened
        // to pass 22px to the side. Fixed ivory over a dark ring: a grade-colored dot
        // camouflaged against the very segment it marks.
        drawCircle(RidgelineTheme.bg, radius = 5.5f, center = pos, alpha = chromeAlpha)
        drawCircle(RidgelineTheme.fg, radius = 3.5f, center = pos, alpha = chromeAlpha)
        chipAnchors.add(Triple(c.candidate.key, pos, label.gradeValue))
        drawText( // legible-exempt: solved via legibleOn over the photo
            label.grade.value,
            topLeft = Offset(pillLeft + label.gradeOffset, textY - label.grade.value.size.height / 2f),
            alpha = chromeAlpha,
        )
        drawText( // legible-exempt: solved via legibleOn over the photo
            label.speed.value,
            topLeft = Offset(
                pillLeft + label.speedOffset,
                textY - label.speed.value.size.height / 2f,
            ),
            alpha = chromeAlpha,
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
        val finishTl = staticLabels.finish
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
                labels: PreparedTransitionTick<androidx.compose.ui.text.TextLayoutResult>,
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
                val timeTl = if (isNext) labels.nextTime else labels.lastTime
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
                // Proportional display face for values (mono "." gets a full cell —
                // "7 . 5"); the time above keeps the mono for stable counting width.
                val gradeTl = labels.grade
                val spdTl = labels.speed
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
            val nextBottom = if (nextVisible) transitionTick(
                nextB,
                isNext = true,
                labels = staticLabels.transitions[curIvIdx + 1],
            )
            else Float.NEGATIVE_INFINITY
            if (lastVisible) {
                transitionTick(
                    lastB,
                    isNext = false,
                    labels = staticLabels.transitions[curIvIdx],
                    avoidBelow = nextBottom + 5f,
                )
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
 * This is the only route-position → screen geometry used by drawing, labels, the
 * prepared pipeline, and tests. Distance still drives organic wobble; smoothed grade
 * drives the monotonic sweep width.
 */
internal class RidgelineGeometry(
    private val route: RidgelineRoute,
    internal val centerX: Float,
    internal val ampBase: Float,
    val camLo: Double,
    val ew: Double,
    internal val topY: Float,
    internal val botY: Float,
) {
    val camHi: Double get() = camLo + ew

    fun screenY(pos: Double): Float = (botY - ((pos - camLo) / ew) * (botY - topY)).toFloat()

    fun worldX(pos: Double): Float {
        val u = route.distAt(pos)
        val amp = ampBase * switchbackAmpFactor(route.smoothedGradeAt(pos)) *
            (0.85f + 0.15f * sin(u * 1.7 + 0.4).toFloat())
        return centerX + amp * sin(route.phaseAt(pos) + 0.4 * sin(u * 1.23 + 0.7)).toFloat()
    }
}

/** One prepared label projected through the exact geometry used by the draw pass. */
internal data class ProjectedTransitionLabel<T>(
    val label: PreparedTransitionLabel<T>,
    val candidate: ChipCandidate,
    val travelled: Boolean,
)

internal data class TransitionLabelFrame<T>(
    val visible: List<ProjectedTransitionLabel<T>>,
    val prioritized: List<ProjectedTransitionLabel<T>>,
    val layout: ChipLayoutResult,
)

/**
 * Collect every transition boundary in the camera window without walking irrelevant
 * intervals. The scan begins in the interval containing [RidgelineGeometry.camLo] and
 * stops at the first boundary above [RidgelineGeometry.camHi].
 */
internal fun <T> collectVisibleTransitionCandidates(
    model: TransitionLabelModel<T>,
    geometry: RidgelineGeometry,
    markerPos: Double,
): List<ProjectedTransitionLabel<T>> {
    val out = ArrayList<ProjectedTransitionLabel<T>>()
    var i = model.route.idxAt(geometry.camLo)
    while (i < model.labels.size) {
        val label = model.labels[i]
        val boundary = label.key
        if (boundary > geometry.camHi) break
        if (boundary >= geometry.camLo) {
            out.add(
                ProjectedTransitionLabel(
                    label = label,
                    candidate = ChipCandidate(
                        key = boundary,
                        anchorX = geometry.worldX(boundary),
                        anchorY = geometry.screenY(boundary),
                        pillW = label.pillW,
                    ),
                    travelled = boundary < markerPos,
                ),
            )
        }
        i++
    }
    return out
}

/** Shared production frame pipeline: project, prioritize, place, and instrument. */
internal fun <T> layoutTransitionLabelFrame(
    model: TransitionLabelModel<T>,
    geometry: RidgelineGeometry,
    markerPos: Double,
    centerX: Float,
    mapW: Float,
    markerRect: Rect,
    metricsGuard: Rect?,
    topBound: Float,
    botBound: Float,
    fixedGuards: List<Rect> = emptyList(),
): TransitionLabelFrame<T> {
    val visible = collectVisibleTransitionCandidates(model, geometry, markerPos)
    // Caller order is route order; production priority is upcoming nearest-first,
    // followed by travelled labels most-recent-first.
    val prioritized = visible.sortedWith(
        compareBy({ it.travelled }, { if (it.travelled) -it.label.key else it.label.key }),
    )
    val layout = layoutTransitionChipsDetailed(
        candidates = prioritized.map { it.candidate },
        centerX = centerX,
        mapW = mapW,
        markerRect = markerRect,
        metricsGuard = metricsGuard,
        topBound = topBound,
        botBound = botBound,
        fixedGuards = fixedGuards,
    )
    return TransitionLabelFrame(visible, prioritized, layout)
}

private const val PACKING_GRID_PX = 2f
private fun packingPixel(value: Float): Float = floor(value / PACKING_GRID_PX) * PACKING_GRID_PX
private fun packingCeil(value: Float): Float = kotlin.math.ceil(value / PACKING_GRID_PX) * PACKING_GRID_PX
private fun packingRect(rect: Rect): Rect = Rect(
    packingPixel(rect.left), packingPixel(rect.top), packingCeil(rect.right), packingCeil(rect.bottom),
)

/**
 * Reprojects exact anchors every frame, while retaining only 2px-quantized packing.
 * Thus route/leader motion stays smooth and collision decisions avoid subpixel churn.
 */
internal class TransitionLabelFrameCache<T> {
    var computations: Int = 0
        private set

    private var cachedModel: TransitionLabelModel<T>? = null
    private var cachedGeometryCenter = Float.NaN
    private var cachedGeometryAmp = Float.NaN
    private var cachedEw = Double.NaN
    private var cachedGeometryTop = Float.NaN
    private var cachedGeometryBot = Float.NaN
    private var cachedCenterX = Float.NaN
    private var cachedTopBound = Float.NaN
    private var cachedBotBound = Float.NaN
    private var cachedMapW = Float.NaN
    private var cachedMarkerRect: Rect? = null
    private var cachedMetricsGuard: Rect? = null
    private var cachedFixedGuards: List<Rect> = emptyList()
    private var cachedFirstInterval = Int.MIN_VALUE
    private var cachedLastInterval = Int.MIN_VALUE
    private var cachedTravelledCut = Int.MIN_VALUE
    private var cachedCameraPixel = Int.MIN_VALUE
    private var cachedLayout: ChipLayoutResult? = null

    fun layout(
        model: TransitionLabelModel<T>,
        geometry: RidgelineGeometry,
        markerPos: Double,
        centerX: Float,
        mapW: Float,
        markerRect: Rect,
        metricsGuard: Rect?,
        topBound: Float,
        botBound: Float,
        fixedGuards: List<Rect> = emptyList(),
    ): TransitionLabelFrame<T> {
        val visible = collectVisibleTransitionCandidates(model, geometry, markerPos)
        val prioritized = visible.sortedWith(
            compareBy({ it.travelled }, { if (it.travelled) -it.label.key else it.label.key }),
        )
        val firstInterval = visible.firstOrNull()?.label?.intervalIndex ?: -1
        val lastInterval = visible.lastOrNull()?.label?.intervalIndex ?: -1
        val markerInterval = model.route.idxAt(markerPos)
        val travelledCut = if (markerPos > model.route.startOf(markerInterval)) markerInterval else markerInterval - 1
        val cameraPixelsPerUnit = (geometry.botY - geometry.topY) / geometry.ew
        val cameraPixel = if (kotlin.math.abs(cameraPixelsPerUnit) < 1e-6f) 0 else floor(
            geometry.camLo * cameraPixelsPerUnit / PACKING_GRID_PX,
        ).toInt()
        val packingCamLo = if (kotlin.math.abs(cameraPixelsPerUnit) < 1e-6f) geometry.camLo else
            cameraPixel * PACKING_GRID_PX / cameraPixelsPerUnit
        val snappedMarker = packingRect(markerRect)
        val cached = cachedLayout
        if (cached != null && cachedModel === model &&
            cachedGeometryCenter == geometry.centerX && cachedGeometryAmp == geometry.ampBase &&
            cachedCameraPixel == cameraPixel && cachedEw == geometry.ew &&
            cachedGeometryTop == geometry.topY && cachedGeometryBot == geometry.botY &&
            cachedCenterX == centerX && cachedTopBound == topBound && cachedBotBound == botBound &&
            cachedFirstInterval == firstInterval && cachedLastInterval == lastInterval &&
            cachedTravelledCut == travelledCut && cachedMapW == mapW &&
            cachedMarkerRect == snappedMarker && cachedMetricsGuard == metricsGuard &&
            cachedFixedGuards == fixedGuards
        ) return TransitionLabelFrame(visible, prioritized, cached)

        val packingGeometry = RidgelineGeometry(
            model.route, geometry.centerX, geometry.ampBase, packingCamLo,
            geometry.ew, geometry.topY, geometry.botY,
        )
        val packingCandidates = prioritized.map {
            it.candidate.copy(
                anchorX = packingGeometry.worldX(it.label.key),
                anchorY = packingGeometry.screenY(it.label.key),
            )
        }
        val layout = layoutTransitionChipsDetailed(
            packingCandidates, centerX, mapW, snappedMarker, metricsGuard,
            topBound, botBound, fixedGuards,
        )
        computations++
        cachedModel = model
        cachedGeometryCenter = geometry.centerX
        cachedGeometryAmp = geometry.ampBase
        cachedCameraPixel = cameraPixel
        cachedEw = geometry.ew
        cachedGeometryTop = geometry.topY
        cachedGeometryBot = geometry.botY
        cachedCenterX = centerX
        cachedTopBound = topBound
        cachedBotBound = botBound
        cachedFirstInterval = firstInterval
        cachedLastInterval = lastInterval
        cachedTravelledCut = travelledCut
        cachedMapW = mapW
        cachedMarkerRect = snappedMarker
        cachedMetricsGuard = metricsGuard
        cachedFixedGuards = fixedGuards.toList()
        cachedLayout = layout
        return TransitionLabelFrame(visible, prioritized, layout)
    }
}

// --- transition chip placement (pure geometry) -----------------------------
// Split out of the draw pass so the "labels never disappear" property is a unit
// test (RidgelineChipLayoutTest) rather than something you can only catch by
// staring at a moving treadmill.

/** Chip pill height, px. */
internal const val CHIP_H = 24f

/** The whole travelled chip, including both text runs, uses one deterministic alpha. */
internal fun transitionChipAlpha(travelled: Boolean): Float = if (travelled) 0.68f else 1f

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
    val overlapFallback: Boolean = false,
) {
    val offBend: Boolean get() = dx != 0f || dy != 0f
}

internal data class ChipLayoutStats(
    val probes: Int,
    val collisionChecks: Int,
    val overlapFallbacks: Int,
)

internal data class ChipLayoutResult(val slots: List<ChipSlot>, val stats: ChipLayoutStats)

private class MutableChipLayoutStats {
    var probes = 0
    var collisionChecks = 0
    var overlapFallbacks = 0
}

/** Spatially bins placed rectangles; probes pass float bounds and allocate nothing. */
private class RectSpatialBins(
    private val mapW: Float,
    private val topBound: Float,
    private val botBound: Float,
    expectedRects: Int,
) {
    private val cell = 32f
    private val cols = max(1, kotlin.math.ceil(mapW / cell).toInt())
    private val rows = max(1, kotlin.math.ceil(max(1f, botBound - topBound) / cell).toInt())
    private val bins = arrayOfNulls<MutableList<Int>>(cols * rows)
    private val rects = ArrayList<Rect>(expectedRects)
    private val seen = IntArray(max(1, expectedRects))
    private var stamp = 0

    private fun col(x: Float): Int = kotlin.math.floor(x / cell).toInt().coerceIn(0, cols - 1)
    private fun row(y: Float): Int = kotlin.math.floor((y - topBound) / cell).toInt().coerceIn(0, rows - 1)

    fun add(rect: Rect) {
        if (rect.width <= 0f || rect.height <= 0f) return
        val id = rects.size
        rects.add(rect)
        for (r in row(rect.top)..row(rect.bottom)) for (c in col(rect.left)..col(rect.right)) {
            val index = r * cols + c
            val bucket = bins[index] ?: ArrayList<Int>().also { bins[index] = it }
            bucket.add(id)
        }
    }

    fun allRects(): List<Rect> = rects

    fun overlaps(l: Float, t: Float, r: Float, b: Float, stats: MutableChipLayoutStats): Boolean {
        stamp++
        if (stamp == Int.MAX_VALUE) {
            seen.fill(0)
            stamp = 1
        }
        for (row in row(t)..row(b)) for (col in col(l)..col(r)) {
            val bucket = bins[row * cols + col] ?: continue
            for (id in bucket) {
                if (seen[id] == stamp) continue
                seen[id] = stamp
                stats.collisionChecks++
                val p = rects[id]
                if (p.left < r && l < p.right && p.top < b && t < p.bottom) return true
            }
        }
        return false
    }

    /** Rightmost edge among blockers intersecting the probe, or NaN when it is clear. */
    fun blockingRight(l: Float, t: Float, r: Float, b: Float, stats: MutableChipLayoutStats): Float {
        stamp++
        if (stamp == Int.MAX_VALUE) {
            seen.fill(0)
            stamp = 1
        }
        var right = Float.NaN
        for (row in row(t)..row(b)) for (col in col(l)..col(r)) {
            val bucket = bins[row * cols + col] ?: continue
            for (id in bucket) {
                if (seen[id] == stamp) continue
                seen[id] = stamp
                stats.collisionChecks++
                val p = rects[id]
                if (p.left < r && l < p.right && p.top < b && t < p.bottom) {
                    right = if (right.isNaN()) p.right else max(right, p.right)
                }
            }
        }
        return right
    }
}

/**
 * Place transition chips in caller-supplied priority order so they never vanish.
 * Production supplies upcoming nearest-first, then travelled most-recent-first.
 *
 * Ordinary bend-relative offsets are followed by a complete blocker-edge/canvas-edge
 * search. If no collision-free rectangle exists, a deterministic clamped slot remains;
 * only that physically overfull last resort may overlap.
 */
internal fun layoutTransitionChips(
    candidates: List<ChipCandidate>,
    centerX: Float,
    mapW: Float,
    markerRect: Rect,
    metricsGuard: Rect?,
    topBound: Float,
    botBound: Float,
): List<ChipSlot> = layoutTransitionChipsDetailed(
    candidates, centerX, mapW, markerRect, metricsGuard, topBound, botBound,
).slots

internal fun layoutTransitionChipsDetailed(
    candidates: List<ChipCandidate>,
    centerX: Float,
    mapW: Float,
    markerRect: Rect,
    metricsGuard: Rect?,
    topBound: Float,
    botBound: Float,
    fixedGuards: List<Rect> = emptyList(),
): ChipLayoutResult {
    val stats = MutableChipLayoutStats()
    val occupancy = RectSpatialBins(
        mapW, topBound, botBound,
        candidates.size + fixedGuards.size + 2,
    )
    occupancy.add(markerRect)
    metricsGuard?.let { occupancy.add(it) }
    fixedGuards.forEach { occupancy.add(it) }
    val out = ArrayList<ChipSlot>(candidates.size)
    var narrowestFailedWidth = Float.POSITIVE_INFINITY
    // Proof-only lower bound: collision-free inflated chip rectangles must fit within
    // the canvas area. When their summed area exceeds it, exhaustive blocker-edge
    // search cannot possibly place every label; retain ordinary bend-relative probes,
    // then take the explicit fail-visible fallback. Potentially satisfiable layouts
    // always keep the complete edge search.
    val inflatedChipArea = candidates.sumOf {
        ((min(it.pillW, max(1f, mapW - 8f)) + 8f) * (CHIP_H + 8f)).toDouble()
    }
    val provablyOverfull = inflatedChipArea >
        (max(1f, mapW) * max(1f, botBound - topBound + 8f)).toDouble()

    for (c in candidates) {
        val pillW = min(c.pillW, max(1f, mapW - 8f))
        fun pillLeftFor(side: Int, dx: Float): Float {
            val cx0 = c.anchorX + side * 22f
            return ((if (side < 0) cx0 - pillW else cx0) + dx)
                .coerceIn(4f, max(4f, mapW - pillW - 4f))
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
            if (onBend) max(c.anchorX + 5f, pl + pillW) else pl + pillW

        fun usable(l: Float, t: Float, r: Float, b: Float): Boolean {
            stats.probes++
            val il = l - 4f; val it = t - 4f; val ir = r + 4f; val ib = b + 4f
            return !occupancy.overlaps(il, it, ir, ib, stats)
        }

        val natural = if (c.anchorX < centerX) -1 else 1
        val sides = intArrayOf(natural, -natural)
        var slot: ChipSlot? = null
        if (pillW < narrowestFailedWidth) {
            search@ for ((dx, dy) in CHIP_OFFSETS) {
                val onBend = dx == 0f && dy == 0f
                val top = topFor(dy)
                for (side in sides) {
                    val pl = pillLeftFor(side, dx)
                    val l = leftFor(pl, onBend)
                    val r = rightFor(pl, onBend)
                    if (usable(l, top, r, top + CHIP_H)) {
                        occupancy.add(Rect(l, top, r, top + CHIP_H))
                        slot = ChipSlot(c.key, pl, top, dx, dy)
                        break@search
                    }
                }
            }
        }

        // For axis-aligned blockers, a feasible rectangle can slide up until it touches
        // a blocker/canvas edge. At each such y, sweep from the canvas edge and jump
        // directly past the rightmost blocker hit. This covers the same blocker-edge
        // positions as a Cartesian x/y search without its quadratic probe explosion.
        if (slot == null && pillW < narrowestFailedWidth && !provablyOverfull) {
            val minLeft = 4f
            val maxLeft = max(minLeft, mapW - pillW - 4f)
            val maxTop = max(topBound, botBound - CHIP_H)
            val preferredLeft = pillLeftFor(natural, 0f)
            val preferredTop = topFor(0f)
            val blockers = occupancy.allRects()
            val ys = FloatArray(blockers.size * 2 + 2)
            var yCount = 0
            ys[yCount++] = topBound; ys[yCount++] = maxTop
            for (b in blockers) {
                ys[yCount++] = (b.bottom + 4f).coerceIn(topBound, maxTop)
                ys[yCount++] = (b.top - CHIP_H - 4f).coerceIn(topBound, maxTop)
            }
            java.util.Arrays.sort(ys, 0, yCount)
            edge@ for (yi in 0 until yCount) {
                if (yi > 0 && ys[yi] == ys[yi - 1]) continue
                val top = ys[yi]
                var pl = minLeft
                while (pl <= maxLeft) {
                    stats.probes++
                    val blockingRight = occupancy.blockingRight(
                        pl - 4f, top - 4f, pl + pillW + 4f, top + CHIP_H + 4f, stats,
                    )
                    if (blockingRight.isNaN()) {
                        occupancy.add(Rect(pl, top, pl + pillW, top + CHIP_H))
                        slot = ChipSlot(c.key, pl, top, pl - preferredLeft, top - preferredTop)
                        break@edge
                    }
                    pl = blockingRight + 4f
                }
            }
        }

        // The canvas may genuinely be too small (or wholly covered by guards). Keep a
        // stable, clamped pill anyway: a visible overlap is the explicit last resort.
        if (slot == null) {
            val pl = pillLeftFor(natural, 0f)
            val top = topFor(0f)
            occupancy.add(Rect(pl, top, pl + pillW, top + CHIP_H))
            slot = ChipSlot(c.key, pl, top, 0f, 0f, overlapFallback = true)
            stats.overlapFallbacks++
            narrowestFailedWidth = min(narrowestFailedWidth, pillW)
        }
        out.add(slot)
    }
    return ChipLayoutResult(
        out,
        ChipLayoutStats(stats.probes, stats.collisionChecks, stats.overlapFallbacks),
    )
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

/**
 * Cached grade paint for the mono trail. Its inputs exclude marker position and pulse,
 * so steady animated frames reuse the gradient, native Paint, shader, and blur filter.
 */
private class SteepnessPaintSlot {
    private var routeKey: RidgelineRoute? = null
    private var camLoBits = 0L
    private var ewBits = 0L
    private var canvasWidthBits = 0
    private var canvasHeightBits = 0
    private var topYBits = 0
    private var botYBits = 0
    private var fadeBits = 0
    private var densityBits = 0
    private var cutTopKey = false

    var brush: Brush = SolidColor(RidgelineTheme.fg)
        private set

    var glowPaint: android.graphics.Paint? = null
        private set

    fun update(
        route: RidgelineRoute,
        camLo: Double,
        ew: Double,
        canvasWidth: Float,
        canvasHeight: Float,
        topY: Float,
        botY: Float,
        cutTop: Boolean,
        fade: Float,
        density: Float,
    ) {
        val sameKey = routeKey === route &&
            camLoBits == camLo.toRawBits() && ewBits == ew.toRawBits() &&
            canvasWidthBits == canvasWidth.toRawBits() &&
            canvasHeightBits == canvasHeight.toRawBits() &&
            topYBits == topY.toRawBits() && botYBits == botY.toRawBits() &&
            fadeBits == fade.toRawBits() && densityBits == density.toRawBits() &&
            cutTopKey == cutTop
        if (sameKey) return

        routeKey = route
        camLoBits = camLo.toRawBits()
        ewBits = ew.toRawBits()
        canvasWidthBits = canvasWidth.toRawBits()
        canvasHeightBits = canvasHeight.toRawBits()
        topYBits = topY.toRawBits()
        botYBits = botY.toRawBits()
        fadeBits = fade.toRawBits()
        densityBits = density.toRawBits()
        cutTopKey = cutTop

        if (botY - topY < 1f) {
            brush = SolidColor(RidgelineTheme.fg)
            glowPaint = null
            return
        }

        val count = STEEP_STOPS + 1
        val stops = ArrayList<Pair<Float, Color>>(count)
        val haloColors = IntArray(count)
        val haloPositions = FloatArray(count)
        for (i in 0 until count) {
            val fraction = i.toFloat() / STEEP_STOPS
            val pos = (camLo + (1.0 - fraction) * ew).coerceIn(0.0, route.total)
            val steepness = (route.smoothedGradeAt(pos) / RidgelineRoute.GRADE_REF)
                .coerceIn(0.0, 1.0).toFloat()
            val fadeAlpha = if (!cutTop) 1f else
                (fraction * (botY - topY) / fade).coerceIn(0f, 1f)
            val trailColor = lerp(
                RidgelineTheme.fg,
                RidgelineTheme.elev,
                steepness * STEEP_TINT_MAX,
            )
            stops.add(fraction to trailColor.copy(alpha = trailColor.alpha * fadeAlpha))
            haloPositions[i] = fraction
            haloColors[i] = RidgelineTheme.elev.copy(
                alpha = STEEP_GLOW_ALPHA * steepness * steepness * fadeAlpha,
            ).toArgb()
        }
        brush = Brush.verticalGradient(
            colorStops = stops.toTypedArray(),
            startY = topY,
            endY = botY,
        )
        glowPaint = android.graphics.Paint().apply {
            isAntiAlias = true
            style = android.graphics.Paint.Style.STROKE
            strokeWidth = 10f * density
            strokeCap = android.graphics.Paint.Cap.ROUND
            strokeJoin = android.graphics.Paint.Join.ROUND
            shader = android.graphics.LinearGradient(
                0f,
                topY,
                0f,
                botY,
                haloColors,
                haloPositions,
                android.graphics.Shader.TileMode.CLAMP,
            )
            maskFilter = android.graphics.BlurMaskFilter(
                5.5f * density,
                android.graphics.BlurMaskFilter.Blur.NORMAL,
            )
        }
    }
}

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
