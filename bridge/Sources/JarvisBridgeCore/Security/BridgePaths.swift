import Foundation

/// File-system locations used by the bridge. Injectable for tests.
public struct BridgePaths: Sendable {
    /// `~/Library/Application Support/Jarvis` (mode 0700)
    public var supportDirectory: URL
    /// `~/Library/Logs/Jarvis`
    public var logDirectory: URL

    public var tokenFile: URL { supportDirectory.appendingPathComponent("bridge.token") }
    public var killFile: URL { supportDirectory.appendingPathComponent("KILL") }
    /// Persisted state of the API-controlled kill switch (survives restarts).
    public var killStateFile: URL { supportDirectory.appendingPathComponent("bridge-killswitch.engaged") }
    public var policyFile: URL { supportDirectory.appendingPathComponent("bridge-policy.json") }
    public var auditLog: URL { logDirectory.appendingPathComponent("bridge-audit.jsonl") }

    public init(supportDirectory: URL, logDirectory: URL) {
        self.supportDirectory = supportDirectory
        self.logDirectory = logDirectory
    }

    public static var standard: BridgePaths {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return BridgePaths(
            supportDirectory: home.appendingPathComponent("Library/Application Support/Jarvis", isDirectory: true),
            logDirectory: home.appendingPathComponent("Library/Logs/Jarvis", isDirectory: true)
        )
    }

    /// Creates the support (0700) and log (0700) directories if needed.
    public func ensureDirectories() throws {
        let fm = FileManager.default
        for dir in [supportDirectory, logDirectory] {
            if !fm.fileExists(atPath: dir.path) {
                try fm.createDirectory(at: dir, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            }
        }
        // Enforce 0700 on the support dir even if it was created by another component.
        try fm.setAttributes([.posixPermissions: 0o700], ofItemAtPath: supportDirectory.path)
    }
}
