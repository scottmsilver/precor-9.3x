package com.precor.treadmill.ui.viewmodel

/** Serializes voice enablement changes with the final microphone boundary. */
internal class VoiceActivationGate {
    private var enabled = false
    private var generation = 0L

    @Synchronized
    fun setEnabled(enabled: Boolean): Boolean {
        if (this.enabled == enabled) return false
        this.enabled = enabled
        generation += 1
        return true
    }

    @Synchronized
    fun isEnabled(): Boolean = enabled

    @Synchronized
    fun beginSession(): Long {
        generation += 1
        return generation
    }

    @Synchronized
    fun isActive(sessionGeneration: Long): Boolean =
        enabled && generation == sessionGeneration

    fun runIfActive(sessionGeneration: Long, action: () -> Unit): Boolean = synchronized(this) {
        if (!enabled || generation != sessionGeneration) return@synchronized false
        action()
        true
    }
}
