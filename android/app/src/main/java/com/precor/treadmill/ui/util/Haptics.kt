package com.precor.treadmill.ui.util

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/** Get system vibrator service. */
private fun getVibrator(context: Context): Vibrator {
    return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val manager = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager
        manager.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
    }
}

/** Trigger a simple haptic vibration. */
fun haptic(context: Context, millis: Long = 10) {
    val vibrator = getVibrator(context)
    vibrator.vibrate(VibrationEffect.createOneShot(millis, VibrationEffect.DEFAULT_AMPLITUDE))
}

/** Trigger a patterned haptic vibration. Pattern: [pause, vibrate, pause, vibrate, ...]. */
fun haptic(context: Context, pattern: LongArray) {
    val vibrator = getVibrator(context)
    // Convert pattern from [duration, duration, ...] to VibrationEffect waveform
    // Web pattern is just vibration durations; Android expects [delay, vibrate, delay, vibrate...]
    val waveform = LongArray(pattern.size * 2)
    for (i in pattern.indices) {
        waveform[i * 2] = if (i == 0) 0 else 30 // pause between vibrations
        waveform[i * 2 + 1] = pattern[i]
    }
    vibrator.vibrate(VibrationEffect.createWaveform(waveform, -1))
}
