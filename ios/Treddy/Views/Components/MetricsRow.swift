import SwiftUI

struct MetricsRow: View {
    let speedMph: Double
    let distance: Double
    let vertFeet: Double
    let calories: Double
    var heartRate: Int = 0
    var hrmConnected: Bool = false
    var scale: CGFloat = 1.0

    var body: some View {
        HStack(spacing: 24 * scale) {
            if hrmConnected && heartRate > 0 {
                metric(value: "\(heartRate)", unit: "bpm", color: hrColor(heartRate))
                divider
            }
            metric(value: Fmt.pace(speedMph), unit: "min/mi", color: speedMph > 0 ? .green : .primary)
            divider
            metric(value: String(format: "%.2f", distance), unit: "miles")
            divider
            metric(value: String(format: "%.0f", vertFeet), unit: "vert ft")
            divider
            metric(value: String(format: "%.0f", calories), unit: "cal")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .glassPanel(cornerRadius: 10)
    }

    private func metric(value: String, unit: String, color: Color = .primary) -> some View {
        HStack(spacing: 4) {
            Text(value)
                .font(.system(size: 20 * scale, weight: .bold).monospacedDigit())
                .foregroundStyle(color)
                .shadow(color: .black.opacity(0.5), radius: 4, y: 1)
            Text(unit)
                .font(.system(size: 11 * scale))
                .foregroundStyle(.secondary)
                .shadow(color: .black.opacity(0.3), radius: 2, y: 1)
        }
    }

    private var divider: some View {
        Rectangle()
            .fill(.primary.opacity(0.1))
            .frame(width: 1, height: 28 * scale)
    }

    private func hrColor(_ bpm: Int) -> Color {
        switch bpm {
        case ..<100: return .green
        case 100..<130: return .yellow
        case 130..<160: return .orange
        default: return .red
        }
    }

}
