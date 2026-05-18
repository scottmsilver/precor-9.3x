import SwiftUI

struct ElevationProfile: View {
    let intervals: [Interval]
    let currentInterval: Int
    let intervalElapsed: Int
    let totalDuration: Int

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Canvas { context, size in
                drawChart(context: context, size: size)
            }

            if !intervals.isEmpty {
                Text("\(currentInterval + 1) of \(intervals.count)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .padding(8)
            }
        }
        .glassPanel()
    }

    private func drawChart(context: GraphicsContext, size: CGSize) {
        guard !intervals.isEmpty else { return }

        let margin = EdgeInsets(top: 12, leading: 32, bottom: 24, trailing: 12)
        let chartW = size.width - margin.leading - margin.trailing
        let chartH = size.height - margin.top - margin.bottom

        let rawMax = intervals.map(\.incline).max() ?? 0
        let maxIncline = max(rawMax, 2) // Minimum 2% scale so flat workouts don't look weird
        let totalDur = Double(totalDuration > 0 ? totalDuration : intervals.reduce(0) { $0 + Int($1.duration) })

        var elapsedToCurrentStart: Double = 0
        for i in 0..<min(currentInterval, intervals.count) {
            elapsedToCurrentStart += intervals[i].duration
        }
        let currentElapsed = elapsedToCurrentStart + Double(intervalElapsed)
        let progressFraction = totalDur > 0 ? currentElapsed / totalDur : 0

        // Grid lines
        let gridColor = Color.primary.opacity(0.24)
        for i in 0...4 {
            let y = margin.top + chartH * CGFloat(i) / 4.0
            var path = Path()
            path.move(to: CGPoint(x: margin.leading, y: y))
            path.addLine(to: CGPoint(x: size.width - margin.trailing, y: y))
            context.stroke(path, with: .color(gridColor), style: StrokeStyle(lineWidth: 0.5, dash: [4, 4]))
        }

        // Y-axis labels — only show distinct values
        var lastLabel = ""
        for i in 0...4 {
            let pct = maxIncline * Double(4 - i) / 4.0
            let y = margin.top + chartH * CGFloat(i) / 4.0
            let label = String(format: "%.0f%%", pct)
            if label != lastLabel {
                let text = Text(label)
                    .font(.system(size: 8))
                    .foregroundStyle(.secondary)
                context.draw(context.resolve(text), at: CGPoint(x: margin.leading - 4, y: y), anchor: .trailing)
                lastLabel = label
            }
        }

        // Build staircase path
        var staircasePath = Path()
        var fillPath = Path()
        var x: CGFloat = margin.leading
        let baseline = margin.top + chartH

        fillPath.move(to: CGPoint(x: margin.leading, y: baseline))

        for (i, interval) in intervals.enumerated() {
            let w = chartW * CGFloat(interval.duration / totalDur)
            let y = margin.top + chartH * (1.0 - CGFloat(interval.incline / maxIncline))

            if i == 0 {
                staircasePath.move(to: CGPoint(x: x, y: y))
            } else {
                staircasePath.addLine(to: CGPoint(x: x, y: y))
            }
            staircasePath.addLine(to: CGPoint(x: x + w, y: y))

            fillPath.addLine(to: CGPoint(x: x, y: y))
            fillPath.addLine(to: CGPoint(x: x + w, y: y))

            x += w
        }

        fillPath.addLine(to: CGPoint(x: x, y: baseline))
        fillPath.closeSubpath()

        // Completed fill
        let progressX = margin.leading + chartW * CGFloat(progressFraction)
        let clipRect = CGRect(x: 0, y: 0, width: progressX, height: size.height)
        var completedContext = context
        completedContext.clip(to: Path(clipRect))
        completedContext.fill(fillPath, with: .color(.green.opacity(0.40)))

        // Full outline (future = dimmer)
        context.stroke(staircasePath, with: .color(.green.opacity(0.7)), lineWidth: 2)

        // Completed outline (brighter)
        var completedStrokeCtx = context
        completedStrokeCtx.clip(to: Path(clipRect))
        completedStrokeCtx.stroke(staircasePath, with: .color(.green), lineWidth: 3)

        // Progress dot
        if intervals.indices.contains(currentInterval) {
            let interval = intervals[currentInterval]
            let fracInInterval = interval.duration > 0 ? Double(intervalElapsed) / interval.duration : 0
            var dotX: CGFloat = margin.leading
            for i in 0..<currentInterval { dotX += chartW * CGFloat(intervals[i].duration / totalDur) }
            dotX += chartW * CGFloat(interval.duration / totalDur) * CGFloat(fracInInterval)
            let dotY = margin.top + chartH * (1.0 - CGFloat(interval.incline / maxIncline))

            let glowRect = CGRect(x: dotX - 10, y: dotY - 10, width: 20, height: 20)
            context.fill(Path(ellipseIn: glowRect), with: .color(.green.opacity(0.4)))
            let dotRect = CGRect(x: dotX - 5, y: dotY - 5, width: 10, height: 10)
            context.fill(Path(ellipseIn: dotRect), with: .color(.green))
        }

        // Interval boundary dots
        var bx: CGFloat = margin.leading
        for i in 0..<intervals.count {
            let dotSize: CGFloat = 6
            let by = baseline + 8
            if i > 0 {
                let rect = CGRect(x: bx - dotSize / 2, y: by - dotSize / 2, width: dotSize, height: dotSize)
                context.stroke(Path(ellipseIn: rect), with: .color(.primary.opacity(0.4)), lineWidth: 1)
            }
            bx += chartW * CGFloat(intervals[i].duration / totalDur)
        }

        // X-axis time labels
        let timeSteps = niceTimeSteps(totalDuration: totalDur)
        for t in timeSteps {
            let tx = margin.leading + chartW * CGFloat(t / totalDur)
            let label = formatTime(Int(t))
            let text = Text(label).font(.system(size: 8)).foregroundStyle(.secondary)
            context.draw(context.resolve(text), at: CGPoint(x: tx, y: baseline + 12), anchor: .top)
        }
    }

    private func niceTimeSteps(totalDuration: Double) -> [Double] {
        let stepSec: Double
        if totalDuration <= 300 { stepSec = 60 }
        else if totalDuration <= 600 { stepSec = 120 }
        else if totalDuration <= 1800 { stepSec = 300 }
        else { stepSec = 600 }
        var steps: [Double] = []
        var t = stepSec
        while t < totalDuration {
            steps.append(t)
            t += stepSec
        }
        return steps
    }

    private func formatTime(_ seconds: Int) -> String {
        let m = seconds / 60
        return "\(m)"
    }
}
