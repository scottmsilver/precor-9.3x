package com.precor.treadmill.ui.theme.readability

import android.graphics.Bitmap
import kotlin.math.ceil

data class NormRect(val x: Double, val y: Double, val w: Double, val h: Double)

fun sampleRegionPixels(pixels: IntArray, width: Int, height: Int, rect: NormRect, id: String, role: Role): RegionStats {
    val x0 = maxOf(0, (rect.x * width).toInt())
    val y0 = maxOf(0, (rect.y * height).toInt())
    val x1 = minOf(width, ceil((rect.x + rect.w) * width).toInt())
    val y1 = minOf(height, ceil((rect.y + rect.h) * height).toInt())
    var sr = 0L; var sg = 0L; var sb = 0L; var n = 0L
    val buckets = HashMap<Int, Int>()
    for (y in y0 until y1) for (x in x0 until x1) {
        val p = pixels[y * width + x]
        val r = (p shr 16) and 0xFF; val g = (p shr 8) and 0xFF; val b = p and 0xFF
        sr += r; sg += g; sb += b; n++
        val key = ((r shr 5) shl 6) or ((g shr 5) shl 3) or (b shr 5)
        buckets[key] = (buckets[key] ?: 0) + 1
    }
    if (n == 0L) n = 1
    val avg = Rgb(sr.toDouble() / n, sg.toDouble() / n, sb.toDouble() / n)
    // tie-break by lowest quantized key so the result is independent of map iteration order (TS<->Kotlin parity)
    var bestKey = 0; var bestCount = -1
    for ((k, c) in buckets) if (c > bestCount || (c == bestCount && k < bestKey)) { bestCount = c; bestKey = k }
    // bucket-center reconstruction (32*bucket+16); must match the TS formula exactly
    val dominant = Rgb(
        (((bestKey shr 6) and 7) * 32 + 16).toDouble(),
        (((bestKey shr 3) and 7) * 32 + 16).toDouble(),
        ((bestKey and 7) * 32 + 16).toDouble()
    )
    val luma = 0.299 * avg.r + 0.587 * avg.g + 0.114 * avg.b
    return RegionStats(id, role, avg, dominant, luma)
}

fun sampleRegion(bitmap: Bitmap, rect: NormRect, id: String, role: Role): RegionStats {
    val pixels = IntArray(bitmap.width * bitmap.height)
    bitmap.getPixels(pixels, 0, bitmap.width, 0, 0, bitmap.width, bitmap.height)
    return sampleRegionPixels(pixels, bitmap.width, bitmap.height, rect, id, role)
}

/**
 * Map a container-normalized rect (where a text block sits ON SCREEN) into the
 * image-normalized rect of the source bitmap, accounting for `ContentScale.Crop`
 * (scale-to-fill + center-crop). Without this, sampling the full bitmap at the
 * block's screen position reads pixels the user never sees, so the APCA result
 * wouldn't match what's actually rendered behind the text. Degrades to the input
 * rect when container size is unknown (0).
 */
fun cropMapRect(imgW: Int, imgH: Int, containerW: Float, containerH: Float, rect: NormRect): NormRect {
    if (containerW <= 0f || containerH <= 0f || imgW <= 0 || imgH <= 0) return rect
    val scale = maxOf(containerW / imgW, containerH / imgH)
    val offsetX = (imgW * scale - containerW) / 2f  // cropped-off margin (scaled px)
    val offsetY = (imgH * scale - containerH) / 2f
    fun toU(cx: Float) = ((cx + offsetX) / scale / imgW).toDouble().coerceIn(0.0, 1.0)
    fun toV(cy: Float) = ((cy + offsetY) / scale / imgH).toDouble().coerceIn(0.0, 1.0)
    val u0 = toU(rect.x.toFloat() * containerW)
    val v0 = toV(rect.y.toFloat() * containerH)
    val u1 = toU((rect.x + rect.w).toFloat() * containerW)
    val v1 = toV((rect.y + rect.h).toFloat() * containerH)
    return NormRect(u0, v0, u1 - u0, v1 - v0)
}
