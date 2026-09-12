import Foundation

/// User-tunable values, persisted in UserDefaults. Defaults match the Python app.
enum Settings {
    private static let d = UserDefaults.standard

    static let intervalChoices = [5, 10, 15, 20, 30]
    static let windowChoices = [30, 45, 60]

    static var intervalMinutes: Int {
        get { d.object(forKey: "intervalMinutes") as? Int ?? 10 }
        set { d.set(newValue, forKey: "intervalMinutes") }
    }

    static var windowSeconds: Int {
        get { d.object(forKey: "windowSeconds") as? Int ?? 45 }
        set { d.set(newValue, forKey: "windowSeconds") }
    }

    static var paused: Bool {
        get { d.bool(forKey: "paused") }
        set { d.set(newValue, forKey: "paused") }
    }
}

/// Daily caught / pinged tally, keyed by local calendar day.
enum Stats {
    private static let d = UserDefaults.standard

    private static var dayKey: String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date())
    }

    static var caught: Int { d.integer(forKey: "stats.\(dayKey).caught") }
    static var pinged: Int { d.integer(forKey: "stats.\(dayKey).pinged") }

    static func recordCaught() { d.set(caught + 1, forKey: "stats.\(dayKey).caught") }
    static func recordPinged() { d.set(pinged + 1, forKey: "stats.\(dayKey).pinged") }
}
