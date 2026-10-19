import Foundation
import CoreImage
import Darwin
import AppKit

@main
struct NativeTests {
    @MainActor static func main() throws {
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.accessory)
        let settings = SettingsWindowController()
        var creations = 0
        func content() -> NSViewController {
            creations += 1
            let controller = NSViewController()
            controller.view = NSView(frame: NSRect(x: 0, y: 0, width: 1060, height: 790))
            return controller
        }
        settings.show(makeContent: content)
        let originalWindow = settings.window!
        precondition(originalWindow.isVisible)
        originalWindow.close()
        precondition(!originalWindow.isVisible)
        settings.show(makeContent: content)
        precondition(settings.window === originalWindow && originalWindow.isVisible && creations == 1)
        originalWindow.close()
        for enabled in [true, false] {
            for automatic in [true, false] {
                let focusStates: [Bool?] = [true, false, nil]
                for focused in focusStates {
                    precondition(AppModel.shouldNotify(enabled: enabled, onlyDuringFocus: automatic, focused: focused)
                        == (enabled && (!automatic || focused == true)))
                }
            }
        }
        let lockDirectory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: lockDirectory) }
        do {
            let first = try RuntimeLock(directory: lockDirectory)
            try withExtendedLifetime(first) {
                do {
                    _ = try RuntimeLock(directory: lockDirectory)
                    fatalError("Second app instance acquired the same lock")
                } catch let error as POSIXError {
                    precondition(error.code.rawValue == EWOULDBLOCK)
                }
            }
        }
        _ = try RuntimeLock(directory: lockDirectory) // Lock is released when the first copy exits.
        let decoder = LineDecoder()
        let text = "{\"type\":\"message\",\"text\":\"Срочно 📨\"}\n{\"type\":\"ready\"}\n"
        var messages: [[String: Any]] = []
        for byte in Data(text.utf8) { messages.append(contentsOf: decoder.consume(Data([byte]))) }
        precondition(messages.count == 2)
        precondition(messages[0]["text"] as? String == "Срочно 📨")
        precondition(messages[1]["type"] as? String == "ready")
        precondition(decoder.consume(Data("broken\n".utf8)).isEmpty)
        precondition(decoder.consume(Data("{\"type\":\"ok\"}\n".utf8)).count == 1)
        var config = FilterConfig()
        config.people = [Person(id: "42", name: "Важный")]
        config.chats = [ChatRule(id: "-100123", name: "Учёба")]
        let data = try JSONEncoder().encode(config)
        let decoded = try JSONDecoder().decode(FilterConfig.self, from: data)
        precondition(decoded.people == config.people)
        precondition(decoded.chats == config.chats)
        precondition(decoded.enabled && !decoded.allPrivate && decoded.chats[0].keywordsOnly)
        let raw = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        precondition(raw["people"] is [[String: Any]])
        precondition(raw["apiHash"] == nil && raw["session"] == nil)
        var legacy = raw
        legacy.removeValue(forKey: "allowMentions")
        legacy.removeValue(forKey: "allowReplies")
        legacy.removeValue(forKey: "onlyDuringFocus")
        let migrated = try JSONDecoder().decode(FilterConfig.self, from: JSONSerialization.data(withJSONObject: legacy))
        precondition(migrated.allowMentions)
        precondition(migrated.allowReplies)
        precondition(migrated.onlyDuringFocus)
        precondition(migrated.people == config.people && migrated.chats == config.chats)
        precondition(migrated.keywords == config.keywords)
        config.allowMentions = false
        let disabledMentions = try JSONDecoder().decode(FilterConfig.self, from: JSONEncoder().encode(config))
        precondition(!disabledMentions.allowMentions)
        var oldDisabled = raw
        oldDisabled["allowMentions"] = false
        oldDisabled.removeValue(forKey: "allowReplies")
        oldDisabled.removeValue(forKey: "onlyDuringFocus")
        let migratedDisabled = try JSONDecoder().decode(FilterConfig.self, from: JSONSerialization.data(withJSONObject: oldDisabled))
        precondition(!migratedDisabled.allowMentions && !migratedDisabled.allowReplies && migratedDisabled.onlyDuringFocus)
        config.allowReplies = false
        config.onlyDuringFocus = true
        let savedOptions = try JSONDecoder().decode(FilterConfig.self, from: JSONEncoder().encode(config))
        precondition(!savedOptions.allowMentions && !savedOptions.allowReplies && savedOptions.onlyDuringFocus)
        config.onlyDuringFocus = false
        let savedManual = try JSONDecoder().decode(FilterConfig.self, from: JSONEncoder().encode(config))
        precondition(!savedManual.onlyDuringFocus)
        let fakeURL = "tg://login?token=TEST_NOT_A_REAL_TOKEN"
        guard let qr = QRRenderer.image(for: fakeURL) else { fatalError("QR image generation failed") }
        guard let tiff = qr.tiffRepresentation, let image = CIImage(data: tiff) else { fatalError("QR image serialization failed") }
        guard let detector = CIDetector(ofType: CIDetectorTypeQRCode, context: CIContext(options: [.useSoftwareRenderer: true]),
                  options: [CIDetectorAccuracy: CIDetectorAccuracyHigh]) else { fatalError("QR detector creation failed") }
        guard let result = detector.features(in: image).first as? CIQRCodeFeature else { fatalError("QR image decoding failed") }
        precondition(result.messageString == fakeURL)
        precondition(QRRenderer.image(for: "https://example.org") == nil)
        for suffix in ["", "token&extra=1", "token fragment", "token#fragment", "token%20"] {
            precondition(QRRenderer.image(for: "tg://login?token=" + suffix) == nil)
        }
        print("Native startup lock: duplicate instance rejected, lock released after exit OK")
        print("Native settings window: opens and reopens the same retained window OK")
        print("Native Focus gate: manual pause, active/inactive/unknown status and config migration OK")
        print("Native checks: split Unicode IPC, invalid frames, config persistence and secret separation OK")
        print("Native QR rendering: generated image decodes to the exact login URI OK")
    }
}
