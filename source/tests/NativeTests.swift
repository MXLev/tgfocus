import Foundation
import CoreImage

@main
struct NativeTests {
    static func main() throws {
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
        let migrated = try JSONDecoder().decode(FilterConfig.self, from: JSONSerialization.data(withJSONObject: legacy))
        precondition(migrated.allowMentions)
        precondition(migrated.people == config.people && migrated.chats == config.chats)
        precondition(migrated.keywords == config.keywords)
        config.allowMentions = false
        let disabledMentions = try JSONDecoder().decode(FilterConfig.self, from: JSONEncoder().encode(config))
        precondition(!disabledMentions.allowMentions)
        let fakeURL = "tg://login?token=TEST_NOT_A_REAL_TOKEN"
        guard let qr = QRRenderer.image(for: fakeURL) else { fatalError("QR image generation failed") }
        guard let tiff = qr.tiffRepresentation, let image = CIImage(data: tiff) else { fatalError("QR image serialization failed") }
        guard let detector = CIDetector(ofType: CIDetectorTypeQRCode, context: CIContext(options: [.useSoftwareRenderer: true]),
                  options: [CIDetectorAccuracy: CIDetectorAccuracyHigh]) else { fatalError("QR detector creation failed") }
        guard let result = detector.features(in: image).first as? CIQRCodeFeature else { fatalError("QR image decoding failed") }
        precondition(result.messageString == fakeURL)
        precondition(QRRenderer.image(for: "https://example.org") == nil)
        print("Native checks: split Unicode IPC, invalid frames, config persistence and secret separation OK")
        print("Native QR rendering: generated image decodes to the exact login URI OK")
    }
}
