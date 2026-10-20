import AppKit

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
}
