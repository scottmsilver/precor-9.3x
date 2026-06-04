package com.precor.treadmill.ui.theme.readability

import kotlin.math.cbrt
import kotlin.math.pow
import kotlin.math.hypot
import kotlin.math.atan2
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

data class Rgb(val r: Double, val g: Double, val b: Double)
data class Oklab(val L: Double, val a: Double, val b: Double)
data class Oklch(val L: Double, val C: Double, val h: Double)

fun rgb(r: Int, g: Int, b: Int) = Rgb(r.toDouble(), g.toDouble(), b.toDouble())
fun hexToRgb(hex: String): Rgb {
    val s = hex.removePrefix("#")
    return rgb(s.substring(0,2).toInt(16), s.substring(2,4).toInt(16), s.substring(4,6).toInt(16))
}

private fun srgbToLinear(c: Double): Double { val x = c/255.0; return if (x <= 0.04045) x/12.92 else ((x+0.055)/1.055).pow(2.4) }
private fun linearToSrgb(x: Double): Double { val c = if (x <= 0.0031308) x*12.92 else 1.055*x.pow(1/2.4)-0.055; return c*255.0 }

fun rgbToOklab(c: Rgb): Oklab {
    val lr = srgbToLinear(c.r); val lg = srgbToLinear(c.g); val lb = srgbToLinear(c.b)
    val l = cbrt(0.4122214708*lr + 0.5363325363*lg + 0.0514459929*lb)
    val m = cbrt(0.2119034982*lr + 0.6806995451*lg + 0.1073969566*lb)
    val s = cbrt(0.0883024619*lr + 0.2817188376*lg + 0.6299787005*lb)
    return Oklab(0.2104542553*l+0.7936177850*m-0.0040720468*s,
                 1.9779984951*l-2.4285922050*m+0.4505937099*s,
                 0.0259040371*l+0.7827717662*m-0.8086757660*s)
}
fun oklabToRgb(o: Oklab): Rgb {
    val l = (o.L + 0.3963377774*o.a + 0.2158037573*o.b).pow(3)
    val m = (o.L - 0.1055613458*o.a - 0.0638541728*o.b).pow(3)
    val s = (o.L - 0.0894841775*o.a - 1.2914855480*o.b).pow(3)
    return Rgb(linearToSrgb(+4.0767416621*l-3.3077115913*m+0.2309699292*s),
               linearToSrgb(-1.2684380046*l+2.6097574011*m-0.3413193965*s),
               linearToSrgb(-0.0041960863*l-0.7034186147*m+1.7076147010*s))
}
fun rgbToOklch(c: Rgb): Oklch {
    val o = rgbToOklab(c); var h = atan2(o.b, o.a)*180/PI; if (h<0) h+=360
    return Oklch(o.L, hypot(o.a, o.b), h)
}
fun oklchToRgb(o: Oklch): Rgb {
    val r = o.h*PI/180; return oklabToRgb(Oklab(o.L, o.C*cos(r), o.C*sin(r)))
}
fun oklabDeltaE(a: Oklab, b: Oklab) = Math.sqrt((a.L-b.L)*(a.L-b.L)+(a.a-b.a)*(a.a-b.a)+(a.b-b.b)*(a.b-b.b))
