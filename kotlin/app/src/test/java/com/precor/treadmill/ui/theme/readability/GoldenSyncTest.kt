package com.precor.treadmill.ui.theme.readability

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class GoldenSyncTest {
    @Test fun testResourceMatchesCanonicalGolden() {
        // Working dir for :app unit tests is kotlin/app ; canonical lives at repo-root/docs
        val canonicalFile = File("../../docs/bg-lab/golden.json")
        assertTrue(
            "canonical golden.json not found at ${canonicalFile.absolutePath} (cwd=${File(".").absolutePath})",
            canonicalFile.exists()
        )
        val canonical = canonicalFile.readText().filter { !it.isWhitespace() }
        val resource = javaClass.getResource("/golden.json")!!.readText().filter { !it.isWhitespace() }
        assertEquals("kotlin test golden.json drifted from docs/bg-lab/golden.json — re-copy it", canonical, resource)
    }
}
