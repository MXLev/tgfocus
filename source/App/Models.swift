import AppKit
import Foundation
import CoreImage
import CoreImage.CIFilterBuiltins
import Security
import SwiftUI
import UserNotifications

struct Person: Codable, Identifiable, Equatable {
    var id: String
    var name: String
}

struct ChatRule: Codable, Identifiable, Equatable {
    var id: String
    var name: String
    var keywordsOnly = true
}

struct FilterConfig: Codable {
    var enabled = true
    var allPrivate = false
    var keywords = ["срочно", "позвони", "перезвони", "нужна помощь", "экстренно"]
    var people: [Person] = []
    var chats: [ChatRule] = []
    var showPreview = true
    var playSound = true
    var allowMentions = true

    init() {}

    enum CodingKeys: String, CodingKey {
        case enabled, allPrivate, keywords, people, chats, showPreview, playSound, allowMentions
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        enabled = try values.decodeIfPresent(Bool.self, forKey: .enabled) ?? true
        allPrivate = try values.decodeIfPresent(Bool.self, forKey: .allPrivate) ?? false
        keywords = try values.decodeIfPresent([String].self, forKey: .keywords) ?? keywords
        people = try values.decodeIfPresent([Person].self, forKey: .people) ?? []
        chats = try values.decodeIfPresent([ChatRule].self, forKey: .chats) ?? []
        showPreview = try values.decodeIfPresent(Bool.self, forKey: .showPreview) ?? true
        playSound = try values.decodeIfPresent(Bool.self, forKey: .playSound) ?? true
        allowMentions = try values.decodeIfPresent(Bool.self, forKey: .allowMentions) ?? true
    }
}

struct Credentials: Codable {
    var apiId = ""
    var apiHash = ""
    var session = ""
}

enum QRRenderer {
    static func image(for url: String) -> NSImage? {
        guard url.hasPrefix("tg://login?token=") else { return nil }
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(url.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage?.transformed(by: CGAffineTransform(scaleX: 8, y: 8)),
              let image = CIContext(options: [.useSoftwareRenderer: true]).createCGImage(output, from: output.extent) else { return nil }
        return NSImage(cgImage: image, size: NSSize(width: image.width, height: image.height))
    }
}

enum CredentialStore {
    static let service = "local.mxlev.TelegramFocus"
    static func load() throws -> Credentials? {
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: "telegram",
            kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw KeychainError(status: status) }
        return try JSONDecoder().decode(Credentials.self, from: data)
    }
    static func save(_ credentials: Credentials) throws {
        let data = try JSONEncoder().encode(credentials)
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: "telegram"]
        var status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var attributes = query
            attributes[kSecValueData as String] = data
            attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(attributes as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }
    static func delete() throws {
        let status = SecItemDelete([kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service, kSecAttrAccount as String: "telegram"] as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw KeychainError(status: status) }
    }
    struct KeychainError: LocalizedError {
        let status: OSStatus
        var errorDescription: String? { "Связка ключей: \(SecCopyErrorMessageString(status, nil) as String? ?? String(status))" }
    }
}

struct Dialog: Identifiable {
    var id: String
    var name: String
    var kind: String
}

struct Activity: Identifiable {
    let id = UUID()
    let date = Date()
    var title: String
    var reason: String
    var allowed: Bool
    var url: String
}

final class LineDecoder: @unchecked Sendable {
    private var pending = Data()
    private let lock = NSLock()
    func consume(_ data: Data) -> [[String: Any]] {
        lock.lock()
        defer { lock.unlock() }
        pending.append(data)
        var objects: [[String: Any]] = []
        while let range = pending.range(of: Data([10])) {
            let line = pending.subdata(in: pending.startIndex..<range.lowerBound)
            pending.removeSubrange(pending.startIndex..<range.upperBound)
            if let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any] { objects.append(object) }
        }
        return objects
    }
}

@MainActor
final class AppModel: ObservableObject {
    static let shared = AppModel()
    @Published var config = FilterConfig()
    @Published var credentials = Credentials()
    @Published var state = "disconnected"
    @Published var statusText = "Аккаунт ещё не подключён"
    @Published var accountName = ""
    @Published var errorText = ""
    @Published var helperReady = false
    @Published var busy = false
    @Published var notificationStatus = "Не проверено"
    @Published var notificationsAllowed = false
    @Published var dialogs: [Dialog] = []
    @Published var loadingDialogs = false
    @Published var activities: [Activity] = []
    @Published var passed = 0
    @Published var suppressed = 0
    @Published var simulationAllowed: Bool?
    @Published var simulationReason = ""
    @Published var selectedPage = "overview"
    @Published var showLogoutConfirmation = false
    @Published var qrImage: NSImage?
    @Published var qrExpiresAt = Date.distantPast
    @Published var deliveryMethod = ""
    @Published var canResend = false
    @Published var resendAt = Date.distantPast
    var process: Process?
    private var input: FileHandle?
    private var outputPipe: Pipe?
    private var shouldReconnect = false
    private var configURL: URL?

    var connected: Bool { state == "connected" }
    var authStep: Bool { ["phone", "code", "password", "qr", "qrExpired", "authUnsupported"].contains(state) }
    var statusColor: Color { connected ? .green : state == "connecting" || state == "reconnecting" ? .orange : .secondary }

    init() {
        do {
            let root: URL
            if let override = ProcessInfo.processInfo.environment["TELEGRAM_FOCUS_DATA_DIR"] {
                root = URL(fileURLWithPath: override, isDirectory: true)
            } else {
                root = try FileManager.default.url(for: .applicationSupportDirectory,
                    in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("TelegramFocus")
            }
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            configURL = root.appendingPathComponent("rules.json")
            if FileManager.default.fileExists(atPath: configURL!.path) {
                config = try JSONDecoder().decode(FilterConfig.self, from: Data(contentsOf: configURL!))
            }
        } catch { errorText = "Не удалось прочитать настройки: \(error.localizedDescription)" }
        do {
            if let saved = try CredentialStore.load() {
                credentials = saved
                shouldReconnect = !saved.session.isEmpty
            }
        } catch { errorText = error.localizedDescription }
        startHelper()
        Task { await refreshNotificationStatus() }
    }

    func startHelper() {
        guard process?.isRunning != true else { return }
        helperReady = false
        guard let resource = Bundle.main.resourceURL else { return }
        let executable = resource.appendingPathComponent("helper/focus-worker")
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            errorText = "В сборке отсутствует модуль Telegram. Пересобери приложение по инструкции."
            return
        }
        let worker = Process()
        worker.executableURL = executable
        let stdinPipe = Pipe(), stdoutPipe = Pipe()
        worker.standardInput = stdinPipe
        worker.standardOutput = stdoutPipe
        worker.standardError = FileHandle.nullDevice
        input = stdinPipe.fileHandleForWriting
        outputPipe = stdoutPipe
        let decoder = LineDecoder()
        stdoutPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            for object in decoder.consume(data) {
                DispatchQueue.main.async { self?.receive(object) }
            }
        }
        worker.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self else { return }
                self.helperReady = false
                self.busy = false
                self.loadingDialogs = false
                self.state = "disconnected"
                self.statusText = "Модуль Telegram остановлен"
            }
        }
        do { try worker.run(); process = worker }
        catch { errorText = "Не удалось запустить модуль Telegram: \(error.localizedDescription)" }
    }

    func send(_ object: [String: Any]) {
        guard process?.isRunning == true, let input else {
            errorText = "Модуль Telegram не запущен. Нажми «Перезапустить модуль» в подключении."
            busy = false
            return
        }
        do {
            var data = try JSONSerialization.data(withJSONObject: object)
            data.append(10)
            try input.write(contentsOf: data)
        } catch { errorText = "Не удалось передать команду модулю Telegram."; busy = false }
    }

    func saveConfig() {
        do {
            let data = try JSONEncoder().encode(config)
            if let configURL {
                try data.write(to: configURL, options: .atomic)
                try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configURL.path)
            }
            if helperReady, let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] {
                send(["command": "config", "config": object])
            }
        } catch { errorText = "Не удалось сохранить правила: \(error.localizedDescription)" }
        simulationAllowed = nil
        simulationReason = ""
    }

    func connect() {
        let api = credentials.apiId.trimmingCharacters(in: .whitespacesAndNewlines)
        let hash = credentials.apiHash.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let value = Int(api), value > 0, hash.count == 32,
              hash.allSatisfy({ $0.isHexDigit && $0.isASCII }) else {
            errorText = "Введи числовой API ID и API Hash из 32 символов с my.telegram.org."
            return
        }
        guard helperReady else { errorText = "Подожди запуска модуля Telegram."; return }
        credentials.apiId = api
        credentials.apiHash = hash
        do { try CredentialStore.save(credentials) }
        catch { errorText = error.localizedDescription; return }
        errorText = ""
        busy = true
        send(["command": "connect", "apiId": api, "apiHash": hash, "session": credentials.session])
    }

    func auth(command: String, value: String) {
        guard !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        busy = true
        errorText = ""
        send(["command": command, command: value])
    }

    func authAction(_ command: String) {
        guard !busy else { return }
        busy = true
        errorText = ""
        if command == "qr" { qrImage = nil }
        send(["command": command])
    }

    func receive(_ object: [String: Any]) {
        guard let type = object["type"] as? String else { return }
        switch type {
        case "ready":
            helperReady = true
            saveConfig()
            if shouldReconnect { shouldReconnect = false; connect() }
        case "state":
            state = object["state"] as? String ?? "disconnected"
            statusText = object["message"] as? String ?? ""
            if let name = object["name"] as? String { accountName = name }
            busy = state == "connecting"
            if state != "qr" { qrImage = nil }
            if connected { errorText = "" }
        case "qr":
            if let url = object["url"] as? String {
                qrImage = QRRenderer.image(for: url)
                if qrImage == nil { errorText = "Не удалось показать QR-код. Попробуй обновить его." }
            }
            qrExpiresAt = Date(timeIntervalSince1970: object["expiresAt"] as? Double ?? 0)
            busy = false
        case "delivery":
            deliveryMethod = object["method"] as? String ?? ""
            canResend = object["canResend"] as? Bool ?? false
            resendAt = Date(timeIntervalSince1970: object["resendAt"] as? Double ?? 0)
        case "session":
            credentials.session = object["session"] as? String ?? ""
            do { try CredentialStore.save(credentials) }
            catch { errorText = "Сессия работает, но не сохранена: \(error.localizedDescription)" }
        case "error":
            errorText = object["message"] as? String ?? "Неизвестная ошибка"
            busy = false
            loadingDialogs = false
            if object["recoverable"] as? Bool != true {
                state = "disconnected"
                statusText = "Нужно переподключиться"
            }
        case "invalidSession":
            credentials.session = ""
            try? CredentialStore.save(credentials)
            state = "disconnected"; busy = false
            statusText = "Сессия отозвана"
            errorText = "Войди в Telegram заново: предыдущая сессия больше не действует."
        case "loggedOut":
            credentials.session = ""
            do { try CredentialStore.delete() }
            catch { errorText = error.localizedDescription }
            state = "disconnected"; busy = false; dialogs = []; accountName = ""
            statusText = "Выход выполнен. Сессия Telegram отозвана."
        case "dialogs":
            dialogs = (object["items"] as? [[String: Any]] ?? []).compactMap { row in
                guard let id = row["id"] as? String, let name = row["name"] as? String,
                      let kind = row["kind"] as? String else { return nil }
                return Dialog(id: id, name: name, kind: kind)
            }
            loadingDialogs = false
        case "simulation":
            simulationAllowed = object["allowed"] as? Bool ?? false
            simulationReason = object["reason"] as? String ?? ""
        case "message":
            let allowed = object["allowed"] as? Bool ?? false
            let title = object["title"] as? String ?? "Telegram"
            let reason = object["reason"] as? String ?? ""
            let url = object["url"] as? String ?? "tg://"
            if allowed { passed += 1 } else { suppressed += 1 }
            activities.insert(Activity(title: title, reason: reason, allowed: allowed, url: url), at: 0)
            if activities.count > 50 { activities.removeLast() }
            if allowed {
                let body = config.showPreview ? (object["text"] as? String ?? "Новое сообщение") : "Новое важное сообщение в Telegram"
                let subtitle = object["kind"] as? String == "private" ? "" : object["sender"] as? String ?? ""
                notify(title: title, subtitle: subtitle, body: body, url: url,
                    id: "tg-\(object["chatId"] ?? "")-\(object["messageId"] ?? "")")
            }
        default: break
        }
    }

    func refreshNotificationStatus() async {
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        notificationsAllowed = [.authorized, .provisional].contains(settings.authorizationStatus)
        switch settings.authorizationStatus {
        case .authorized: notificationStatus = "Разрешены"
        case .provisional: notificationStatus = "Доставляются тихо"
        case .denied: notificationStatus = "Запрещены в macOS"
        default: notificationStatus = "Нужно разрешение"
        }
    }

    func requestNotifications(test: Bool = false) {
        errorText = ""
        NSApp.activate(ignoringOtherApps: true)
        Task {
            do {
                let allowed = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
                await refreshNotificationStatus()
                if allowed && test {
                    notify(title: "Telegram Focus", subtitle: "Проверка уведомлений",
                        body: "Срочно: фильтр готов к работе. Теперь разреши Telegram Focus в нужном режиме фокусирования.",
                        url: "", id: UUID().uuidString)
                } else if !allowed {
                    errorText = "Разреши уведомления Telegram Focus в Системных настройках → Уведомления."
                }
            } catch {
                await refreshNotificationStatus()
                errorText = "macOS не разрешила уведомления. Открой Системные настройки → Уведомления → Telegram Focus и включи разрешение, затем повтори тест."
            }
        }
    }

    func notify(title: String, subtitle: String, body: String, url: String, id: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.subtitle = subtitle
        content.body = body
        if config.playSound { content.sound = .default }
        content.interruptionLevel = .active
        content.userInfo = ["url": url]
        UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: id, content: content, trigger: nil)) { [weak self] error in
            if error != nil {
                DispatchQueue.main.async { self?.errorText = "macOS не приняла уведомление. Проверь разрешение в настройках." }
            }
        }
    }

    func loadDialogs() {
        guard connected else { errorText = "Сначала подключи Telegram во вкладке «Подключение»."; return }
        loadingDialogs = true
        send(["command": "dialogs"])
    }

    func simulate(text: String, kind: String, chatId: String, senderId: String, mentioned: Bool = false) {
        send(["command": "simulate", "message": ["text": text, "kind": kind, "chatId": chatId, "senderId": senderId, "mentioned": mentioned]])
    }

    func logout() {
        guard helperReady else { return }
        busy = true
        send(["command": "logout"])
    }

    func shutdown() {
        outputPipe?.fileHandleForReading.readabilityHandler = nil
        if process?.isRunning == true {
            send(["command": "quit"])
            try? input?.close()
        }
    }

    func openTelegram(_ string: String) {
        guard let url = URL(string: string), url.scheme == "tg" else { return }
        NSWorkspace.shared.open(url)
    }

    func openSettings(_ section: String) {
        if let url = URL(string: "x-apple.systempreferences:\(section)") { NSWorkspace.shared.open(url) }
    }
}
