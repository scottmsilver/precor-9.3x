package com.precor.treadmill.discovery

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

data class DiscoveredTreadmill(val name: String, val baseUrl: String)

private const val SERVICE_TYPE = "_treadmill._tcp."

/**
 * Browses [_treadmill._tcp] via [NsdManager] while [start]ed. Emits the set
 * of resolved treadmills. Thin platform glue — the testable logic is
 * [discoveredBaseUrl]. Verified on-LAN (gated on the Pi advertising).
 */
class TreadmillDiscovery(context: Context) {
    private val nsd = context.applicationContext
        .getSystemService(Context.NSD_SERVICE) as NsdManager

    private val _found = MutableStateFlow<List<DiscoveredTreadmill>>(emptyList())
    val found: StateFlow<List<DiscoveredTreadmill>> = _found

    private var listener: NsdManager.DiscoveryListener? = null

    fun start() {
        if (listener != null) return
        val l = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(s: String) {}
            override fun onDiscoveryStopped(s: String) {}
            override fun onStartDiscoveryFailed(s: String, e: Int) {}
            override fun onStopDiscoveryFailed(s: String, e: Int) {}
            override fun onServiceLost(info: NsdServiceInfo) {
                _found.update { list -> list.filterNot { it.name == info.serviceName } }
            }
            override fun onServiceFound(info: NsdServiceInfo) {
                nsd.resolveService(info, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(i: NsdServiceInfo, e: Int) {}
                    override fun onServiceResolved(i: NsdServiceInfo) {
                        val txt = i.attributes.orEmpty()
                            .mapValues { (_, v) -> v?.toString(Charsets.UTF_8) ?: "" }
                        val host = i.host?.hostAddress ?: return
                        val url = discoveredBaseUrl(host, i.port, txt)
                        val item = DiscoveredTreadmill(i.serviceName ?: host, url)
                        _found.update { list ->
                            (list.filterNot { it.name == item.name } + item)
                        }
                    }
                })
            }
        }
        listener = l
        nsd.discoverServices(SERVICE_TYPE, NsdManager.PROTOCOL_DNS_SD, l)
    }

    fun stop() {
        listener?.let { runCatching { nsd.stopServiceDiscovery(it) } }
        listener = null
        _found.value = emptyList()
    }
}
