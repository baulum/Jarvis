import Foundation
import os

/// Kill switch: engaged if the API flag is set OR the `KILL` file exists.
///
/// The API flag is persisted (as a marker file) so it survives restarts of the bridge.
/// The `KILL` file is a user-level override that the API can never clear.
public final class KillSwitch: Sendable {
    private let killFile: URL
    private let stateFile: URL
    private let apiFlag: OSAllocatedUnfairLock<Bool>

    public init(killFile: URL, stateFile: URL) {
        self.killFile = killFile
        self.stateFile = stateFile
        self.apiFlag = OSAllocatedUnfairLock(initialState: FileManager.default.fileExists(atPath: stateFile.path))
    }

    public var fileEngaged: Bool {
        // lstat-based existence check; any entry named KILL engages the switch.
        var st = stat()
        return lstat(killFile.path, &st) == 0
    }

    public var apiEngaged: Bool { apiFlag.withLock { $0 } }

    public var isEngaged: Bool { apiEngaged || fileEngaged }

    public func setEngaged(_ engaged: Bool) {
        apiFlag.withLock { $0 = engaged }
        let fm = FileManager.default
        if engaged {
            if !fm.fileExists(atPath: stateFile.path) {
                fm.createFile(atPath: stateFile.path, contents: Data(Timestamp.now().utf8), attributes: [.posixPermissions: 0o600])
            }
        } else {
            try? fm.removeItem(at: stateFile)
        }
    }

    public var statusJSON: JSON {
        ["killSwitch": .bool(isEngaged), "apiEngaged": .bool(apiEngaged), "fileEngaged": .bool(fileEngaged)]
    }
}
