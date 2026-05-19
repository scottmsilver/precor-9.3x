import Foundation
import os

struct ConnectionStatusMessage: Codable {
    var connected: Bool = false
}

struct HeartRateMessage: Codable {
    var bpm: Int = 0
    var connected: Bool = false
    var device: String = ""
}

@MainActor
protocol TreadmillWebSocketClient: AnyObject {
    var onStatus: ((TreadmillStatus) -> Void)? { get set }
    var onProgram: ((ProgramState) -> Void)? { get set }
    var onSession: ((SessionState) -> Void)? { get set }
    var onConnection: ((Bool) -> Void)? { get set }
    var onHR: ((HeartRateMessage) -> Void)? { get set }
    var onScanResult: ((ScanResultMessage) -> Void)? { get set }
    var onProfileChanged: ((ProfileChangedMessage) -> Void)? { get set }
    var onConnect: (() -> Void)? { get set }
    var onDisconnect: (() -> Void)? { get set }

    func connect(to baseURL: String)
    func disconnect()
}

/// WebSocket client for real-time treadmill status updates.
/// Reconnects automatically with exponential backoff.
@Observable
@MainActor
final class TreadmillWebSocket: TreadmillWebSocketClient {
    private let logger = Logger(subsystem: "com.treddy", category: "WebSocket")
    private var task: URLSessionWebSocketTask?
    private var session: URLSession?
    private var serverURL: String = ""
    private var reconnectDelay: TimeInterval = 1.0
    private var shouldReconnect = true

    var isConnected = false

    var onStatus: ((TreadmillStatus) -> Void)?
    var onProgram: ((ProgramState) -> Void)?
    var onSession: ((SessionState) -> Void)?
    var onConnection: ((Bool) -> Void)?
    var onHR: ((HeartRateMessage) -> Void)?
    var onScanResult: ((ScanResultMessage) -> Void)?
    var onProfileChanged: ((ProfileChangedMessage) -> Void)?
    var onConnect: (() -> Void)?
    var onDisconnect: (() -> Void)?

    private let decoder = JSONDecoder()

    func connect(to baseURL: String) {
        shouldReconnect = true
        let wsURL = baseURL
            .replacingOccurrences(of: "https://", with: "wss://")
            .replacingOccurrences(of: "http://", with: "ws://")
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        serverURL = "\(wsURL)/ws"

        // Trust the Pi's self-signed cert for local HTTPS
        let config = URLSessionConfiguration.default
        session = URLSession(configuration: config, delegate: TrustAllDelegate(), delegateQueue: nil)

        doConnect()
    }

    func disconnect() {
        shouldReconnect = false
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        isConnected = false
    }

    private var hasReceivedFirstMessage = false

    private func doConnect() {
        guard let url = URL(string: serverURL) else { return }
        let ws = session!.webSocketTask(with: url)
        task = ws
        hasReceivedFirstMessage = false
        ws.resume()
        reconnectDelay = 1.0
        logger.info("Connecting to \(self.serverURL)")
        receiveLoop(ws)
    }

    private nonisolated func receiveLoop(_ ws: URLSessionWebSocketTask) {
        ws.receive { [weak self] result in
            guard let self = self else { return }
            Task { @MainActor in
                switch result {
                case .success(let message):
                    if case .string(let text) = message {
                        self.handleMessage(text)
                    }
                    self.receiveLoop(ws)
                case .failure:
                    self.handleDisconnect()
                }
            }
        }
    }

    private func handleMessage(_ text: String) {
        guard let data = text.data(using: .utf8) else { return }

        // Signal connection established on first message
        if !hasReceivedFirstMessage {
            hasReceivedFirstMessage = true
            isConnected = true
            onConnect?()
        }

        // Route by "type" field
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let type = json["type"] as? String {
            switch type {
            case "status":
                if let status = try? decoder.decode(TreadmillStatus.self, from: data) {
                    self.onStatus?(status)
                }
            case "program":
                if let prog = try? decoder.decode(ProgramState.self, from: data) {
                    self.onProgram?(prog)
                }
            case "session":
                if let sess = try? decoder.decode(SessionState.self, from: data) {
                    self.onSession?(sess)
                }
            case "connection":
                if let connection = try? decoder.decode(ConnectionStatusMessage.self, from: data) {
                    self.onConnection?(connection.connected)
                }
            case "hr":
                if let hr = try? decoder.decode(HeartRateMessage.self, from: data) {
                    self.onHR?(hr)
                }
            case "scan_result":
                if let scan = try? decoder.decode(ScanResultMessage.self, from: data) {
                    self.onScanResult?(scan)
                }
            case "profile_changed":
                if let msg = try? decoder.decode(ProfileChangedMessage.self, from: data) {
                    self.onProfileChanged?(msg)
                }
            default:
                break
            }
        }
    }

    private func handleDisconnect() {
        isConnected = false
        logger.info("Disconnected")
        onDisconnect?()

        guard shouldReconnect else { return }
        let delay = reconnectDelay
        reconnectDelay = min(reconnectDelay * 2, 30)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self = self, self.shouldReconnect else { return }
            self.logger.info("Reconnecting in \(delay)s")
            self.doConnect()
        }
    }
}

// TrustAllDelegate is in TrustAllDelegate.swift (shared with TreadmillAPI)
