package com.precor.treadmill.ui.theme.readability

import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.abs

class BrightRegionRegressionTest {
    /** A photo whose timer sits over a bright clearing: global average is "medium" but the local
     * region is bright. The engine must still produce text that clears the hero target there. */
    @Test fun brightLocalRegionStillMeetsHeroTarget() {
        val brightTimer = RegionStats("timer", Role.HERO, rgb(210,215,200), rgb(210,215,200), 210.0)
        val darkRest = RegionStats("speed", Role.BODY, rgb(40,55,45), rgb(40,55,45), 50.0)
        val theme = chooseTheme(listOf(brightTimer, darkRest), AdvicePrior()).theme
        val fit = fitRegion(theme, brightTimer)
        assertTrue("hero region must reach Lc>=75, got ${fit.lc}", abs(fit.lc) >= 75.0)
    }
}
