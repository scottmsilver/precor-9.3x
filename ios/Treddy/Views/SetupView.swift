import SwiftUI

struct SetupView: View {
    @Environment(TreadmillStore.self) private var store
    @StateObject private var discovery = TreadmillDiscovery()
    @State private var urlText = "https://rpi:8000"
    @State private var connecting = false
    @State private var errorMessage: String?
    @State private var scanning = true

    var body: some View {
        VStack(spacing: 24) {
            Spacer()

            Text("Treddy")
                .font(.largeTitle.weight(.bold))

            Text("Enter your treadmill server address")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            VStack(spacing: 12) {
                // mDNS discovery: auto-connect single (in .onChange below);
                // picker when 2+; scanning indicator while looking; manual
                // form always remains as the zero-result fallback.
                if discovery.found.count > 1 {
                    Text("Select your treadmill")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    ForEach(discovery.found) { d in
                        Button {
                            Task { await awaitConnect(d.baseURL) }
                        } label: {
                            Text("\(d.name)  —  \(d.baseURL)")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.green)
                        .frame(maxWidth: 360)
                    }
                    Text("— or enter manually —")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                } else if scanning && discovery.found.isEmpty {
                    HStack {
                        ProgressView()
                        Text("Looking for your treadmill…")
                    }
                    .foregroundStyle(.secondary)
                }

                TextField("Server URL", text: $urlText)
                    .textFieldStyle(.roundedBorder)
                    .textContentType(.URL)
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .frame(maxWidth: 360)
                    .onSubmit(connect)

                if let err = errorMessage {
                    Text(err)
                        .font(.caption)
                        .foregroundStyle(.red)
                }

                Button(action: connect) {
                    if connecting {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    } else {
                        Text("Connect")
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(.green)
                .disabled(urlText.trimmingCharacters(in: .whitespaces).isEmpty || connecting)
                .frame(maxWidth: 360)
            }
            .padding(24)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 20))

            Spacer()
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear {
            let saved = UserDefaults.standard.string(forKey: "server_url") ?? ""
            if !saved.isEmpty { urlText = saved }
            discovery.start()
            Task { try? await Task.sleep(for: .seconds(4)); scanning = false }
        }
        .onDisappear { discovery.stop() }
        .onChange(of: discovery.found) { _, list in
            if list.count == 1 {
                Task { await awaitConnect(list[0].baseURL) }
            }
        }
    }

    private func connect() {
        let trimmed = urlText.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, !connecting else { return }

        // Basic URL validation
        guard trimmed.hasPrefix("http://") || trimmed.hasPrefix("https://") else {
            errorMessage = "URL must start with http:// or https://"
            return
        }
        errorMessage = nil
        Task { await awaitConnect(trimmed) }
    }

    /// Shared connect-and-poll path used by the manual button, the picker
    /// buttons, and the auto-connect-on-single-discovery flow.
    private func awaitConnect(_ url: String) async {
        connecting = true
        defer { connecting = false }
        store.serverURL = url
        for _ in 0..<10 {
            try? await Task.sleep(for: .milliseconds(500))
            if store.isConnected {
                store.completeSetup()
                return
            }
        }
        errorMessage = "Could not connect to \(url)"
    }
}

#Preview {
    SetupView()
        .environment(TreadmillStore())
}
