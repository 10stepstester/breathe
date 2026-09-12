import AppKit

/// The lungs icon in the menu bar and everything under it.
@MainActor
final class StatusMenu: NSObject, NSMenuDelegate {
    private let item: NSStatusItem
    private let menu = NSMenu()
    private let scheduler: Scheduler

    private let statusLine = NSMenuItem()
    private let scoreLine = NSMenuItem()
    private let didItItem = NSMenuItem(title: "I sat tall and took a breath", action: #selector(didIt), keyEquivalent: "")
    private let pauseItem = NSMenuItem(title: "Pause", action: #selector(togglePause), keyEquivalent: "")
    private let intervalMenu = NSMenu()
    private let windowMenu = NSMenu()

    init(scheduler: Scheduler) {
        self.scheduler = scheduler
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        super.init()
        build()
        item.menu = menu
        scheduler.onChange = { [weak self] in self?.refresh() }
        refresh()
    }

    private func build() {
        statusLine.isEnabled = false
        scoreLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(scoreLine)
        menu.addItem(.separator())

        let checkNow = NSMenuItem(title: "Check now", action: #selector(checkNow), keyEquivalent: "")
        checkNow.target = self
        menu.addItem(checkNow)
        didItItem.target = self
        menu.addItem(didItItem)
        pauseItem.target = self
        menu.addItem(pauseItem)
        menu.addItem(.separator())

        let every = NSMenuItem(title: "Check every", action: nil, keyEquivalent: "")
        for minutes in Settings.intervalChoices {
            let m = NSMenuItem(title: "\(minutes) minutes", action: #selector(setInterval(_:)), keyEquivalent: "")
            m.target = self
            m.tag = minutes
            intervalMenu.addItem(m)
        }
        every.submenu = intervalMenu
        menu.addItem(every)

        let window = NSMenuItem(title: "Camera stays on for", action: nil, keyEquivalent: "")
        for seconds in Settings.windowChoices {
            let m = NSMenuItem(title: "\(seconds) seconds", action: #selector(setWindow(_:)), keyEquivalent: "")
            m.target = self
            m.tag = seconds
            windowMenu.addItem(m)
        }
        window.submenu = windowMenu
        menu.addItem(window)
        menu.addItem(.separator())

        let quit = NSMenuItem(title: "Quit Breathe", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        menu.addItem(quit)
    }

    private func refresh() {
        let now = Date()
        var symbol = "lungs"
        switch scheduler.state {
        case .waiting(let until):
            statusLine.title = "Next check in \(clock(until.timeIntervalSince(now)))"
            pauseItem.title = "Pause"
            didItItem.isHidden = true
        case .checking(let until):
            statusLine.title = "Camera on — sit tall, breathe (\(Int(max(0, until.timeIntervalSince(now))))s)"
            pauseItem.title = "Pause"
            didItItem.isHidden = false
            symbol = "lungs.fill"
        case .paused:
            statusLine.title = "Paused"
            pauseItem.title = "Resume"
            didItItem.isHidden = true
            symbol = "lungs"
        case .noCamera:
            statusLine.title = "Camera access needed"
            pauseItem.title = "Resume"
            didItItem.isHidden = true
            symbol = "lungs"
        }
        scoreLine.title = "Today: \(Stats.caught) caught · \(Stats.pinged) pinged"

        if let image = NSImage(systemSymbolName: symbol, accessibilityDescription: "Breathe") {
            image.isTemplate = true
            item.button?.image = image
        }
        item.button?.appearsDisabled = (scheduler.state == .paused)

        for m in intervalMenu.items { m.state = m.tag == Settings.intervalMinutes ? .on : .off }
        for m in windowMenu.items { m.state = m.tag == Settings.windowSeconds ? .on : .off }
    }

    private func clock(_ seconds: TimeInterval) -> String {
        let s = Int(max(0, seconds))
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    // MARK: - Actions

    @objc private func checkNow() { scheduler.checkNow() }
    @objc private func didIt() { scheduler.markCaught() }
    @objc private func togglePause() { scheduler.togglePause() }

    @objc private func setInterval(_ sender: NSMenuItem) {
        Settings.intervalMinutes = sender.tag
        scheduler.intervalChanged()
    }

    @objc private func setWindow(_ sender: NSMenuItem) {
        Settings.windowSeconds = sender.tag
        refresh()
    }
}
