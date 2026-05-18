import SwiftUI

struct SpeedInclineControls: View {
    @Environment(TreadmillStore.self) private var store
    var vertical: Bool = true

    var body: some View {
        if vertical {
            VStack(spacing: 8) {
                speedPanel
                    .frame(maxHeight: .infinity)
                inclinePanel
                    .frame(maxHeight: .infinity)
            }
        } else {
            HStack(spacing: 8) {
                speedPanel
                inclinePanel
            }
        }
    }

    private var speedPanel: some View {
        ControlPanel(
            value: String(format: "%.1f", store.status.speedMph),
            unit: "mph",
            valueColor: .green,
            smallUp: { Task { await store.adjustSpeed(delta: 1) } },
            smallDown: { Task { await store.adjustSpeed(delta: -1) } },
            bigUp: { Task { await store.adjustSpeed(delta: 10) } },
            bigDown: { Task { await store.adjustSpeed(delta: -10) } }
        )
        .disabled(!store.isConnected)
        .opacity(store.isConnected ? 1.0 : 0.4)
    }

    private var inclinePanel: some View {
        ControlPanel(
            value: String(format: "%.1f", store.status.inclinePct),
            unit: "% incline",
            valueColor: AppColors.text,
            smallUp: { Task { await store.adjustIncline(delta: 0.5) } },
            smallDown: { Task { await store.adjustIncline(delta: -0.5) } },
            bigUp: { Task { await store.adjustIncline(delta: 1.0) } },
            bigDown: { Task { await store.adjustIncline(delta: -1.0) } }
        )
        .disabled(!store.isConnected)
        .opacity(store.isConnected ? 1.0 : 0.4)
    }
}

struct ControlPanel: View {
    let value: String
    let unit: String
    var valueColor: Color = .primary
    let smallUp: () -> Void
    let smallDown: () -> Void
    let bigUp: () -> Void
    let bigDown: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            VStack(spacing: 6) {
                RepeatButton(action: smallUp) {
                    Image(systemName: "chevron.up")
                        .font(.system(size: 18, weight: .semibold))
                }
                RepeatButton(action: smallDown) {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 18, weight: .semibold))
                }
            }
            .frame(maxHeight: .infinity)
            .layoutPriority(-1)

            VStack(spacing: 2) {
                Spacer(minLength: 0)
                Text(value)
                    .font(.system(size: 44, weight: .bold).monospacedDigit())
                    .foregroundStyle(valueColor)
                    .lineLimit(1)
                    .fixedSize()
                Text(unit)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
            }
            .layoutPriority(1)

            VStack(spacing: 6) {
                RepeatButton(action: bigUp) {
                    VStack(spacing: -4) {
                        Image(systemName: "chevron.up")
                        Image(systemName: "chevron.up")
                    }
                    .font(.system(size: 14, weight: .semibold))
                }
                RepeatButton(action: bigDown) {
                    VStack(spacing: -4) {
                        Image(systemName: "chevron.down")
                        Image(systemName: "chevron.down")
                    }
                    .font(.system(size: 14, weight: .semibold))
                }
            }
            .frame(maxHeight: .infinity)
            .layoutPriority(-1)
        }
        .frame(maxHeight: .infinity)
        .padding(12)
        .glassPanel(cornerRadius: 16)
    }
}

struct RepeatButton<Label: View>: View {
    let action: () -> Void
    @ViewBuilder let label: () -> Label

    @State private var repeatTask: Task<Void, Never>?

    var body: some View {
        label()
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .frame(minWidth: 60, minHeight: 60)
            .background(Color(.systemGray5), in: RoundedRectangle(cornerRadius: 12))
            .onLongPressGesture(minimumDuration: .infinity, pressing: { pressing in
                if pressing {
                    action()
                    repeatTask = Task { @MainActor in
                        try? await Task.sleep(for: .milliseconds(400))
                        var count = 0
                        while !Task.isCancelled {
                            action()
                            count += 1
                            try? await Task.sleep(for: .milliseconds(count >= 5 ? 75 : 150))
                        }
                    }
                } else {
                    repeatTask?.cancel()
                    repeatTask = nil
                }
            }, perform: {})
            .onDisappear {
                repeatTask?.cancel()
                repeatTask = nil
            }
    }
}
