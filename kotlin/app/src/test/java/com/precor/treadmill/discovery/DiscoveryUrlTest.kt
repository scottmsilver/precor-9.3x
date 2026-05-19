package com.precor.treadmill.discovery

import org.junit.Assert.assertEquals
import org.junit.Test

class DiscoveryUrlTest {
    @Test fun buildsHttpsUrlFromTxtScheme() {
        assertEquals(
            "https://192.168.1.50:8000",
            discoveredBaseUrl(host = "192.168.1.50", port = 8000, txt = mapOf("scheme" to "https"))
        )
    }

    @Test fun defaultsToHttpsWhenSchemeMissing() {
        assertEquals(
            "https://rpi-zero.local:8000",
            discoveredBaseUrl(host = "rpi-zero.local", port = 8000, txt = emptyMap())
        )
    }

    @Test fun honorsHttpSchemeIfAdvertised() {
        assertEquals(
            "http://10.0.0.9:8080",
            discoveredBaseUrl(host = "10.0.0.9", port = 8080, txt = mapOf("scheme" to "http"))
        )
    }
}
