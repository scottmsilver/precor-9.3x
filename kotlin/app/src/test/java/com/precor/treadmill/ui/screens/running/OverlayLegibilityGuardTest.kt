package com.precor.treadmill.ui.screens.running

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural guard for "no text or widget on the background photo unless it went through APCA."
 *
 * Components that render directly over the photo must use [com.precor.treadmill.ui.theme.LegibleText]
 * (text) or [com.precor.treadmill.ui.theme.legibleOn] (widget draw colors), both of which run the
 * color through APCA against [com.precor.treadmill.ui.theme.LocalOverlayBackground] before drawing.
 * This test fails the build if a raw Compose `Text(` or a hardcoded faint-ivory text color
 * reappears in one of those components, so the guard can't be silently bypassed.
 *
 * Working dir for `:app` unit tests is `kotlin/app`.
 */
class OverlayLegibilityGuardTest {
    private val onPhotoComponents = listOf(
        "MetricsRow.kt",
        "SpeedInclineControls.kt",
        "ProgramHUD.kt",
    )
    private val dir = File("src/main/java/com/precor/treadmill/ui/screens/running")

    @Test
    fun onPhotoComponentsUseLegibleTextNotRawText() {
        val rawText = Regex("""\bText\(""") // matches `Text(` but not `LegibleText(` / `TextStyle(`
        for (name in onPhotoComponents) {
            val src = File(dir, name).readText()
            // Ignore import lines so `import ...material3.Text` isn't counted.
            val body = src.lineSequence().filterNot { it.trimStart().startsWith("import ") }.joinToString("\n")
            assertTrue(
                "$name renders a raw Text() over the photo — use LegibleText so the APCA guard cannot be skipped",
                !rawText.containsMatchIn(body),
            )
        }
    }

    @Test
    fun onPhotoComponentsHaveNoHardcodedFaintIvoryText() {
        // The 0x59/0x99-alpha ivory anti-pattern: de-emphasized text that washed out over bright photos.
        val faint = Regex("""Color\(0x(59|99)E8E4DF\)""")
        for (name in onPhotoComponents) {
            val src = File(dir, name).readText()
            assertTrue(
                "$name uses a hardcoded faint-ivory text color — drive it from the engine textColor / LegibleText instead",
                !faint.containsMatchIn(src),
            )
        }
    }
}
