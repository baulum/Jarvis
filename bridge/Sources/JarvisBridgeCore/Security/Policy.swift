import Foundation
import os

/// Action names used in the policy's `allowedActions` list and in the audit log.
public enum BridgeAction {
    public static let health = "health"
    public static let killSwitch = "killswitch"
    public static let policyRead = "policy.read"
    public static let permissionsRequest = "permissions.request"
    public static let appsList = "apps.list"
    public static let appsLaunch = "apps.launch"
    public static let appsActivate = "apps.activate"
    public static let appsQuit = "apps.quit"
    public static let windowsList = "windows.list"
    public static let frontmost = "frontmost"
    public static let uiTree = "ui.tree"
    public static let uiPress = "ui.press"
    public static let uiSetValue = "ui.setValue"
    public static let inputType = "input.type"
    public static let inputKey = "input.key"
    public static let inputClick = "input.click"
    public static let screenshot = "screenshot"
    public static let clipboardRead = "clipboard.read"
    public static let clipboardWrite = "clipboard.write"
    public static let filesSearch = "files.search"
    public static let filesMove = "files.move"
    public static let filesRename = "files.rename"
    public static let filesMkdir = "files.mkdir"
    public static let filesReveal = "files.reveal"
    public static let appleScript = "applescript"
    public static let shortcutsList = "shortcuts.list"
    public static let shortcutsRun = "shortcuts.run"
    public static let openURL = "openUrl"

    /// Actions that are never subject to `allowedActions` (they are needed to operate safely).
    public static let alwaysAllowed: Set<String> = [health, killSwitch, policyRead]

    /// All policy-controlled actions.
    public static let all: [String] = [
        permissionsRequest, appsList, appsLaunch, appsActivate, appsQuit, windowsList, frontmost,
        uiTree, uiPress, uiSetValue, inputType, inputKey, inputClick, screenshot,
        clipboardRead, clipboardWrite, filesSearch, filesMove, filesRename, filesMkdir, filesReveal,
        appleScript, shortcutsList, shortcutsRun, openURL,
    ]
}

/// Defense-in-depth policy loaded from `bridge-policy.json`.
public struct BridgePolicy: Codable, Sendable, Equatable {
    public var allowedActions: [String]
    /// AppleScript additionally requires this flag (default false).
    public var allowAppleScript: Bool
    public var allowedFileRoots: [String]
    public var blockedApps: [String]

    public static let `default` = BridgePolicy(
        allowedActions: BridgeAction.all,
        allowAppleScript: false,
        allowedFileRoots: ["~/Documents", "~/Desktop", "~/Downloads", "~/Developer"],
        blockedApps: ["com.apple.keychainaccess", "com.apple.systempreferences"]
    )

    /// Used when the policy file exists but cannot be parsed: everything policy-controlled is denied.
    public static let failClosed = BridgePolicy(
        allowedActions: [], allowAppleScript: false, allowedFileRoots: [],
        blockedApps: BridgePolicy.default.blockedApps)

    public init(allowedActions: [String], allowAppleScript: Bool, allowedFileRoots: [String], blockedApps: [String]) {
        self.allowedActions = allowedActions
        self.allowAppleScript = allowAppleScript
        self.allowedFileRoots = allowedFileRoots
        self.blockedApps = blockedApps
    }

    /// Missing keys fall back to defaults so older policy files keep working.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = BridgePolicy.default
        allowedActions = try c.decodeIfPresent([String].self, forKey: .allowedActions) ?? d.allowedActions
        allowAppleScript = try c.decodeIfPresent(Bool.self, forKey: .allowAppleScript) ?? d.allowAppleScript
        allowedFileRoots = try c.decodeIfPresent([String].self, forKey: .allowedFileRoots) ?? d.allowedFileRoots
        blockedApps = try c.decodeIfPresent([String].self, forKey: .blockedApps) ?? d.blockedApps
    }

    public func allows(action: String) -> Bool {
        if BridgeAction.alwaysAllowed.contains(action) { return true }
        if action == BridgeAction.appleScript && !allowAppleScript { return false }
        return allowedActions.contains(action)
    }

    public func isBlocked(bundleId: String?) -> Bool {
        guard let bundleId else { return false }
        return blockedApps.contains { $0.caseInsensitiveCompare(bundleId) == .orderedSame }
    }

    public var json: JSON {
        [
            "allowedActions": .array(allowedActions.map { .string($0) }),
            "allowAppleScript": .bool(allowAppleScript),
            "allowedFileRoots": .array(allowedFileRoots.map { .string($0) }),
            "blockedApps": .array(blockedApps.map { .string($0) }),
        ]
    }
}

/// Loads the policy file, creating it with defaults if missing, and reloads it when its mtime changes.
public final class PolicyStore: Sendable {
    private struct State {
        var policy: BridgePolicy
        var mtime: Date?
        var size: Int?
        var error: String?
    }

    public let fileURL: URL
    private let state: OSAllocatedUnfairLock<State>

    public init(fileURL: URL) {
        self.fileURL = fileURL
        self.state = OSAllocatedUnfairLock(initialState: State(policy: .default, mtime: nil, size: nil, error: nil))
        _ = current()
    }

    /// Returns the effective policy, reloading from disk if the file changed.
    public func current() -> BridgePolicy {
        let fm = FileManager.default
        let attrs = try? fm.attributesOfItem(atPath: fileURL.path)
        if attrs == nil {
            createDefaultFile()
        }
        let a = attrs ?? (try? fm.attributesOfItem(atPath: fileURL.path))
        let mtime = a?[.modificationDate] as? Date
        let size = (a?[.size] as? NSNumber)?.intValue

        return state.withLock { s in
            if s.mtime != nil, s.mtime == mtime, s.size == size { return s.policy }
            s.mtime = mtime
            s.size = size
            guard let data = try? Data(contentsOf: fileURL) else {
                s.policy = .failClosed
                s.error = "Policy file unreadable"
                BridgeLog.error("Policy file unreadable; failing closed")
                return s.policy
            }
            do {
                s.policy = try JSONDecoder().decode(BridgePolicy.self, from: data)
                s.error = nil
                BridgeLog.info("Policy loaded from \(fileURL.path)")
            } catch {
                s.policy = .failClosed
                s.error = "Policy file invalid: \(error.localizedDescription)"
                BridgeLog.error("Policy file invalid; failing closed (all policy-controlled actions denied)")
            }
            return s.policy
        }
    }

    public var loadError: String? {
        _ = current()
        return state.withLock { $0.error }
    }

    private func createDefaultFile() {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        guard let data = try? encoder.encode(BridgePolicy.default) else { return }
        let fm = FileManager.default
        if fm.createFile(atPath: fileURL.path, contents: data, attributes: [.posixPermissions: 0o600]) {
            BridgeLog.info("Created default policy at \(fileURL.path)")
        }
    }
}
