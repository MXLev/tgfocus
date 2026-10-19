import AppKit

@MainActor
final class SettingsWindowController {
    static let shared = SettingsWindowController()
    private(set) var window: NSWindow?

    func show(makeContent: () -> NSViewController) {
        if window == nil {
            let created = NSWindow(contentViewController: makeContent())
            created.title = "Telegram Focus — настройки"
            created.styleMask = [.titled, .closable, .miniaturizable, .resizable]
            created.setContentSize(NSSize(width: 1060, height: 790))
            created.minSize = NSSize(width: 960, height: 680)
            created.isReleasedWhenClosed = false
            created.center()
            window = created
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.deminiaturize(nil)
        window?.makeKeyAndOrderFront(nil)
    }
}
