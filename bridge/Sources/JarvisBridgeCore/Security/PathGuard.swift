import Foundation

/// Validates user-supplied paths against the policy's allowed roots.
///
/// Rules:
/// - Paths must be absolute or start with `~/`.
/// - Any `..` component is rejected outright (no lexical traversal games).
/// - The deepest existing ancestor is resolved with `realpath(3)` so symlinks pointing
///   outside a root are detected; non-existent trailing components are appended verbatim
///   (they cannot be symlinks). The final component itself is not resolved, so operations
///   act on the entry itself (e.g. moving a symlink moves the link).
/// - The canonical result must equal a canonical root or lie strictly below it.
public struct PathGuard: Sendable {
    public let roots: [String]
    public let homeDirectory: String

    public init(roots: [String], homeDirectory: String = NSHomeDirectory()) {
        self.roots = roots
        self.homeDirectory = homeDirectory
    }

    /// Expands `~` and validates syntax. Does not check roots.
    public func expand(_ raw: String) throws -> String {
        guard !raw.isEmpty, raw.utf8.count < 4096, !raw.contains("\0") else {
            throw BridgeError.badRequest("invalid_path", "Path is empty, too long or contains NUL")
        }
        var path = raw
        if path == "~" {
            path = homeDirectory
        } else if path.hasPrefix("~/") {
            path = homeDirectory + String(path.dropFirst(1))
        } else if path.hasPrefix("~") {
            throw BridgeError.badRequest("invalid_path", "~user paths are not supported")
        }
        guard path.hasPrefix("/") else {
            throw BridgeError.badRequest("invalid_path", "Path must be absolute or start with ~/")
        }
        let components = path.split(separator: "/", omittingEmptySubsequences: true)
        if components.contains("..") {
            throw BridgeError(status: 403, code: "path_traversal", message: "Paths containing '..' are not allowed")
        }
        return path
    }

    /// Canonicalizes a path: realpath() of the deepest existing ancestor of the parent directory,
    /// plus the remaining (non-existent) components and the final component.
    public static func canonicalize(_ path: String) -> String {
        let comps = path.split(separator: "/", omittingEmptySubsequences: true)
            .filter { $0 != "." }
            .map(String.init)
        if comps.isEmpty { return "/" }
        let last = comps.last!
        var parentComps = Array(comps.dropLast())
        var tail: [String] = []
        while true {
            let candidate = "/" + parentComps.joined(separator: "/")
            if let real = realPath(candidate) {
                let rest = (tail.reversed() + [last]).joined(separator: "/")
                return real == "/" ? "/" + rest : real + "/" + rest
            }
            guard let popped = parentComps.popLast() else {
                return "/" + (tail.reversed() + [last]).joined(separator: "/")
            }
            tail.append(popped)
        }
    }

    /// Fully resolves a path (including its last component) if it exists.
    static func realPath(_ path: String) -> String? {
        guard let ptr = realpath(path, nil) else { return nil }
        defer { free(ptr) }
        return String(cString: ptr)
    }

    /// Canonical forms of the configured roots (fully resolved when they exist).
    public func canonicalRoots() -> [String] {
        roots.compactMap { root in
            guard let expanded = try? expand(root) else { return nil }
            let trimmed = expanded.count > 1 && expanded.hasSuffix("/") ? String(expanded.dropLast()) : expanded
            return Self.realPath(trimmed) ?? Self.canonicalize(trimmed)
        }
    }

    /// Validates `raw` and returns the canonical absolute path to operate on.
    /// - Parameter allowRoot: whether the path may equal a root itself (e.g. reveal) or must be inside it.
    public func check(_ raw: String, allowRoot: Bool = false) throws -> String {
        let expanded = try expand(raw)
        let canonical = Self.canonicalize(expanded)
        for root in canonicalRoots() {
            if canonical == root {
                if allowRoot { return canonical }
                continue
            }
            let prefix = root == "/" ? "/" : root + "/"
            if canonical.hasPrefix(prefix) { return canonical }
        }
        throw BridgeError(
            status: 403, code: "path_not_allowed",
            message: "Path is outside the allowed file roots",
            extra: ["allowedFileRoots": .array(roots.map { .string($0) })])
    }
}
