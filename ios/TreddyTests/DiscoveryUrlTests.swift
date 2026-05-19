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
