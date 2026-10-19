import AppKit
import SwiftUI
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        MainActor.assumeIsolated {
            if AppModel.shared.startupAllowed { AppModel.shared.updateFocusAutomation() }
        }
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
        MenuBarExtra {
            MenuContent().environmentObject(model)
        } label: {
            Image(systemName: model.effectiveFilterEnabled ? "paperplane.circle.fill" : "paperplane.circle")
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
        if model.config.onlyDuringFocus {
            Text(model.effectiveFilterEnabled ? "Авто-режим: неотложные уведомления" : "Авто-режим: пауза — подробнее в настройках")
        } else {
            Text("Ручной режим: возможны дубли с Telegram")
        }
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
