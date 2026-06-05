package com.precor.treadmill.ui.screens.running

import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * Structural guard for "no text or widget on the background photo unless it went through APCA."
 *
 * Every composable in the running screen renders OVER the full-bleed background photo, so its
 * text must go through [com.precor.treadmill.ui.theme.LegibleText] (or, for free-floating hero
 * text that solves its own polarity, be explicitly marked `// legible-exempt: <why>`). A raw
 * Compose `Text(` or a hardcoded faint-ivory text color in any of these files fails the build —
 * so on-photo text can't be added without the legibility guard, and we don't discover these
 * places one at a time.
 *
 * Working dir for `:app` unit tests is `kotlin/app`.
 */
class OverlayLegibilityGuardTest {
    private val dir = File("src/main/java/com/precor/treadmill/ui/screens/running")

    // Files whose text genuinely never sits on the photo (none today) would be listed here.
    private val exemptFiles = emptySet<String>()

    // `Text(`/`BasicText(`/`ClickableText(` (but not `LegibleText(`/`TextStyle(`) and canvas
    // `drawText(` — every glyph-rendering primitive that paints over the photo. Adding any of
    // these without `// legible-exempt: why` fails the build.
    private val rawText = Regex("""(^|[^A-Za-z])(Text|BasicText|ClickableText)\(|\bdrawText\s*\(""")
    private val faintIvory = Regex("""Color\(0x(59|99)E8E4DF\)""")
    private val importLine = Regex("""^\s*import\s""")

    @Test
    fun noRawTextOverThePhoto() {
        val violations = mutableListOf<String>()
        for (file in dir.listFiles { f -> f.extension == "kt" }!!.sortedBy { it.name }) {
            if (file.name in exemptFiles) continue
            file.readLines().forEachIndexed { i, line ->
                if (importLine.containsMatchIn(line)) return@forEachIndexed
                if (line.contains("// legible-exempt")) return@forEachIndexed
                if (rawText.containsMatchIn(line)) violations.add("${file.name}:${i + 1}  $line".trim())
            }
        }
        assertTrue(
            "Raw Text() over the photo — use LegibleText (or mark a self-solving exception with " +
                "`// legible-exempt: why`):\n${violations.joinToString("\n")}",
            violations.isEmpty(),
        )
    }

    @Test
    fun guardFlagsTextRenderingEvasionsButNotLegibleWrappers() {
        // The primitives that paint glyphs over the photo must all be caught...
        for (s in listOf("Text(", "  Text(", "Row { Text(", "BasicText(", "ClickableText(", "drawText(", "drawText (")) {
            assertTrue("should flag: $s", rawText.containsMatchIn(s))
        }
        // ...but the sanctioned wrapper and unrelated tokens must NOT be flagged.
        for (s in listOf("LegibleText(", "TextStyle(", "TextMeasurer(", "val textColor =")) {
            assertTrue("should NOT flag: $s", !rawText.containsMatchIn(s))
        }
    }

    @Test
    fun noHardcodedFaintIvoryText() {
        val violations = mutableListOf<String>()
        for (file in dir.listFiles { f -> f.extension == "kt" }!!.sortedBy { it.name }) {
            file.readLines().forEachIndexed { i, line ->
                if (line.contains("// legible-exempt")) return@forEachIndexed
                if (faintIvory.containsMatchIn(line)) violations.add("${file.name}:${i + 1}")
            }
        }
        assertTrue(
            "Hardcoded faint-ivory text color — drive it from the engine textColor / LegibleText:\n" +
                violations.joinToString("\n"),
            violations.isEmpty(),
        )
    }
}
