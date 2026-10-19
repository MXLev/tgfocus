import AppIntents
import AppKit
import Security

// This is the app's configured Focus Filter, not the app-relative Focus Status API.
// With no active filter macOS restores parameter defaults, so the default MUST be false.
struct TGFocusFilter: SetFocusFilterIntent {
    static var title: LocalizedStringResource = "Важные сообщения Telegram"
    static var description = IntentDescription("Включать доставку TGFocus, пока действует этот режим фокусирования.")

    @Parameter(title: "Включать фильтр TGFocus", default: false)
    var useFilter: Bool

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "Важные сообщения Telegram",
                              subtitle: useFilter ? "Доставка включена" : "Доставка выключена")
    }

    static func suggestedFocusFilters(for context: FocusFilterSuggestionContext) async -> [TGFocusFilter] {
        var filter = TGFocusFilter()
        filter.useFilter = true
        return [filter]
    }

    func perform() async throws -> some IntentResult {
        // The running app queries .current; callbacks do not persist an on/off flag,
        // which could survive a crash, missed deactivation, or reboot.
        .result()
    }
}

enum FocusFilterState: Equatable {
    case active, inactive, unavailable, unsupportedSigning
}

enum FocusAutomation {
    static let unsupportedMessage = "Авто-режим недоступен в этой сборке: macOS не регистрирует App Intents без Team ID в подписи. Выбери ручной режим; добавлять системный фильтр не нужно."

    // A signing team is a prerequisite, not proof that Focus registration succeeded.
    // Never treat an unregistered ad-hoc app's .notFound as a working integration.
    static let hasSigningTeam: Bool = {
        var code: SecCode?
        guard SecCodeCopySelf(SecCSFlags(rawValue: 0), &code) == errSecSuccess, let code else { return false }
        var information: CFDictionary?
        guard SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation),
                                            &information) == errSecSuccess,
              let values = information as? [String: Any],
              let team = values[kSecCodeInfoTeamIdentifier as String] as? String else { return false }
        return !team.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }()

    static func read() async -> FocusFilterState {
        guard hasSigningTeam else { return .unsupportedSigning }
        do {
            return try await TGFocusFilter.current.useFilter ? .active : .inactive
        } catch SetFocusFilterIntentError.notFound {
            return .inactive
        } catch {
            // An error is not evidence that Focus is enabled.
            return .unavailable
        }
    }
}

enum FilterPresentation {
    static func title(enabled: Bool) -> String {
        enabled ? "Фильтр включён" : "Фильтр выключен"
    }
    static func icon(enabled: Bool) -> NSImage {
        let image = NSImage(systemSymbolName: enabled ? "paperplane.circle.fill" : "paperplane.circle",
                            accessibilityDescription: title(enabled: enabled))!
        image.isTemplate = true
        return image
    }
    static func allowsDelivery(enabled: Bool, automatic: Bool, focus: FocusFilterState) -> Bool {
        enabled && (!automatic || focus == .active)
    }
}
