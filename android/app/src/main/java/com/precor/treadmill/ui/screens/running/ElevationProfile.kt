package com.precor.treadmill.ui.screens.running

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.*
import androidx.compose.ui.graphics.drawscope.*
import androidx.compose.ui.text.*
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.precor.treadmill.ui.viewmodel.ElevationPoint
import com.precor.treadmill.ui.viewmodel.TreadmillViewModel
import kotlin.math.roundToInt

// Layout constants matching the web version
private const val W = 400f
private const val H = 140f
private const val PAD = 10f
private const val ML = 28f  // margin left
private const val MR = 4f   // margin right
private const val MT = 4f   // margin top
private const val MB = 18f  // margin bottom
private const val TICK = 4f

private val doneAreaColor = Color(0xFF6BC89B).copy(alpha = 0.18f)
private val futureAreaColor = Color(0xFF6BC89B).copy(alpha = 0.08f)
private val doneStrokeColor = Color(0xFF6BC89B).copy(alpha = 0.5f)
private val futureStrokeColor = Color(0xFF6BC89B).copy(alpha = 0.18f)
private val axisColor = Color(0xFFE8E4DF).copy(alpha = 0.2f)
private val gridColor = Color(0xFFE8E4DF).copy(alpha = 0.12f)
private val tickColor = Color(0xFFE8E4DF).copy(alpha = 0.25f)
private val labelColor = Color(0xFFE8E4DF).copy(alpha = 0.5f)
private val dotColor = Color(0xFF6BC89B)
private val dotBorderColor = Color(0xFFE8E4DF)
private val stepDotCompletedColor = Color(0xFF6BC89B).copy(alpha = 0.8f)
private val stepDotCurrentColor = Color(0xFF6BC89B)
private val stepDotCurrentGlowColor = Color(0xFF6BC89B).copy(alpha = 0.15f)
private val stepDotFutureStrokeColor = Color(0xFFE8E4DF).copy(alpha = 0.3f)
private val stepTrackColor = Color(0xFFE8E4DF).copy(alpha = 0.1f)

private fun inclineY(inc: Float, yMax: Float): Float = MT + H - PAD - (inc / yMax) * (H - PAD * 2)

/** D3-style "nice numbers" tick generation for time axis. */
private fun computeTimeTicks(totalSec: Double): List<Pair<Double, String>> {
    if (totalSec <= 0) return emptyList()
    val targetTicks = 6
    val rawStep = totalSec / targetTicks
    val niceSteps = listOf(30.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1800.0, 3600.0, 7200.0)
    var step = niceSteps[0]
    for (s in niceSteps) {
        step = s
        if (s >= rawStep) break
    }

    val ticks = mutableListOf<Pair<Double, String>>()
    val useHour = totalSec >= 3600
    var t = step
    while (t < totalSec) {
        if (totalSec - t >= step * 0.15) {
            val label = if (useHour) {
                val hrs = (t / 3600).toInt()
                val mins = ((t % 3600) / 60).roundToInt()
                when {
                    hrs == 0 -> ":${mins.toString().padStart(2, '0')}"
                    mins == 0 -> "${hrs}h"
                    else -> "$hrs:${mins.toString().padStart(2, '0')}"
                }
            } else {
                "${(t / 60).roundToInt()}"
            }
            ticks.add(t to label)
        }
        t += step
    }
    return ticks
}

private fun computeInclineTicks(maxIncline: Double): List<Int> = when {
    maxIncline <= 5.0 -> listOf(0, 2, 5)
    maxIncline <= 10.0 -> listOf(0, 5, 10)
    else -> listOf(0, 5, 10, 15)
}

@OptIn(ExperimentalTextApi::class)
@Composable
fun ElevationProfile(
    viewModel: TreadmillViewModel,
    modifier: Modifier = Modifier,
) {
    val pgm by viewModel.derivedProgram.collectAsState()
    val textMeasurer = rememberTextMeasurer()

    val totalW = ML + W + MR
    val totalH = MT + H + MB

    Canvas(
        modifier = modifier.fillMaxSize(),
    ) {
        val scaleX = size.width / totalW
        val scaleY = size.height / totalH

        // Helper to convert chart coords to canvas coords
        fun cx(x: Float) = x * scaleX
        fun cy(y: Float) = y * scaleY

        val timeTicks = computeTimeTicks(pgm.totalDuration)
        val inclineTicks = computeInclineTicks(pgm.maxIncline)

        // Axis lines
        drawLine(axisColor, Offset(cx(ML), cy(MT)), Offset(cx(ML), cy(MT + H)), strokeWidth = 1f)
        drawLine(axisColor, Offset(cx(ML), cy(MT + H)), Offset(cx(ML + W), cy(MT + H)), strokeWidth = 1f)

        // Y-axis grid lines + ticks
        val dashEffect = PathEffect.dashPathEffect(floatArrayOf(3f * scaleX, 4f * scaleX))
        for (inc in inclineTicks) {
            val y = inclineY(inc.toFloat(), pgm.yAxisMax)
            // Grid line (dashed)
            drawLine(gridColor, Offset(cx(ML), cy(y)), Offset(cx(ML + W), cy(y)), strokeWidth = 0.5f, pathEffect = dashEffect)
            // Tick mark
            drawLine(tickColor, Offset(cx(ML - TICK), cy(y)), Offset(cx(ML), cy(y)), strokeWidth = 1f)

            // Y-axis label
            val labelText = "$inc%"
            val result = textMeasurer.measure(
                AnnotatedString(labelText),
                style = TextStyle(color = labelColor, fontSize = 9.sp, fontWeight = FontWeight.Medium),
            )
            drawText(result, topLeft = Offset(cx(ML - TICK - 2) - result.size.width, cy(y) - result.size.height / 2f))
        }

        // X-axis grid lines + ticks + labels
        for ((sec, label) in timeTicks) {
            val svgX = ML + (sec / pgm.totalDuration * W).toFloat()
            // Grid line (dashed)
            drawLine(gridColor, Offset(cx(svgX), cy(MT)), Offset(cx(svgX), cy(MT + H)), strokeWidth = 0.5f, pathEffect = dashEffect)
            // Tick mark
            drawLine(tickColor, Offset(cx(svgX), cy(MT + H)), Offset(cx(svgX), cy(MT + H + TICK)), strokeWidth = 1f)

            // X-axis label
            val result = textMeasurer.measure(
                AnnotatedString(label),
                style = TextStyle(color = labelColor, fontSize = 9.sp, fontWeight = FontWeight.Medium),
            )
            drawText(result, topLeft = Offset(cx(svgX) - result.size.width / 2f, cy(MT + H + TICK + 1)))
        }

        // Build spline path
        if (pgm.points.isNotEmpty()) {
            val path = buildSplinePath(pgm.points, pgm.tangents, scaleX, scaleY)
            val areaPath = Path().apply {
                addPath(path)
                lineTo(cx(ML + W), cy(MT + H))
                lineTo(cx(ML), cy(MT + H))
                close()
            }

            val posX = cx(ML + pgm.elevPosX)

            // Future area (dimmer)
            clipRect(posX, 0f, size.width, size.height) {
                drawPath(areaPath, futureAreaColor)
                drawPath(path, futureStrokeColor, style = Stroke(width = 1.5f * scaleX, cap = StrokeCap.Round, join = StrokeJoin.Round))
            }

            // Done area (brighter)
            clipRect(0f, 0f, posX, size.height) {
                drawPath(areaPath, doneAreaColor)
                drawPath(path, doneStrokeColor, style = Stroke(width = 1.5f * scaleX, cap = StrokeCap.Round, join = StrokeJoin.Round))
            }

            // Position dot
            val dotX = cx(ML + pgm.elevPosX)
            val dotY = cy(MT + pgm.elevPosY)
            val dotRadius = 5f * scaleX

            // Glow shadow
            drawCircle(dotColor.copy(alpha = 0.3f), radius = dotRadius * 2, center = Offset(dotX, dotY))
            // Outer glow
            drawCircle(dotBorderColor.copy(alpha = 0.6f), radius = dotRadius * 1.3f, center = Offset(dotX, dotY))
            // Dot
            drawCircle(dotColor, radius = dotRadius, center = Offset(dotX, dotY))
            // Border
            drawCircle(dotBorderColor, radius = dotRadius, center = Offset(dotX, dotY), style = Stroke(width = 1.5f))
        }

        // Step indicator dots
        if (pgm.intervalBoundaryXs.size > 2) {
            val trackY = cy(MT + H - 2)

            // Track line
            drawLine(stepTrackColor, Offset(cx(ML), trackY), Offset(cx(ML + W), trackY), strokeWidth = 1f)

            // Dots at each interval boundary (skip last = end)
            for (i in 0 until pgm.intervalBoundaryXs.size - 1) {
                val bx = pgm.intervalBoundaryXs[i]
                val dotCx = cx(ML + bx)
                val isCompleted = i < pgm.currentInterval
                val isCurrent = i == pgm.currentInterval

                when {
                    isCurrent -> {
                        drawCircle(stepDotCurrentGlowColor, radius = 6f * scaleX, center = Offset(dotCx, trackY))
                        drawCircle(stepDotCurrentColor, radius = 3.5f * scaleX, center = Offset(dotCx, trackY))
                    }
                    isCompleted -> {
                        drawCircle(stepDotCompletedColor, radius = 2.5f * scaleX, center = Offset(dotCx, trackY))
                    }
                    else -> {
                        drawCircle(stepDotFutureStrokeColor, radius = 2.5f * scaleX, center = Offset(dotCx, trackY),
                            style = Stroke(width = 1f))
                    }
                }
            }
        }
    }
}

/** Build a cubic bezier path from points using Hermite→Bezier conversion. */
private fun buildSplinePath(
    points: List<ElevationPoint>,
    tangents: FloatArray,
    scaleX: Float,
    scaleY: Float,
): Path {
    fun cx(x: Float) = (ML + x) * scaleX
    fun cy(y: Float) = (MT + y) * scaleY

    val n = points.size
    val path = Path()

    if (n == 0) return path

    // Start at left edge
    path.moveTo(cx(0f), cy(points[0].y))
    path.lineTo(cx(points[0].x), cy(points[0].y))

    // Cubic bezier segments
    for (i in 0 until n - 1) {
        val p0 = points[i]
        val p1 = points[i + 1]
        val d = p1.x - p0.x
        val cp1x = p0.x + d / 3
        val cp1y = p0.y + (tangents[i] * d) / 3
        val cp2x = p1.x - d / 3
        val cp2y = p1.y - (tangents[i + 1] * d) / 3
        path.cubicTo(cx(cp1x), cy(cp1y), cx(cp2x), cy(cp2y), cx(p1.x), cy(p1.y))
    }

    // Extend to right edge
    path.lineTo(cx(W), cy(points[n - 1].y))

    return path
}
