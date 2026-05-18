import SwiftUI

struct RunningView: View {
    @Environment(TreadmillStore.self) var store
    @State private var isLandscape = false
    @State private var showHUD = false
    @State private var hudTimer: Task<Void, Never>?
    @State private var showDurationButtons = false

    private var program: ProgramState { store.program }
    private var session: SessionState { store.session }

    var body: some View {
        GeometryReader { geo in
            let landscape = geo.size.width > geo.size.height
            Group {
                if landscape {
                    landscapeLayout
                } else {
                    portraitLayout
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .onAppear { isLandscape = landscape }
            .onChange(of: landscape) { _, newValue in isLandscape = newValue }
        }
    }

    // MARK: - Landscape

    private var landscapeLayout: some View {
        Group {
            if program.completed || (!program.running && !program.paused && session.elapsed > 5) {
                completionView
            } else if !program.running && !program.paused && !session.active {
                Color.clear.onAppear { store.navigate(to: .lobby) }
            } else {
                activeLandscapeLayout
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var activeLandscapeLayout: some View {
        GeometryReader { geo in
            let h = geo.size.height
            let w = geo.size.width
            let timerSize = min(max(h * 0.14, 48), 100)
            let controlsWidth = min(max(w * 0.33, 280), 440)

            ZStack {
                // Background photo
                Image("bg_forest")
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipped()

                // Darkening overlay
                LinearGradient(
                    colors: [
                        Color.black.opacity(0.15),
                        Color.black.opacity(0.05),
                        Color.black.opacity(0.05),
                        Color.black.opacity(0.18),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                VStack(spacing: 0) {
                    // Fixed-height container for timer/encouragement — never resizes
                    ZStack {
                        // Invisible timer to hold the size constant
                        Text("00:00")
                            .font(.system(size: timerSize, weight: .bold))
                            .monospacedDigit()
                            .hidden()

                        if let msg = program.encouragement, !msg.isEmpty {
                            Text(msg.replacingOccurrences(of: "<<", with: "").replacingOccurrences(of: ">>", with: ""))
                                .font(.system(size: min(max(h * 0.05, 18), 36), weight: .semibold))
                                .foregroundStyle(AppColors.green)
                                .shadow(color: .black.opacity(0.5), radius: 6, y: 2)
                                .transition(.opacity)
                        } else {
                            Text(Fmt.time(session.elapsed))
                                .font(.system(size: timerSize, weight: .bold))
                                .monospacedDigit()
                                .contentTransition(.numericText())
                                .shadow(color: .black.opacity(0.5), radius: 6, y: 2)
                                .transition(.opacity)
                        }
                    }
                    .animation(.easeInOut(duration: 0.3), value: program.encouragement != nil)
                    .padding(.top, 8)
                    .onTapGesture {
                        if program.program?.manual == true && program.running {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                showDurationButtons.toggle()
                            }
                        }
                    }

                    // Duration adjustment (manual programs only, shown on timer tap)
                    if showDurationButtons && program.program?.manual == true && program.running {
                        durationAdjustButtons
                            .transition(.opacity.combined(with: .scale(scale: 0.9)))
                    }

                    MetricsRow(
                        speedMph: store.status.speedMph,
                        distance: session.distance,
                        vertFeet: session.vertFeet,
                        calories: session.calories,
                        heartRate: store.status.heartRate,
                        hrmConnected: store.status.hrmConnected,
                        scale: min(max(h / 380, 1.0), 1.8)
                    )
                    .padding(.vertical, 6)

                    GeometryReader { midGeo in
                        HStack(spacing: 8) {
                            elevationCard
                                .frame(height: midGeo.size.height)
                            SpeedInclineControls(vertical: true)
                                .frame(width: controlsWidth, height: midGeo.size.height)
                        }
                    }
                    .padding(.horizontal, 12)

                    stopBar
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    // MARK: - Portrait

    private var portraitLayout: some View {
        Group {
            if program.completed || (!program.running && !program.paused && session.elapsed > 5) {
                completionView
            } else if !program.running && !program.paused && !session.active {
                Color.clear.onAppear { store.navigate(to: .lobby) }
            } else {
                activePortraitLayout
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var activePortraitLayout: some View {
        GeometryReader { geo in
            let h = geo.size.height
            let timerSize = min(max(h * 0.10, 48), 80)

            ZStack {
                // Background photo
                Image("bg_forest")
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipped()

                // Darkening overlay
                LinearGradient(
                    colors: [
                        Color.black.opacity(0.15),
                        Color.black.opacity(0.05),
                        Color.black.opacity(0.05),
                        Color.black.opacity(0.18),
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )

                VStack(spacing: 0) {
                    // Fixed-height container for timer/encouragement
                    ZStack {
                        Text("00:00")
                            .font(.system(size: timerSize, weight: .bold))
                            .monospacedDigit()
                            .hidden()

                        if let msg = program.encouragement, !msg.isEmpty {
                            Text(msg.replacingOccurrences(of: "<<", with: "").replacingOccurrences(of: ">>", with: ""))
                                .font(.system(size: min(max(h * 0.04, 16), 28), weight: .semibold))
                                .foregroundStyle(AppColors.green)
                                .shadow(color: .black.opacity(0.5), radius: 6, y: 2)
                                .transition(.opacity)
                        } else {
                            Text(Fmt.time(session.elapsed))
                                .font(.system(size: timerSize, weight: .bold))
                                .monospacedDigit()
                                .contentTransition(.numericText())
                                .shadow(color: .black.opacity(0.5), radius: 6, y: 2)
                                .transition(.opacity)
                        }
                    }
                    .animation(.easeInOut(duration: 0.3), value: program.encouragement != nil)
                    .padding(.top, 8)
                    .onTapGesture {
                        if program.program?.manual == true && program.running {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                showDurationButtons.toggle()
                            }
                        }
                    }

                    // Duration adjustment (manual programs only, shown on timer tap)
                    if showDurationButtons && program.program?.manual == true && program.running {
                        durationAdjustButtons
                            .transition(.opacity.combined(with: .scale(scale: 0.9)))
                    }

                    MetricsRow(
                        speedMph: store.status.speedMph,
                        distance: session.distance,
                        vertFeet: session.vertFeet,
                        calories: session.calories,
                        heartRate: store.status.heartRate,
                        hrmConnected: store.status.hrmConnected,
                        scale: min(max(h / 500, 1.0), 1.6)
                    )
                    .padding(.vertical, 8)

                    elevationCard
                        .padding(.horizontal, 12)

                    SpeedInclineControls(vertical: false)
                        .padding(.horizontal, 12)
                        .padding(.top, 8)

                    Spacer(minLength: 0)

                    stopBar
                        .padding(.horizontal, 12)
                        .padding(.bottom, 12)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    // MARK: - Completion view

    private var completionView: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 48))
                .foregroundStyle(.green)
            Text("Workout Complete")
                .font(.title2.weight(.bold))

            HStack(spacing: 24) {
                VStack {
                    Text(Fmt.time(session.elapsed))
                        .font(.system(size: 20, weight: .bold))
                        .monospacedDigit()
                    Text("time")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                VStack {
                    Text(String(format: "%.2f", session.distance))
                        .font(.system(size: 20, weight: .bold))
                        .monospacedDigit()
                    Text("miles")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                VStack {
                    Text(String(format: "%.0f", session.vertFeet))
                        .font(.system(size: 20, weight: .bold))
                        .monospacedDigit()
                    Text("vert ft")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                VStack {
                    Text(String(format: "%.0f", session.calories))
                        .font(.system(size: 20, weight: .bold))
                        .monospacedDigit()
                    Text("cal")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }

            Button("New Workout") {
                Task {
                    await store.resetSession()
                    store.navigate(to: .lobby)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(.green)
            Spacer()
        }
    }

    // MARK: - Shared elements

    private var elevationCard: some View {
        ZStack {
            if let prog = program.program, !prog.intervals.isEmpty {
                ElevationProfile(
                    intervals: prog.intervals,
                    currentInterval: program.currentInterval,
                    intervalElapsed: program.intervalElapsed,
                    totalDuration: program.totalDuration
                )
            } else {
                RoundedRectangle(cornerRadius: 12)
                    .fill(AppColors.glassBackground)
            }

            // HUD overlay
            if showHUD && program.running {
                hudOverlay
                    .transition(.opacity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .contentShape(Rectangle())
        .overlay {
            GeometryReader { geo in
                Color.clear
                    .contentShape(Rectangle())
                    .simultaneousGesture(
                        SpatialTapGesture(count: 2)
                            .onEnded { value in
                                guard program.running else { return }
                                let mid = geo.size.width / 2
                                if value.location.x > mid {
                                    Task { await store.skip() }
                                } else {
                                    Task { await store.prev() }
                                }
                            }
                    )
                    .onTapGesture(count: 1) {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            showHUD.toggle()
                        }
                        if showHUD {
                            scheduleHUDHide()
                        }
                    }
            }
        }
        .onDisappear {
            hudTimer?.cancel()
            hudTimer = nil
        }
    }

    private var hudOverlay: some View {
        HStack(spacing: 32) {
            // Previous interval
            Button {
                // Skip backward not supported by API
            } label: {
                Image(systemName: "backward.fill")
                    .font(.title2)
                    .foregroundStyle(.white)
            }
            .disabled(program.currentInterval == 0)
            .opacity(program.currentInterval == 0 ? 0.3 : 1)

            // Play/Pause
            Button {
                Task { await store.pause() }
                scheduleHUDHide()
            } label: {
                Image(systemName: program.paused ? "play.fill" : "pause.fill")
                    .font(.system(size: 36))
                    .foregroundStyle(.white)
            }

            // Next interval / Skip
            Button {
                Task { await store.skip() }
                scheduleHUDHide()
            } label: {
                Image(systemName: "forward.fill")
                    .font(.title2)
                    .foregroundStyle(.white)
            }
        }
        .padding(20)
        .glassPanel(cornerRadius: 16)
    }

    private func scheduleHUDHide() {
        hudTimer?.cancel()
        hudTimer = Task {
            try? await Task.sleep(for: .seconds(4))
            if !Task.isCancelled && !program.paused {
                withAnimation { showHUD = false }
            }
        }
    }

    private var durationAdjustButtons: some View {
        HStack(spacing: 8) {
            Button("-10") { Task { await store.adjustDuration(-600) } }
                .buttonStyle(.bordered).tint(.secondary)
            Button("-5") { Task { await store.adjustDuration(-300) } }
                .buttonStyle(.bordered).tint(.secondary)
            Button("+5") { Task { await store.adjustDuration(300) } }
                .buttonStyle(.bordered).tint(.secondary)
            Button("+10") { Task { await store.adjustDuration(600) } }
                .buttonStyle(.bordered).tint(.secondary)
        }
        .font(.caption.weight(.semibold))
    }

    @ViewBuilder
    private var stopBar: some View {
        if program.paused {
            HStack(spacing: 12) {
                Button("Resume") {
                    Task { await store.pause() }
                }
                .buttonStyle(StopBarStyle(color: AppColors.green))

                Button("Reset") {
                    Task {
                        await store.resetSession()
                        store.navigate(to: .lobby)
                    }
                }
                .buttonStyle(StopBarStyle(color: Color(.systemGray3)))
            }
        } else {
            Button("Stop") {
                Task { await store.pause() }
            }
            .buttonStyle(StopBarStyle(color: AppColors.red))
        }
    }

}

struct StopBarStyle: ButtonStyle {
    let color: Color

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.title3.weight(.semibold))
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 28)
            .background(color.opacity(configuration.isPressed ? 0.45 : 0.38), in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(color.opacity(0.45), lineWidth: 1))
    }
}
