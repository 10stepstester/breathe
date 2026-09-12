import AppKit

/// Menu-bar only (LSUIElement). No dock icon, no windows in step one.
@main
final class AppDelegate: NSObject, NSApplicationDelegate {
    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.accessory)
        app.run()
    }

    private var scheduler: Scheduler!
    private var statusMenu: StatusMenu!

    func applicationDidFinishLaunching(_ notification: Notification) {
        Notifier.shared.requestPermission()
        scheduler = Scheduler()
        statusMenu = StatusMenu(scheduler: scheduler)
        scheduler.start()
    }

    func applicationWillTerminate(_ notification: Notification) {
        scheduler.stop()
    }
}
