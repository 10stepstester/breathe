import Foundation

/// Drives the cycle: wait N minutes -> camera on for W seconds -> ping or stay quiet.
/// Step one has no detection yet, so every window ends as a miss unless the
/// user hits "I did it" from the menu. Step two replaces that with pose scoring.
@MainActor
final class Scheduler {
    enum State: Equatable {
        case waiting(until: Date)
        case checking(until: Date)
        case paused
        case noCamera
    }

    private(set) var state: State = .paused {
        didSet { onChange?() }
    }

    /// Fired once a second and on every state change so the menu can redraw.
    var onChange: (() -> Void)?

    private let camera = Camera()
    private var tick: Timer?

    func start() {
        tick = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tickFired() }
        }
        RunLoop.main.add(tick!, forMode: .common)
        state = Settings.paused ? .paused : .waiting(until: nextCheckDate())
    }

    func stop() {
        tick?.invalidate()
        camera.stop()
    }

    // MARK: - Menu actions

    func checkNow() {
        if case .checking = state { return }
        beginCheck()
    }

    func togglePause() {
        if case .paused = state {
            Settings.paused = false
            state = .waiting(until: nextCheckDate())
        } else {
            Settings.paused = true
            if case .checking = state { camera.stop() }
            state = .paused
        }
    }

    /// Placeholder until pose detection lands: the user tells us they did it.
    func markCaught() {
        guard case .checking = state else { return }
        camera.stop()
        Stats.recordCaught()
        state = .waiting(until: nextCheckDate())
    }

    func intervalChanged() {
        if case .waiting = state { state = .waiting(until: nextCheckDate()) }
    }

    // MARK: - Cycle

    private func tickFired() {
        switch state {
        case .waiting(let until) where Date() >= until:
            beginCheck()
        case .checking(let until) where Date() >= until:
            endCheck(caught: false)
        default:
            onChange?()
        }
    }

    private func beginCheck() {
        if Camera.isBusyElsewhere() {
            // Zoom / FaceTime has the camera. Skip silently, no stats.
            state = .waiting(until: nextCheckDate())
            return
        }
        camera.start { [weak self] ok in
            guard let self else { return }
            guard ok else {
                self.state = .noCamera
                Notifier.shared.send(title: "Breathe needs the camera",
                                     body: "Allow camera access in System Settings > Privacy & Security > Camera, then click Resume.")
                return
            }
            self.state = .checking(until: Date().addingTimeInterval(TimeInterval(Settings.windowSeconds)))
        }
    }

    private func endCheck(caught: Bool) {
        camera.stop()
        if caught {
            Stats.recordCaught()
        } else {
            Stats.recordPinged()
            Notifier.shared.send(title: "Sit tall. Take a breath.",
                                 body: "Missed that one. Next check in \(Settings.intervalMinutes) minutes.")
        }
        state = .waiting(until: nextCheckDate())
    }

    private func nextCheckDate() -> Date {
        Date().addingTimeInterval(TimeInterval(Settings.intervalMinutes * 60))
    }
}
