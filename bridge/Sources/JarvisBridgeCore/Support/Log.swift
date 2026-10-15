import Foundation

/// Operational logging to stderr (redirected to ~/Library/Logs/Jarvis/bridge.log by launchd).
/// Never pass tokens, typed text or clipboard contents to these functions.
public enum BridgeLog {
    // Only used while holding `lock`.
    nonisolated(unsafe) private static let formatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let lock = NSLock()

    public static func info(_ message: String) { write("INFO", message) }
    public static func warn(_ message: String) { write("WARN", message) }
    public static func error(_ message: String) { write("ERROR", message) }

    private static func write(_ level: String, _ message: String) {
        lock.lock()
        defer { lock.unlock() }
        let line = "\(formatter.string(from: Date())) [\(level)] \(message)\n"
        FileHandle.standardError.write(Data(line.utf8))
    }
}

/// ISO-8601 timestamp helper.
enum Timestamp {
    static func now() -> String { string(Date()) }

    static func string(_ date: Date) -> String {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f.string(from: date)
    }
}

/// Runs blocking work (AX IPC, file ops) on a global queue instead of the cooperative pool.
func runBlocking<T: Sendable>(_ body: @escaping @Sendable () throws -> T) async throws -> T {
    try await withCheckedThrowingContinuation { cont in
        DispatchQueue.global(qos: .userInitiated).async {
            do {
                cont.resume(returning: try body())
            } catch {
                cont.resume(throwing: error)
            }
        }
    }
}
