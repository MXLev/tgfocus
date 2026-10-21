import AppKit
import SwiftUI
import UserNotifications
import Darwin

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        MainActor.assumeIsolated {
            if AppModel.isStartupSmokeTest {
                Task { @MainActor in await self.runStartupSmokeTest() }
            }
        }
    }

    @MainActor private func runStartupSmokeTest() async {
        let model = AppModel.shared
        let deadline = Date().addingTimeInterval(15)
        while !model.helperReady && Date() < deadline {
            try? await Task.sleep(for: .milliseconds(100))
        }
        guard model.helperReady else {
            fputs("App startup smoke test: worker did not become ready\n", stderr)
            model.shutdown()
            exit(1)
        }
        let settings = SettingsWindowController.shared
        settings.show { NSHostingController(rootView: RootView().environmentObject(model)) }
        guard settings.window?.isVisible == true else {
            fputs("App startup smoke test: settings window did not open\n", stderr)
            model.shutdown()
            exit(1)
        }
        settings.window?.close()
        settings.show { NSHostingController(rootView: RootView().environmentObject(model)) }
        guard settings.window?.isVisible == true else {
            fputs("App startup smoke test: settings window did not reopen\n", stderr)
            model.shutdown()
            exit(1)
        }
        if ProcessInfo.processInfo.arguments.contains("--measure-idle") {
            model.setFilterEnabled(false)
            settings.window?.close()
            print("Idle resource measurement ready")
            fflush(stdout)
            try? await Task.sleep(for: .seconds(25))
        }
        print("Signed app startup: worker ready, settings opened and reopened OK")
        fflush(stdout)
        NSApp.terminate(nil)
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { AppModel.shared.shutdown() }
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter,
        willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        var options: UNNotificationPresentationOptions = [.banner, .list]
        if notification.request.content.sound != nil { options.insert(.sound) }
        completionHandler(options)
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.notification.request.content.userInfo["settingsPage"] as? String == "ai" {
            DispatchQueue.main.async {
                let model = AppModel.shared
                model.selectedPage = "ai"
                SettingsWindowController.shared.show {
                    NSHostingController(rootView: RootView().environmentObject(model))
                }
            }
        } else if let string = response.notification.request.content.userInfo["url"] as? String,
           let url = URL(string: string), url.scheme == "tg" {
            DispatchQueue.main.async { NSWorkspace.shared.open(url) }
        }
        completionHandler()
    }
}

@main
struct TelegramFocusApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    @StateObject private var model = AppModel.shared
    var body: some Scene {
        MenuBarExtra {
            MenuContent().environmentObject(model)
        } label: {
            Image(nsImage: FilterPresentation.icon(enabled: model.effectiveFilterEnabled))
                .id(model.effectiveFilterEnabled)
                .accessibilityLabel("Telegram Focus")
                .help("Telegram Focus")
        }
    }
}

struct MenuContent: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        Text(model.connected ? "Telegram: \(model.accountName)" : model.statusText)
        Text(model.filterTitle)
        Button(model.config.enabled ? "Выключить фильтр" : "Включить фильтр") { model.setFilterEnabled(!model.config.enabled) }
        Text(model.automationStatus)
        Text("Пропущено: \(model.passed) · Заглушено: \(model.suppressed)")
        Divider()
        Button("Открыть настройки…") {
            // Open after the menu closes; retain and reuse the window after closing it.
            DispatchQueue.main.async {
                SettingsWindowController.shared.show {
                    NSHostingController(rootView: RootView().environmentObject(model))
                }
            }
        }.keyboardShortcut(",")
        Button("Тестовое уведомление") { model.requestNotifications(test: true) }
        Divider()
        Button("Завершить Telegram Focus") { NSApplication.shared.terminate(nil) }.keyboardShortcut("q")
    }
}
