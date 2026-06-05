package com.precor.treadmill.ui.theme.readability

import kotlin.math.abs
import kotlin.math.pow

private const val Rco=0.2126729; private const val Gco=0.7151522; private const val Bco=0.0721750
private const val normBG=0.56; private const val normTXT=0.57; private const val revTXT=0.62; private const val revBG=0.65
private const val blkThrs=0.022; private const val blkClmp=1.414; private const val loClip=0.1; private const val deltaYmin=0.0005
private const val scaleBoW=1.14; private const val loBoWoffset=0.027; private const val scaleWoB=1.14; private const val loWoBoffset=0.027

private fun toY(c: Rgb): Double {
    fun lin(v: Double) = (v/255.0).pow(2.4)
    var y = Rco*lin(c.r)+Gco*lin(c.g)+Bco*lin(c.b)
    if (y < blkThrs) y += (blkThrs - y).pow(blkClmp)
    return y
}

/** APCA Lc. Positive = dark text on light bg; negative = light text on dark bg. */
fun apcaLc(text: Rgb, bg: Rgb): Double {
    val txtY = toY(text); val bgY = toY(bg)
    if (abs(bgY-txtY) < deltaYmin) return 0.0
    val out: Double = if (bgY > txtY) {
        val s = (bgY.pow(normBG) - txtY.pow(normTXT)) * scaleBoW
        if (s < loClip) 0.0 else s - loBoWoffset
    } else {
        val s = (bgY.pow(revBG) - txtY.pow(revTXT)) * scaleWoB
        if (s > -loClip) 0.0 else s + loWoBoffset
    }
    return out * 100.0
}
