import Foundation

/// Appends JSON lines to the audit log. Writes happen on a private serial queue.
///
/// Never logs the token, typed text, set values, AppleScript sources, shortcut input or clipboard
/// contents: such fields are replaced by `{"redacted": true, "length": n}`.
public final class AuditLogger: Sendable {
    public let fileURL: URL
    private let queue = DispatchQueue(label: "com.jarvis.bridge.audit")
    private let maxBytes: Int

    /// Keys whose values must never reach the log.
    public static let sensitiveKeys: Set<String> = [
        "text", "value", "source", "input", "token", "password", "authorization", "clipboard",
    ]

    public init(fileURL: URL, maxBytes: Int = 20 * 1024 * 1024) {
        self.fileURL = fileURL
        self.maxBytes = maxBytes
    }

    public func log(_ entry: JSON) {
        let line = entry.serialized() + Data([0x0A])
        let url = fileURL
        let maxBytes = self.maxBytes
        queue.async {
            Self.rotateIfNeeded(url, maxBytes: maxBytes)
            let fd = open(url.path, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0o600)
            guard fd >= 0 else { return }
            defer { close(fd) }
            _ = line.withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
        }
    }

    /// Blocks until all pending entries are written (used by tests and shutdown).
    public func flush() {
        queue.sync {}
    }

    private static func rotateIfNeeded(_ url: URL, maxBytes: Int) {
        var st = stat()
        guard stat(url.path, &st) == 0, Int(st.st_size) > maxBytes else { return }
        let rotated = url.appendingPathExtension("1")
        try? FileManager.default.removeItem(at: rotated)
        try? FileManager.default.moveItem(at: url, to: rotated)
    }

    /// Produces a log-safe summary of request parameters.
    public static func summarize(_ value: JSON, depth: Int = 0) -> JSON {
        guard depth < 4 else { return "…" }
        switch value {
        case .object(let o):
            var out: [String: JSON] = [:]
            for (k, v) in o {
                if sensitiveKeys.contains(k.lowercased()) {
                    out[k] = redacted(v)
                } else {
                    out[k] = summarize(v, depth: depth + 1)
                }
            }
            return .object(out)
        case .array(let a):
            let head = a.prefix(10).map { summarize($0, depth: depth + 1) }
            return .array(a.count > 10 ? head + [.string("…(+\(a.count - 10))")] : head)
        case .string(let s):
            return .string(s.count > 200 ? String(s.prefix(200)) + "…" : s)
        default:
            return value
        }
    }

    static func redacted(_ v: JSON) -> JSON {
        switch v {
        case .string(let s): return ["redacted": true, "length": .int(s.count)]
        case .null: return .null
        default: return ["redacted": true]
        }
    }
}
