package com.precor.treadmill.data

import com.precor.treadmill.ui.theme.readability.AdvicePrior
import org.junit.Assert.assertEquals
import org.junit.Test

class BackgroundAdviceClientTest {
    @Test fun parsesFullJson() {
        val p = parseAdvicePrior("""{"palette_hue":158,"suggested_polarity":"dark","mood":"cool-forest"}""")
        assertEquals(158.0, p.paletteHue!!, 0.01)
        assertEquals("dark", p.suggestedPolarity)
    }
    @Test fun toleratesMissingAndUnknownFields() {
        val p = parseAdvicePrior("""{"unexpected":true}""")
        assertEquals(null, p.paletteHue)
        assertEquals(AdvicePrior(), p)  // all defaults
    }
    @Test fun toleratesGarbage() {
        assertEquals(AdvicePrior(), parseAdvicePrior("not json"))
    }
}
