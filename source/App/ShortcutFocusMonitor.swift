import AppKit
import Foundation
import Darwin

enum ShortcutFocusState: Equatable {
    case unknown, off, on

    static func parse(_ data: Data) -> Self {
        guard data.count <= 32, let text = String(data: data, encoding: .utf8) else { return .unknown }
        switch text.trimmingCharacters(in: .whitespacesAndNewlines) {
        case "ON": return .on
        case "OFF": return .off
        default: return .unknown
        }
    }
}

/// Runs only when opted in. One child at a time, no shell, bounded output and timeout.
@MainActor
final class ShortcutFocusMonitor {
    var onResult: ((ShortcutFocusState) -> Void)?
    private var process: Process?
    private var timer: Timer?
    private var timeout: Timer?
    private var name = ""
    private var generation = UUID()
    private var timedOut = false
    private let executable: URL
    private let arguments: (String, URL) -> [String]
    private let timeoutSeconds: TimeInterval

    init(executable: URL = URL(fileURLWithPath: "/usr/bin/shortcuts"), timeoutSeconds: TimeInterval = 10,
         arguments: @escaping (String, URL) -> [String] = { ["run", $0, "--output-path", $1.path] }) {
        self.executable = executable
        self.timeoutSeconds = timeoutSeconds
        self.arguments = arguments
    }

    func start(name: String, interval: TimeInterval) {
        stop()
        self.name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !self.name.isEmpty, self.name.utf8.count <= 200 else { return }
        check()
        timer = Timer.scheduledTimer(withTimeInterval: max(15, interval), repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.check() }
        }
        timer?.tolerance = 2
    }

    func stop() {
        generation = UUID()
        timer?.invalidate(); timer = nil
        timeout?.invalidate(); timeout = nil
        if let process, process.isRunning { Self.terminate(process) }
        process = nil
        name = ""
        onResult?(.unknown)
    }

    func check() {
        guard process == nil, !name.isEmpty else { return }
        timedOut = false
        let token = generation
        let child = Process()
        // A private directory prevents another process replacing our output path.
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let output = directory.appendingPathComponent("state.txt")
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                                   attributes: [.posixPermissions: 0o700])
        } catch { onResult?(.unknown); return }
        child.executableURL = executable
        child.arguments = arguments(name, output)
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        child.terminationHandler = { [weak self] completed in
            var state = ShortcutFocusState.unknown
            if completed.terminationStatus == 0,
               let handle = try? FileHandle(forReadingFrom: output) {
                let data = try? handle.read(upToCount: 33)
                try? handle.close()
                if let data { state = .parse(data) }
            }
            try? FileManager.default.removeItem(at: directory)
            let result = state
            DispatchQueue.main.async {
                guard let self, self.generation == token, self.process === completed else { return }
                self.timeout?.invalidate(); self.timeout = nil
                self.process = nil
                self.onResult?(self.timedOut ? .unknown : result)
            }
        }
        process = child
        do {
            try child.run()
            timeout = Timer.scheduledTimer(withTimeInterval: timeoutSeconds, repeats: false) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self, self.process === child, self.generation == token else { return }
                    self.timedOut = true
                    self.onResult?(.unknown)
                    Self.terminate(child)
                    // Keep the process slot until termination; discard late output.
                }
            }
        } catch {
            process = nil
            try? FileManager.default.removeItem(at: directory)
            onResult?(.unknown)
        }
    }

    private static func terminate(_ child: Process) {
        guard child.isRunning else { return }
        child.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 1) {
            if child.isRunning { kill(child.processIdentifier, SIGKILL) }
        }
    }
}
