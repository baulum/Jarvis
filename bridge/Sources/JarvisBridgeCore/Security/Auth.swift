import Foundation
import Security

public enum TokenStoreError: Error, CustomStringConvertible {
    case randomFailed(OSStatus)
    case io(String)
    case insecure(String)

    public var description: String {
        switch self {
        case .randomFailed(let s): return "SecRandomCopyBytes failed (\(s))"
        case .io(let m): return "Token file I/O error: \(m)"
        case .insecure(let m): return "Token file is insecure: \(m)"
        }
    }
}

/// Loads or creates the shared bearer token.
public enum TokenStore {
    public static let minimumLength = 32

    /// Returns the token stored at `url`, generating a new one (32 random bytes, hex) if missing.
    /// The file is created with mode 0600 and the parent directory must already exist.
    public static func loadOrCreate(at url: URL) throws -> String {
        let fm = FileManager.default
        if fm.fileExists(atPath: url.path) {
            let attrs = try fm.attributesOfItem(atPath: url.path)
            if let owner = attrs[.ownerAccountID] as? NSNumber, owner.uint32Value != getuid() {
                throw TokenStoreError.insecure("owned by another user")
            }
            if attrs[.type] as? FileAttributeType != .typeRegular {
                throw TokenStoreError.insecure("not a regular file")
            }
            // Tighten permissions if needed.
            try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            let data = try Data(contentsOf: url)
            let token = String(decoding: data, as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
            if token.count >= minimumLength, !token.contains(where: { $0.isWhitespace }) {
                return token
            }
            BridgeLog.warn("Token file malformed; regenerating")
            try fm.removeItem(at: url)
        }
        let token = try generateToken()
        try writeExclusive(Data((token + "\n").utf8), to: url, mode: 0o600)
        BridgeLog.info("Generated new bridge token at \(url.path)")
        return token
    }

    public static func generateToken(byteCount: Int = 32) throws -> String {
        var bytes = [UInt8](repeating: 0, count: byteCount)
        let status = SecRandomCopyBytes(kSecRandomDefault, byteCount, &bytes)
        guard status == errSecSuccess else { throw TokenStoreError.randomFailed(status) }
        return bytes.map { String(format: "%02x", $0) }.joined()
    }

    /// Creates a file with O_EXCL and the given mode so it is never world-readable, even briefly.
    static func writeExclusive(_ data: Data, to url: URL, mode: mode_t) throws {
        let fd = open(url.path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, mode)
        guard fd >= 0 else { throw TokenStoreError.io(String(cString: strerror(errno))) }
        defer { close(fd) }
        fchmod(fd, mode)
        let written = data.withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
        guard written == data.count else { throw TokenStoreError.io("short write") }
    }
}

public enum Auth {
    /// Compares two secrets in time independent of where they differ.
    public static func constantTimeEquals(_ a: String, _ b: String) -> Bool {
        let x = Array(a.utf8)
        let y = Array(b.utf8)
        let n = max(x.count, y.count)
        var diff: UInt8 = x.count == y.count ? 0 : 1
        for i in 0..<n {
            let xb = i < x.count ? x[i] : 0
            let yb = i < y.count ? y[i] : 0
            diff |= xb ^ yb
        }
        return diff == 0
    }

    /// Extracts the token from an `Authorization: Bearer <token>` header value.
    public static func bearerToken(from header: String?) -> String? {
        guard let header else { return nil }
        let trimmed = header.trimmingCharacters(in: .whitespaces)
        let prefix = "bearer "
        guard trimmed.count > prefix.count, trimmed.lowercased().hasPrefix(prefix) else { return nil }
        let token = trimmed.dropFirst(prefix.count).trimmingCharacters(in: .whitespaces)
        return token.isEmpty ? nil : token
    }

    public static func isAuthorized(header: String?, expected: String) -> Bool {
        guard let provided = bearerToken(from: header) else { return false }
        return constantTimeEquals(provided, expected)
    }
}
