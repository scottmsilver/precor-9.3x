import Foundation

/// Pure mapping from a resolved mDNS record to the app's base server URL.
/// No platform imports beyond Foundation — testable on macOS or any
/// non-iOS host. scheme defaults to https (the Pi serves a self-signed
/// cert; iOS trusts it via TrustAllDelegate — see precor-9_3x-41a).
func discoveredBaseURL(host: String, port: Int, txt: [String: String]) -> String {
    let scheme = (txt["scheme"]?.isEmpty == false) ? txt["scheme"]! : "https"
    return "\(scheme)://\(host):\(port)"
}
