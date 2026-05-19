package com.precor.treadmill.discovery

/**
 * Pure mapping from a resolved mDNS record to the app's base server URL.
 * No Android imports — unit-testable on the JVM. scheme defaults to https
 * (the Pi serves a self-signed cert; clients trust it — see precor-9_3x-41a).
 */
fun discoveredBaseUrl(host: String, port: Int, txt: Map<String, String>): String {
    val scheme = txt["scheme"]?.takeIf { it.isNotBlank() } ?: "https"
    return "$scheme://$host:$port"
}
