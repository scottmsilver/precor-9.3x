package com.precor.treadmill.ui.screens.running

import androidx.compose.ui.geometry.Rect
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Replays a real multi-interval program through the map's real geometry, second by
 * second, and asserts the thing the user asked for: **a transition label that is on
 * screen does not disappear while you keep running.**
 *
 * The per-frame placement rules are unit-tested in [RidgelineChipLayoutTest]; this walks
 * a whole workout so camera pans, switchback sweep and chip crowding all interact the way
 * they do on the treadmill. It also writes `build/ridgeline-labels.svg`, a filmstrip of
 * the run, so the result can be eyeballed without a device.
 */
class RidgelineLabelStabilityTest {

    // The same 12-interval workout used for the on-device check: short segments, mixed
    // grades, 16 minutes — long enough that the camera windows and pans.
    private val plan = listOf(
        Triple(3.0, 1.0, 120.0), Triple(4.5, 3.0, 90.0), Triple(3.2, 1.5, 60.0),
        Triple(5.0, 6.0, 75.0), Triple(3.0, 2.0, 45.0), Triple(4.2, 8.5, 90.0),
        Triple(3.0, 1.0, 60.0), Triple(5.5, 4.0, 120.0), Triple(3.4, 2.5, 50.0),
        Triple(4.8, 7.0, 80.0), Triple(3.0, 0.5, 100.0), Triple(4.0, 5.5, 70.0),
    )
    private val route = RidgelineRoute(
        plan.map { (speed, grade, dur) -> RouteInterval(grade = grade, speed = speed, durSec = dur) },
    )

    // Canvas geometry mirroring drawRidgeline() on a landscape tablet panel.
    private val w = 1280f
    private val h = 800f
    private val dp = 2f
    private val stripW = 12f * dp
    private val stripX = w - 16f * dp - stripW / 2f
    private val mapW = stripX - stripW / 2f - 14f * dp
    private val topY = 74f / 800f * h
    private val botY = h - 50f * dp
    private val centerX = mapW * 0.50f
    private val ampBase = mapW * 0.40f
    private val pillW = 72f // measured "7.5% 3.0" at 13sp, near enough for placement
    private val metricsGuard = Rect(24f - 24f, 24f - 24f, 24f + 320f + 24f, 24f + 180f + 24f)

    private data class Frame(
        val markerPos: Double,
        val camLo: Double,
        val marker: Pair<Float, Float>,
        val anchors: Map<Double, Pair<Float, Float>>,
        val slots: List<ChipSlot>,
    )

    /** One rendered frame at [markerPos], with the camera at [camLo]. */
    private fun frameAt(markerPos: Double, camLo: Double): Frame {
        val geom = RidgelineGeometry(route, centerX, ampBase, camLo, POS_WINDOW, topY, botY)
        val anchors = LinkedHashMap<Double, Pair<Float, Float>>()
        val candidates = ArrayList<ChipCandidate>()
        val ahead = ArrayList<ChipCandidate>()
        val behind = ArrayList<ChipCandidate>()
        for (i in 0 until route.count) {
            val bs = route.startOf(i)
            if (bs < camLo || bs > geom.camHi) continue
            val x = geom.worldX(bs)
            val y = geom.screenY(bs)
            anchors[bs] = x to y
            val c = ChipCandidate(bs, x, y, pillW)
            if (bs < markerPos) behind.add(c) else ahead.add(c)
        }
        // Same priority order the draw pass uses: upcoming first, then most-recent past.
        candidates.addAll(ahead)
        candidates.addAll(behind.sortedByDescending { it.key })
        val mx = geom.worldX(markerPos)
        val my = geom.screenY(markerPos)
        val slots = layoutTransitionChips(
            candidates = candidates,
            centerX = centerX,
            mapW = mapW,
            markerRect = Rect(mx - 20f, my - 20f, mx + 20f, my + 20f),
            metricsGuard = metricsGuard,
            topBound = topY,
            botBound = botY,
        )
        return Frame(markerPos, camLo, mx to my, anchors, slots)
    }

    /** Walk the whole program at 1s resolution, camera following as it does live. */
    private fun runFrames(): List<Frame> {
        val frames = ArrayList<Frame>()
        var camLo = computeTargetLo(route, 0.0)
        var t = 0.0
        while (t <= route.total) {
            // The live camera eases toward its target over ~1s; sample the eased value so
            // mid-pan frames (where chips are sliding) are covered too.
            val target = computeTargetLo(route, t)
            camLo += (target - camLo) * 0.25
            frames.add(frameAt(t, camLo))
            t += 1.0
        }
        return frames
    }

    @Test
    fun everyVisibleTransitionKeepsItsLabelForTheWholeRun() {
        val frames = runFrames()
        val misses = mutableListOf<String>()
        for (f in frames) {
            val placed = f.slots.map { it.key }.toSet()
            for (bs in f.anchors.keys) {
                if (bs !in placed) misses.add("t=%.0fs: boundary %.0fs in window but unlabelled".format(f.markerPos, bs))
            }
        }
        assertTrue(
            "${misses.size} frame(s) dropped a visible transition label:\n" +
                misses.take(15).joinToString("\n"),
            misses.isEmpty(),
        )
    }

    /** No label may blink: once shown, it stays shown until it scrolls out of the window. */
    @Test
    fun noLabelBlinksOffAndBackOn() {
        val frames = runFrames()
        val wasShown = HashSet<Double>()
        val blinks = mutableListOf<String>()
        for (f in frames) {
            val placed = f.slots.map { it.key }.toSet()
            for (bs in f.anchors.keys) {
                if (bs in placed) wasShown.add(bs)
                else if (bs in wasShown) blinks.add("t=%.0fs: boundary %.0fs vanished while still on screen".format(f.markerPos, bs))
            }
            // Boundaries that scrolled out are allowed to come back fresh later.
            wasShown.retainAll(f.anchors.keys)
        }
        assertTrue(
            "${blinks.size} label disappearance(s) mid-run:\n" + blinks.take(15).joinToString("\n"),
            blinks.isEmpty(),
        )
    }

    /** Placed pills never overlap each other, the marker, or the metrics pill. */
    @Test
    fun placedLabelsNeverOverlap() {
        val clashes = mutableListOf<String>()
        for (f in runFrames()) {
            val rects = f.slots.map { Rect(it.pillLeft, it.pillTop, it.pillLeft + pillW, it.pillTop + CHIP_H) }
            for (a in rects.indices) {
                if (rects[a].overlaps(metricsGuard)) clashes.add("t=%.0fs: chip over the metrics pill".format(f.markerPos))
                for (b in a + 1 until rects.size) {
                    if (rects[a].overlaps(rects[b])) clashes.add("t=%.0fs: chips overlap".format(f.markerPos))
                }
            }
        }
        assertTrue("${clashes.size} overlap(s):\n" + clashes.take(10).joinToString("\n"), clashes.isEmpty())
    }

    /** Renders a filmstrip so the run can be reviewed by eye (not an assertion). */
    @Test
    fun writesFilmstripForVisualReview() {
        val frames = runFrames()
        val picks = (0 until 12).map { frames[(it * (frames.size - 1)) / 11] }
        val cols = 4
        val scale = 0.42f
        val cw = w * scale
        val ch = h * scale
        val sb = StringBuilder()
        sb.append("""<svg xmlns="http://www.w3.org/2000/svg" width="${cw * cols + 40}" """)
        sb.append(""" height="${ch * 3 + 60}" style="background:#0b0f12">""")
        picks.forEachIndexed { n, f ->
            val ox = 10 + (n % cols) * cw
            val oy = 10 + (n / cols) * (ch + 16)
            sb.append("""<g transform="translate($ox,$oy) scale($scale)">""")
            sb.append("""<rect width="$w" height="$h" fill="#11171b" stroke="#243038"/>""")
            // route thread
            val pts = (0..160).map { k ->
                val p = f.camLo + (k / 160.0) * POS_WINDOW
                val g = RidgelineGeometry(route, centerX, ampBase, f.camLo, POS_WINDOW, topY, botY)
                if (p > route.total) null else "${g.worldX(p)},${g.screenY(p)}"
            }.filterNotNull().joinToString(" ")
            sb.append("""<polyline points="$pts" fill="none" stroke="#e8e4df" stroke-width="4" opacity="0.85"/>""")
            // chips
            for (s in f.slots) {
                val a = f.anchors[s.key]!!
                if (s.offBend) {
                    val edge = if (s.pillLeft > a.first) s.pillLeft else s.pillLeft + pillW
                    sb.append("""<line x1="${a.first}" y1="${a.second}" x2="$edge" y2="${s.pillTop + CHIP_H / 2}" stroke="#e8e4df" stroke-width="2" opacity="0.4"/>""")
                }
                val past = s.key < f.markerPos
                val col = if (past) "#6f7c85" else "#9fd0a8"
                sb.append("""<rect x="${s.pillLeft}" y="${s.pillTop}" width="$pillW" height="$CHIP_H" rx="6" fill="#070b0e" stroke="$col" stroke-width="2" opacity="${if (past) 0.5 else 1.0}"/>""")
                sb.append("""<text x="${s.pillLeft + 7}" y="${s.pillTop + 17}" font-family="monospace" font-size="14" fill="$col">${"%.1f%%".format(route.gradeAt(s.key + 1))}</text>""")
                sb.append("""<circle cx="${a.first}" cy="${a.second}" r="5" fill="#e8e4df"/>""")
            }
            sb.append("""<circle cx="${f.marker.first}" cy="${f.marker.second}" r="11" fill="#7fd18c"/>""")
            sb.append("""<rect x="${metricsGuard.left}" y="${metricsGuard.top}" width="${metricsGuard.width}" height="${metricsGuard.height}" fill="none" stroke="#3a4650" stroke-dasharray="8 8"/>""")
            sb.append("""<text x="20" y="${h - 20}" font-family="monospace" font-size="26" fill="#8fa0ab">t=${f.markerPos.toInt()}s  labels=${f.slots.size}/${f.anchors.size}</text>""")
            sb.append("</g>")
        }
        sb.append("</svg>")
        val out = File("build/ridgeline-labels.svg")
        out.parentFile.mkdirs()
        out.writeText(sb.toString())
        println("filmstrip -> ${out.absolutePath}")
    }
}
