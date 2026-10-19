import AppKit
import SwiftUI
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { AppModel.shared.shutdown() }
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter,
        willPresent notification: UNNotification, withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound, .list])
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse, withCompletionHandler completionHandler: @escaping () -> Void) {
        if let string = response.notification.request.content.userInfo["url"] as? String,
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
        Settings {
            RootView().environmentObject(model).frame(idealWidth: 1060, idealHeight: 790)
        }
        MenuBarExtra {
            MenuContent().environmentObject(model)
        } label: {
            Image(systemName: model.config.enabled ? "paperplane.circle.fill" : "paperplane.circle")
                .accessibilityLabel("Telegram Focus")
                .help("Telegram Focus")
        }
    }
}

struct MenuContent: View {
    @EnvironmentObject var model: AppModel
    var body: some View {
        Text(model.connected ? "Telegram: \(model.accountName)" : model.statusText)
        Toggle("Фильтр включён", isOn: $model.config.enabled)
            .onChange(of: model.config.enabled) { model.saveConfig() }
        Text("Пропущено: \(model.passed) · Заглушено: \(model.suppressed)")
        Divider()
        SettingsLink { Text("Открыть настройки…") }
            .simultaneousGesture(TapGesture().onEnded { NSApp.activate(ignoringOtherApps: true) })
        Button("Тестовое уведомление") { model.requestNotifications(test: true) }
        Divider()
        Button("Завершить Telegram Focus") { NSApplication.shared.terminate(nil) }.keyboardShortcut("q")
    }
}
