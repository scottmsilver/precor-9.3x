import Foundation
import Network

struct DiscoveredTreadmill: Identifiable, Equatable {
    var id: String { name }
    let name: String
    let baseURL: String
}

/// Browses _treadmill._tcp via NWBrowser while running. Resolves each result
/// to host/port/TXT and maps via discoveredBaseURL(). Thin platform glue;
/// verified on-device (gated — no iOS device currently available).
@MainActor
final class TreadmillDiscovery: ObservableObject {
    @Published private(set) var found: [DiscoveredTreadmill] = []
    private var browser: NWBrowser?

    func start() {
        guard browser == nil else { return }
        let params = NWParameters()
        params.includePeerToPeer = false
        let b = NWBrowser(
            for: .bonjourWithTXTRecord(type: "_treadmill._tcp", domain: nil),
            using: params)
        b.browseResultsChangedHandler = { [weak self] results, _ in
            for r in results {
                guard case let .service(name, _, _, _) = r.endpoint else { continue }
                var scheme = "https"
                if case let .bonjour(txt) = r.metadata,
                   let s = txt["scheme"], !s.isEmpty { scheme = s }
                self?.resolve(endpoint: r.endpoint, name: name, scheme: scheme)
            }
        }
        b.start(queue: .main)
        browser = b
    }

    func stop() {
        browser?.cancel()
        browser = nil
        found = []
    }

    private func resolve(endpoint: NWEndpoint, name: String, scheme: String) {
        let conn = NWConnection(to: endpoint, using: .tcp)
        conn.stateUpdateHandler = { [weak self] state in
            guard case .ready = state,
                  case let .hostPort(host, port)? = conn.currentPath?.remoteEndpoint
            else { return }
            // Strip IPv6 zone-id (e.g. "fe80::1%en0" -> "fe80::1") for URL use.
            let h = "\(host)".split(separator: "%").first.map(String.init) ?? "\(host)"
            let url = discoveredBaseURL(
                host: h, port: Int(port.rawValue), txt: ["scheme": scheme])
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
