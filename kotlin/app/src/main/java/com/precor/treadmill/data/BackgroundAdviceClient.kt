package com.precor.treadmill.data

import com.precor.treadmill.ui.theme.readability.AdvicePrior
import org.json.JSONObject

/** Postel's Law: never throw on server output; unknown/missing -> defaults. */
fun parseAdvicePrior(body: String): AdvicePrior = try {
    val j = JSONObject(body)
    AdvicePrior(
        paletteHue = if (j.has("palette_hue") && !j.isNull("palette_hue")) j.getDouble("palette_hue") else null,
        suggestedPolarity = j.optString("suggested_polarity").takeIf { it == "light" || it == "dark" },
        mood = j.optString("mood").takeIf { it.isNotEmpty() },
    )
} catch (_: Exception) {
    AdvicePrior()
}

class BackgroundAdviceClient(private val baseUrl: String, private val httpGetPost: (String, String) -> String) {
    private val cache = HashMap<String, AdvicePrior>()
    /** Returns cached prior or fetches once. Falls back to neutral AdvicePrior on any error. */
    fun advise(imageHash: String, imageB64: () -> String): AdvicePrior {
        cache[imageHash]?.let { return it }
        val prior = try {
            val body = """{"image_hash":"$imageHash","image_b64":"${imageB64()}"}"""
            parseAdvicePrior(httpGetPost("$baseUrl/api/background/advise", body))
        } catch (_: Exception) { AdvicePrior() }
        cache[imageHash] = prior
        return prior
    }
}
