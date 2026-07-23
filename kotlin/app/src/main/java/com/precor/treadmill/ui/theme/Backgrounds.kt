package com.precor.treadmill.ui.theme

import com.precor.treadmill.R

/**
 * Running-screen background photos, selectable in Settings and persisted via
 * ServerPreferences.backgroundImage. The adaptive readability system re-tunes
 * every panel to whichever photo is active — backgrounds are interchangeable.
 */
object Backgrounds {
    data class Bg(val key: String, val label: String, val res: Int)

    val all = listOf(
        Bg("ridge", "Ridgeline", R.drawable.bg_ridge),
        Bg("lake", "Lake", R.drawable.bg_lake),
        Bg("forest", "Forest", R.drawable.bg_forest),
    )

    fun resFor(key: String): Int = all.firstOrNull { it.key == key }?.res ?: all.first().res
}
