# mDNS Device Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Native apps auto-discover the treadmill on the LAN via mDNS/DNS-SD, with manual entry retained as fallback.

**Architecture:** The Pi advertises one DNS-SD service (`_treadmill._tcp`, TXT `scheme=https`) via a static Avahi service file installed through the existing manifest. Android (`NsdManager`) and iOS (`NWBrowser`) browse it, build a base URL from the resolved host+port+TXT, and feed it into the existing setup/persistence flow. The web UI is unchanged (same-origin).

**Tech Stack:** Avahi (Pi), Android `NsdManager` (no new deps), iOS `NWBrowser`/Network framework, JUnit4 + XCTest for the pure URL-mapping units.

**Spec:** `docs/superpowers/specs/2026-05-18-mdns-device-discovery-design.md` (Approved 2026-05-19). HTTPS prerequisite (`precor-9_3x-41a`) is satisfied.

**Availability constraints (2026-05-19):** `rpi-zero` is offline and no iOS device is on hand. Every task's code + pure-unit tests are doable now; steps needing the live Pi or an iOS device are explicitly marked **[GATED]** and tracked as deferred beads issues, not blockers.

---

## File Structure

- `deploy/treadmill.avahi-service` — **create**. Avahi DNS-SD service definition (XML).
- `deploy/manifest.txt` — **modify**. One `file` row installing the above to `/etc/avahi/services/`.
- `deploy/lib-artifacts.sh:33-36` — **modify**. Extend the dest allowlist to permit `/etc/avahi/services/`.
- `deploy/deploy.sh:31` — **modify**. Stage the new file into `build/`.
- `deploy/setup.sh` — **modify**. `avahi-daemon` OS-prereq + `systemctl enable --now`.
- `deploy/ship-check.sh` — **modify**. Non-belt `avahi-browse` assertion.
- `kotlin/app/src/main/java/com/precor/treadmill/discovery/DiscoveryUrl.kt` — **create**. Pure URL builder (no Android imports — JVM-testable).
- `kotlin/app/src/test/java/com/precor/treadmill/discovery/DiscoveryUrlTest.kt` — **create**. JVM unit test.
- `kotlin/app/src/main/java/com/precor/treadmill/discovery/TreadmillDiscovery.kt` — **create**. `NsdManager` wrapper.
- `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/setup/SetupScreen.kt` — **modify**. 3-state discovery UX.
- `ios/Treddy/Services/DiscoveryUrl.swift` — **create**. Pure URL builder.
- `ios/TreddyTests/DiscoveryUrlTests.swift` — **create**. XCTest.
- `ios/Treddy/Services/TreadmillDiscovery.swift` — **create**. `NWBrowser` wrapper.
- `ios/Treddy/Views/SetupView.swift` — **modify**. 3-state discovery UX.
- `ios/gen_xcodeproj.py:159` — **modify**. Add Bonjour + Local Network Info.plist keys.
- `CLAUDE.md` — **modify**. Document the discovery mechanism + contract.
- `provisioning/dietpi/README.md` — **modify**. One line: device is mDNS-discoverable.

The pure URL builder is the only logic with real bug surface, so it is the only TDD-tested unit on each client. The `NsdManager`/`NWBrowser` wrappers are thin platform glue verified on-device/LAN (gated).

---

## Task 1: Pi — Avahi service file + manifest + allowlist

**Files:**
- Create: `deploy/treadmill.avahi-service`
- Modify: `deploy/lib-artifacts.sh:33-36`, `deploy/manifest.txt`, `deploy/deploy.sh:31`
- Test: `deploy/tests/manifest-avahi.sh` (create)

- [ ] **Step 1: Write the failing test**

Create `deploy/tests/manifest-avahi.sh`:

```bash
#!/usr/bin/env bash
# Asserts the avahi service row is accepted by the manifest parser and the
# service XML is well-formed.
set -euo pipefail
cd "$(dirname "$0")/.."
source ./lib-artifacts.sh

manifest_rows ./manifest.txt | grep -q 'treadmill\.avahi-service' \
  || { echo "FAIL: avahi row rejected/absent in manifest_rows output"; exit 1; }

# XML well-formed (xmllint if present, else python).
if command -v xmllint >/dev/null 2>&1; then
  xmllint --noout treadmill.avahi-service
else
  python3 -c 'import xml.dom.minidom,sys; xml.dom.minidom.parse("treadmill.avahi-service")'
fi
echo "PASS: manifest accepts avahi row; service XML well-formed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bash deploy/tests/manifest-avahi.sh`
Expected: FAIL — `manifest_rows` rejects the (not-yet-added) row's dest, or the row/file is absent.

- [ ] **Step 3: Create the Avahi service file**

Create `deploy/treadmill.avahi-service`:

```xml
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Treadmill on %h</name>
  <service>
    <type>_treadmill._tcp</type>
    <port>8000</port>
    <txt-record>scheme=https</txt-record>
    <txt-record>path=/</txt-record>
  </service>
</service-group>
```

- [ ] **Step 4: Extend the manifest dest allowlist**

In `deploy/lib-artifacts.sh`, change the `case $dest in` block (lines 33-36):

```bash
    case $dest in
      /usr/local/bin/*|/etc/systemd/system/*|/etc/avahi/services/*|'~/'*|/home/*) ;;
      *) echo "manifest: dest outside allowed roots: $line" >&2; return 1 ;;
    esac
```

- [ ] **Step 5: Add the manifest row**

Append to `deploy/manifest.txt` (after the `unit` rows):

```
file  build/treadmill.avahi-service             /etc/avahi/services/treadmill.service 0644 root
```

- [ ] **Step 6: Stage the file into build/**

In `deploy/deploy.sh:31`, extend the aux copy:

```bash
  cp deploy/setup.sh deploy/lib-artifacts.sh deploy/manifest.txt deploy/treadmill.avahi-service build/
```

- [ ] **Step 7: Run test to verify it passes**

Run: `bash deploy/tests/manifest-avahi.sh`
Expected: `PASS: manifest accepts avahi row; service XML well-formed`

- [ ] **Step 8: Commit**

```bash
git add deploy/treadmill.avahi-service deploy/lib-artifacts.sh deploy/manifest.txt deploy/deploy.sh deploy/tests/manifest-avahi.sh
git commit -m "feat(deploy): advertise _treadmill._tcp via static Avahi service file"
```

---

## Task 2: Pi — avahi-daemon prerequisite + enable in setup.sh

**Files:**
- Modify: `deploy/setup.sh` (OS-prereq block ~lines 27-39; services-enable block ~lines 84-89)

- [ ] **Step 1: Add avahi-daemon to the OS-prereq check**

In `deploy/setup.sh`, in the prereq block, after the `openssl` line, add:

```bash
[ -x /usr/sbin/avahi-daemon ] || need="$need avahi-daemon"
```

And add to the prereq comment list:

```bash
#   - avahi-daemon       : publishes the _treadmill._tcp mDNS service
```

- [ ] **Step 2: Enable avahi-daemon**

In the services-enable section (near `[ -x /usr/local/bin/hrm-daemon ] && sudo systemctl enable hrm`), add:

```bash
sudo systemctl enable --now avahi-daemon 2>/dev/null || true
```

(Avahi auto-loads `/etc/avahi/services/*.service` and reloads on file change; no explicit reload needed.)

- [ ] **Step 3: Verify syntax**

Run: `bash -n deploy/setup.sh`
Expected: no output (syntax OK).

- [ ] **Step 4: [GATED — rpi-zero offline] Live verify**

When `rpi-zero` is reachable: `make deploy`, then
`ssh rpi-zero 'systemctl is-active avahi-daemon && avahi-browse -rpt _treadmill._tcp | grep -i treadmill'`
Expected: `active`; a resolved record with port 8000 and `scheme=https`. Tracked under the deferred Pi-verification beads issue (see Task 12).

- [ ] **Step 5: Commit**

```bash
git add deploy/setup.sh
git commit -m "feat(deploy): install + enable avahi-daemon for mDNS discovery"
```

---

## Task 3: Pi — ship-check avahi-browse assertion

**Files:**
- Modify: `deploy/ship-check.sh` (probe heredoc; add a check function + call before the summary at line ~313)

- [ ] **Step 1: Add the assertion**

In the `PYEOF` probe (the same heredoc that defines `ok`/`bad`/`warn`), add this function and call it in the non-belt section (it does not touch the belt):

```python
import subprocess
def check_mdns():
    try:
        out = subprocess.run(
            ["avahi-browse", "-rpt", "_treadmill._tcp"],
            capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        warn("mdns_advert", "avahi-browse failed: %s" % e); return
    # Resolved record line: '=;...;_treadmill._tcp;...;<port>;"scheme=https" ...'
    if any(l.startswith("=") and "_treadmill._tcp" in l and "8000" in l
           and "scheme=https" in l for l in out.splitlines()):
        ok("mdns_advert", "_treadmill._tcp resolves (port 8000, scheme=https)")
    else:
        bad("mdns_advert", "no resolved _treadmill._tcp record")
```

Call `check_mdns()` alongside the other non-belt checks (before the `if fails:` summary).

- [ ] **Step 2: Verify syntax**

Run: `bash -n deploy/ship-check.sh`
Expected: no output.

- [ ] **Step 3: [GATED — rpi-zero offline] Live verify**

When reachable: `make ship-check-nobelt` → the `mdns_advert` line is `PASS`. Tracked under the deferred Pi-verification beads issue.

- [ ] **Step 4: Commit**

```bash
git add deploy/ship-check.sh
git commit -m "test(deploy): ship-check asserts _treadmill._tcp mDNS advert"
```

---

## Task 4: Kotlin — pure discovery URL builder (TDD)

**Files:**
- Create: `kotlin/app/src/main/java/com/precor/treadmill/discovery/DiscoveryUrl.kt`
- Test: `kotlin/app/src/test/java/com/precor/treadmill/discovery/DiscoveryUrlTest.kt`

- [ ] **Step 1: Write the failing test**

Create `kotlin/app/src/test/java/com/precor/treadmill/discovery/DiscoveryUrlTest.kt`:

```kotlin
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "com.precor.treadmill.discovery.DiscoveryUrlTest"`
Expected: FAIL — `discoveredBaseUrl` unresolved reference.

- [ ] **Step 3: Write minimal implementation**

Create `kotlin/app/src/main/java/com/precor/treadmill/discovery/DiscoveryUrl.kt`:

```kotlin
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd kotlin && ./gradlew :app:testDebugUnitTest --tests "com.precor.treadmill.discovery.DiscoveryUrlTest"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/discovery/DiscoveryUrl.kt kotlin/app/src/test/java/com/precor/treadmill/discovery/DiscoveryUrlTest.kt
git commit -m "feat(android): pure mDNS->baseUrl mapping (precor-9_3x-41a contract)"
```

---

## Task 5: Kotlin — NsdManager discovery wrapper

**Files:**
- Create: `kotlin/app/src/main/java/com/precor/treadmill/discovery/TreadmillDiscovery.kt`

- [ ] **Step 1: Write the wrapper**

Create `kotlin/app/src/main/java/com/precor/treadmill/discovery/TreadmillDiscovery.kt`:

```kotlin
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
 * Browses _treadmill._tcp via NsdManager while [start]ed. Emits the set of
 * resolved treadmills. Thin platform glue — the testable logic is
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
```

- [ ] **Step 2: Verify it compiles**

Run: `cd kotlin && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/discovery/TreadmillDiscovery.kt
git commit -m "feat(android): NsdManager _treadmill._tcp discovery wrapper"
```

---

## Task 6: Kotlin — SetupScreen 3-state discovery UX

**Files:**
- Modify: `kotlin/app/src/main/java/com/precor/treadmill/ui/screens/setup/SetupScreen.kt`

- [ ] **Step 1: Implement the UX**

In `SetupScreen.kt`, inject discovery and add the three states. Add to the composable (keep the existing manual form as the zero-result fallback). Insert near the top of `SetupScreen`, after `val scope = rememberCoroutineScope()`:

```kotlin
    val context = androidx.compose.ui.platform.LocalContext.current
    val discovery = remember { com.precor.treadmill.discovery.TreadmillDiscovery(context) }
    val found by discovery.found.collectAsState()
    var scanning by remember { mutableStateOf(true) }

    DisposableEffect(Unit) {
        discovery.start()
        onDispose { discovery.stop() }
    }
    // ~4s scan window, then reveal manual entry regardless.
    LaunchedEffect(Unit) { kotlinx.coroutines.delay(4000); scanning = false }

    // Auto-connect on a single discovered device.
    LaunchedEffect(found) {
        if (found.size == 1) {
            serverPreferences.setServerUrl(found.first().baseUrl.trimEnd('/'))
            discovery.stop()
            onConnected()
        }
    }
```

Then, in the `Column` inside the `Card`, render discovery state above the existing manual `OutlinedTextField` block:

```kotlin
                if (found.size > 1) {
                    Text("Select your treadmill", style = MaterialTheme.typography.bodyMedium, color = colors.text2)
                    found.forEach { d ->
                        Button(
                            onClick = {
                                scope.launch {
                                    serverPreferences.setServerUrl(d.baseUrl.trimEnd('/'))
                                    discovery.stop(); onConnected()
                                }
                            },
                            modifier = Modifier.fillMaxWidth(),
                            colors = ButtonDefaults.buttonColors(containerColor = colors.green, contentColor = colors.bg),
                        ) { Text("${d.name}  —  ${d.baseUrl}") }
                    }
                    Text("— or enter manually —", style = MaterialTheme.typography.bodySmall, color = colors.text3)
                } else if (scanning && found.isEmpty()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        CircularProgressIndicator(modifier = Modifier.size(18.dp), strokeWidth = 2.dp, color = colors.green)
                        Spacer(Modifier.width(8.dp))
                        Text("Looking for your treadmill…", style = MaterialTheme.typography.bodyMedium, color = colors.text2)
                    }
                }
```

(The single-result case is handled by the auto-connect `LaunchedEffect`; the existing manual field always remains below as the zero-result fallback.)

- [ ] **Step 2: Verify it compiles**

Run: `cd kotlin && ./gradlew :app:compileDebugKotlin`
Expected: BUILD SUCCESSFUL.

- [ ] **Step 3: Build the APK**

Run: `cd kotlin && ./gradlew assembleDebug`
Expected: BUILD SUCCESSFUL; APK at `kotlin/app/build/outputs/apk/debug/app-debug.apk`.

- [ ] **Step 4: [GATED — rpi-zero offline] On-LAN verify**

When the Pi advertises: install the APK on the tablet (discover endpoint via `adb mdns services`, see project memory), launch with no saved server URL, confirm it auto-connects (single Pi) or shows the picker (primary+spare). Tracked under the deferred Pi-verification beads issue.

- [ ] **Step 5: Commit**

```bash
git add kotlin/app/src/main/java/com/precor/treadmill/ui/screens/setup/SetupScreen.kt
git commit -m "feat(android): mDNS auto/picker/manual setup UX"
```

---

## Task 7: iOS — pure discovery URL builder (TDD)

**Files:**
- Create: `ios/Treddy/Services/DiscoveryUrl.swift`
- Test: `ios/TreddyTests/DiscoveryUrlTests.swift`

- [ ] **Step 1: Write the failing test**

Create `ios/TreddyTests/DiscoveryUrlTests.swift`:

```swift
import XCTest
@testable import Treddy

final class DiscoveryUrlTests: XCTestCase {
    func testHttpsFromTxtScheme() {
        XCTAssertEqual(
            discoveredBaseURL(host: "192.168.1.50", port: 8000, txt: ["scheme": "https"]),
            "https://192.168.1.50:8000")
    }
    func testDefaultsToHttps() {
        XCTAssertEqual(
            discoveredBaseURL(host: "rpi-zero.local", port: 8000, txt: [:]),
            "https://rpi-zero.local:8000")
    }
    func testHonorsHttp() {
        XCTAssertEqual(
            discoveredBaseURL(host: "10.0.0.9", port: 8080, txt: ["scheme": "http"]),
            "http://10.0.0.9:8080")
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run (on Mac via SSH, see project memory for iOS build): `xcodebuild test -scheme Treddy -destination 'platform=iOS Simulator,name=iPhone 15' -only-testing:TreddyTests/DiscoveryUrlTests`
Expected: FAIL — `discoveredBaseURL` unresolved.

- [ ] **Step 3: Write minimal implementation**

Create `ios/Treddy/Services/DiscoveryUrl.swift`:

```swift
import Foundation

/// Pure mapping from a resolved mDNS record to the app's base server URL.
/// scheme defaults to https (Pi serves a self-signed cert; iOS trusts it via
/// TrustAllDelegate — see precor-9_3x-41a).
func discoveredBaseURL(host: String, port: Int, txt: [String: String]) -> String {
    let scheme = (txt["scheme"]?.isEmpty == false) ? txt["scheme"]! : "https"
    return "\(scheme)://\(host):\(port)"
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `xcodebuild test -scheme Treddy -destination 'platform=iOS Simulator,name=iPhone 15' -only-testing:TreddyTests/DiscoveryUrlTests`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add ios/Treddy/Services/DiscoveryUrl.swift ios/TreddyTests/DiscoveryUrlTests.swift
git commit -m "feat(ios): pure mDNS->baseURL mapping (precor-9_3x-41a contract)"
```

---

## Task 8: iOS — NWBrowser discovery wrapper

**Files:**
- Create: `ios/Treddy/Services/TreadmillDiscovery.swift`

- [ ] **Step 1: Write the wrapper**

Create `ios/Treddy/Services/TreadmillDiscovery.swift`:

```swift
import Foundation
import Network

struct DiscoveredTreadmill: Identifiable, Equatable {
    var id: String { name }
    let name: String
    let baseURL: String
}

/// Browses _treadmill._tcp via NWBrowser while running. Resolves each result
/// to host/port/TXT and maps via discoveredBaseURL(). Thin platform glue;
/// verified on-device (gated — no iOS device currently).
@MainActor
final class TreadmillDiscovery: ObservableObject {
    @Published private(set) var found: [DiscoveredTreadmill] = []
    private var browser: NWBrowser?

    func start() {
        guard browser == nil else { return }
        let params = NWParameters()
        params.includePeerToPeer = false
        let b = NWBrowser(for: .bonjourWithTXTRecord(type: "_treadmill._tcp", domain: nil), using: params)
        b.browseResultsChangedHandler = { [weak self] results, _ in
            for r in results {
                guard case let .service(name, _, _, _) = r.endpoint else { continue }
                var scheme = "https"
                if case let .bonjour(txt) = r.metadata, let s = txt["scheme"], !s.isEmpty { scheme = s }
                self?.resolve(endpoint: r.endpoint, name: name, scheme: scheme)
            }
        }
        b.start(queue: .main)
        browser = b
    }

    func stop() {
        browser?.cancel(); browser = nil; found = []
    }

    private func resolve(endpoint: NWEndpoint, name: String, scheme: String) {
        let conn = NWConnection(to: endpoint, using: .tcp)
        conn.stateUpdateHandler = { [weak self] state in
            guard case .ready = state,
                  case let .hostPort(host, port)? = conn.currentPath?.remoteEndpoint else { return }
            let h = "\(host)".split(separator: "%").first.map(String.init) ?? "\(host)"
            let url = discoveredBaseURL(host: h, port: Int(port.rawValue),
                                        txt: ["scheme": scheme])
            let item = DiscoveredTreadmill(name: name, baseURL: url)
            Task { @MainActor in
                self?.found.removeAll { $0.name == name }
                self?.found.append(item)
            }
            conn.cancel()
        }
        conn.start(queue: .main)
    }
}
```

- [ ] **Step 2: Verify it compiles**

Run (on Mac): `xcodebuild build -scheme Treddy -destination 'generic/platform=iOS'`
Expected: BUILD SUCCEEDED.

- [ ] **Step 3: Commit**

```bash
git add ios/Treddy/Services/TreadmillDiscovery.swift
git commit -m "feat(ios): NWBrowser _treadmill._tcp discovery wrapper"
```

---

## Task 9: iOS — Info.plist Bonjour + Local Network keys

**Files:**
- Modify: `ios/gen_xcodeproj.py:159`

- [ ] **Step 1: Add the keys**

In `ios/gen_xcodeproj.py`, immediately after the line:

```python
    a('                INFOPLIST_KEY_NSMicrophoneUsageDescription = "Voice control for your treadmill";')
```

add:

```python
    a('                INFOPLIST_KEY_NSLocalNetworkUsageDescription = "Find your treadmill on the local network.";')
    a('                INFOPLIST_KEY_NSBonjourServices = "_treadmill._tcp";')
```

(Xcode promotes the `INFOPLIST_KEY_NSBonjourServices` build setting into the generated Info.plist as a one-element `NSBonjourServices` array.)

- [ ] **Step 2: Regenerate + verify the project**

Run: `python3 ios/gen_xcodeproj.py` then (on Mac) `xcodebuild build -scheme Treddy -destination 'generic/platform=iOS'`
Expected: regenerates `ios/Treddy.xcodeproj`; BUILD SUCCEEDED.

- [ ] **Step 3: Commit**

```bash
git add ios/gen_xcodeproj.py ios/Treddy.xcodeproj/project.pbxproj
git commit -m "feat(ios): declare _treadmill._tcp Bonjour + Local Network usage"
```

---

## Task 10: iOS — SetupView 3-state discovery UX

**Files:**
- Modify: `ios/Treddy/Views/SetupView.swift`

- [ ] **Step 1: Implement the UX**

In `SetupView.swift`, add `@StateObject private var discovery = TreadmillDiscovery()` and `@State private var scanning = true`. In `.onAppear`, after the saved-URL load, add:

```swift
            discovery.start()
            Task { try? await Task.sleep(for: .seconds(4)); scanning = false }
```

Add `.onDisappear { discovery.stop() }`. Add an `.onChange(of: discovery.found)` that auto-connects a single result:

```swift
        .onChange(of: discovery.found) { _, list in
            if list.count == 1 {
                store.serverURL = list[0].baseURL
                discovery.stop()
            }
        }
```

(`store.serverURL`'s `didSet` already persists + reconnects; the existing poll-then-`completeSetup()` in `connect()` path is reused — extract the connect-poll into a `func awaitConnect(_ url: String)` and call it from both the manual button and the auto/picker paths.)

In the `VStack`, above the manual `TextField`, render:

```swift
            if discovery.found.count > 1 {
                Text("Select your treadmill").font(.subheadline).foregroundStyle(.secondary)
                ForEach(discovery.found) { d in
                    Button("\(d.name)  —  \(d.baseURL)") {
                        store.serverURL = d.baseURL; discovery.stop()
                    }
                    .buttonStyle(.borderedProminent).tint(.green).frame(maxWidth: 360)
                }
                Text("— or enter manually —").font(.caption).foregroundStyle(.tertiary)
            } else if scanning && discovery.found.isEmpty {
                HStack { ProgressView(); Text("Looking for your treadmill…") }
                    .foregroundStyle(.secondary)
            }
```

- [ ] **Step 2: Verify it compiles**

Run (on Mac): `xcodebuild build -scheme Treddy -destination 'generic/platform=iOS'`
Expected: BUILD SUCCEEDED.

- [ ] **Step 3: [GATED — no iOS device] On-device verify**

When an iOS device is available and the Pi advertises: launch Treddy with no saved URL; confirm auto-connect (single) or picker (primary+spare), and manual fallback when discovery finds nothing. Tracked under `precor-9_3x-2ef`-style deferred verification.

- [ ] **Step 4: Commit**

```bash
git add ios/Treddy/Views/SetupView.swift
git commit -m "feat(ios): mDNS auto/picker/manual setup UX"
```

---

## Task 11: Docs — CLAUDE.md + provisioning note

**Files:**
- Modify: `CLAUDE.md`, `provisioning/dietpi/README.md`

- [ ] **Step 1: Document the discovery mechanism**

In `CLAUDE.md`, under the Deployment section, add a subsection:

```markdown
### Device Discovery (mDNS)

The Pi advertises one DNS-SD service via a static Avahi file
(`/etc/avahi/services/treadmill.service`, installed from
`deploy/treadmill.avahi-service` through the manifest): type
`_treadmill._tcp`, port 8000, TXT `scheme=https`, `path=/`. Native apps
(Android `NsdManager`, iOS `NWBrowser`) discover it and pre-fill/auto-connect
the setup screen; manual entry remains the fallback. The web UI is exempt
(same-origin). The `scheme=https` contract depends on the self-signed-cert
work (`precor-9_3x-41a`).
```

- [ ] **Step 2: Provisioning note**

In `provisioning/dietpi/README.md`, add one line near the SSH-reach note: "Once deployed, the treadmill is discoverable on the LAN as `_treadmill._tcp` (apps auto-find it; no IP entry needed)."

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md provisioning/dietpi/README.md
git commit -m "docs: document _treadmill._tcp mDNS discovery"
```

---

## Task 12: Backlog + deferred-verification beads issues

- [ ] **Step 1: File the BLE onboarding backlog issue**

```bash
bd create --type=feature --priority=4 \
  --title="BLE WiFi onboarding (screenless first-network setup)" \
  --description="Future option from the mDNS spec: BLE solves zero-network onboarding (push WiFi creds to a screenless treadmill before it can be on the LAN); mDNS then handles steady-state. Non-blocking, out of scope for the mDNS plan. See docs/superpowers/specs/2026-05-18-mdns-device-discovery-design.md."
```

- [ ] **Step 2: File the gated live-verification issue**

```bash
bd create --type=task --priority=2 \
  --title="mDNS: live-verify advert + on-device discovery (gated)" \
  --description="Blocked on hardware availability: (a) rpi-zero offline -> verify avahi-browse _treadmill._tcp + ship-check mdns_advert PASS after deploy; (b) Android on-LAN auto/picker; (c) iOS device unavailable -> on-device auto/picker. All code + pure-unit tests already landed."
bd defer <id> --until="2026-06-02"
```

- [ ] **Step 3: Commit (beads export)**

```bash
git add .beads/issues.jsonl .beads/last-touched
git commit -m "chore(beads): file BLE-onboarding backlog + gated mDNS verification"
```

---

## Self-Review

**Spec coverage:** Service contract → Task 1. Static Avahi file + manifest + allowlist → Task 1. avahi-daemon prereq/enable → Task 2. ship-check assertion → Task 3. Kotlin discovery + URL mapping + 3-state UX → Tasks 4-6. iOS discovery + URL mapping + Info.plist + 3-state UX → Tasks 7-10. Docs → Task 11. BLE backlog → Task 12. Web UI explicitly unchanged (no task, by design). All spec sections mapped.

**Placeholder scan:** No TBD/TODO. Every code step has complete code. `[GATED]` steps are explicit verification deferrals (hardware unavailable), each with a concrete command to run when unblocked and a beads issue — not vague placeholders.

**Type consistency:** `discoveredBaseUrl`/`discoveredBaseURL(host, port, txt)` consistent across Tasks 4/5 (Kotlin) and 7/8 (iOS). `DiscoveredTreadmill { name, baseURL }` consistent in Tasks 5/6 (Kotlin) and 8/10 (iOS). Service type string `_treadmill._tcp` consistent across Pi XML, Kotlin (`_treadmill._tcp.`), iOS, ship-check, docs.

---

## Open Risks

- `NsdManager.resolveService` is deprecated on API 34+ but functional on minSdk 28; `registerServiceInfoCallback` (API 34+) is a future refinement, not needed now (YAGNI).
- iOS `NWBrowser` host resolution via `NWConnection.currentPath` yields an IP; acceptable (clients trust-all, no hostname dependency). If a `.local` name is ever required, switch to `NetService` resolution.
- `INFOPLIST_KEY_NSBonjourServices` single-value→array promotion is the documented Xcode behavior; if a future second service type is needed, switch to an explicit Info.plist fragment.
- All on-device/on-Pi behavior is gated until `rpi-zero` and an iOS device are available (tracked, Task 12).
