import Foundation
import XCTest

@testable import JarvisBridgeCore

final class AuthTests: XCTestCase {
    func testConstantTimeEquals() {
        XCTAssertTrue(Auth.constantTimeEquals("abc123", "abc123"))
        XCTAssertFalse(Auth.constantTimeEquals("abc123", "abc124"))
        XCTAssertFalse(Auth.constantTimeEquals("abc123", "abc1234"))
        XCTAssertFalse(Auth.constantTimeEquals("abc1234", "abc123"))
        XCTAssertFalse(Auth.constantTimeEquals("", "x"))
        XCTAssertTrue(Auth.constantTimeEquals("", ""))
        // Prefix of the right token with trailing NUL padding must not match.
        XCTAssertFalse(Auth.constantTimeEquals("abc\0", "abc"))
    }

    func testBearerParsing() {
        XCTAssertEqual(Auth.bearerToken(from: "Bearer tok"), "tok")
        XCTAssertEqual(Auth.bearerToken(from: "bearer   tok  "), "tok")
        XCTAssertNil(Auth.bearerToken(from: "Basic dG9r"))
        XCTAssertNil(Auth.bearerToken(from: "Bearer "))
        XCTAssertNil(Auth.bearerToken(from: nil))
        XCTAssertTrue(Auth.isAuthorized(header: "Bearer secret", expected: "secret"))
        XCTAssertFalse(Auth.isAuthorized(header: "Bearer secreT", expected: "secret"))
    }

    func testTokenGenerationAndPersistence() throws {
        let dir = try TempDir()
        let file = dir.sub("bridge.token")
        let token = try TokenStore.loadOrCreate(at: file)
        XCTAssertEqual(token.count, 64)
        XCTAssertTrue(token.allSatisfy { $0.isHexDigit })
        let perms = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(perms?.intValue, 0o600)
        // Second load returns the same token.
        XCTAssertEqual(try TokenStore.loadOrCreate(at: file), token)
        // Two generated tokens differ.
        XCTAssertNotEqual(try TokenStore.generateToken(), try TokenStore.generateToken())
    }

    func testMalformedTokenIsRegenerated() throws {
        let dir = try TempDir()
        let file = dir.sub("bridge.token")
        try Data("short".utf8).write(to: file)
        let token = try TokenStore.loadOrCreate(at: file)
        XCTAssertEqual(token.count, 64)
    }

    func testSupportDirectoryIs0700() throws {
        let dir = try TempDir()
        let paths = BridgePaths(supportDirectory: dir.sub("support"), logDirectory: dir.sub("logs"))
        try paths.ensureDirectories()
        let perms = try FileManager.default.attributesOfItem(atPath: paths.supportDirectory.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(perms?.intValue, 0o700)
    }
}

final class PathGuardTests: XCTestCase {
    var dir: TempDir!
    var root: String!
    var outside: String!
    var guardian: PathGuard!

    override func setUpWithError() throws {
        dir = try TempDir()
        root = dir.sub("Documents").path
        outside = dir.sub("secret").path
        try FileManager.default.createDirectory(atPath: root + "/sub", withIntermediateDirectories: true)
        try FileManager.default.createDirectory(atPath: outside, withIntermediateDirectories: true)
        guardian = PathGuard(roots: ["~/Documents"], homeDirectory: dir.path)
    }

    func testInsideRootAllowed() throws {
        XCTAssertEqual(try guardian.check("~/Documents/sub/file.txt"), root + "/sub/file.txt")
        XCTAssertEqual(try guardian.check(root + "/new/deeper/dir"), root + "/new/deeper/dir")
        XCTAssertEqual(try guardian.check(root + "/./sub"), root + "/sub")
    }

    func testTraversalRejected() {
        XCTAssertThrowsError(try guardian.check("~/Documents/../.ssh")) { error in
            XCTAssertEqual((error as? BridgeError)?.code, "path_traversal")
            XCTAssertEqual((error as? BridgeError)?.status, 403)
        }
        XCTAssertThrowsError(try guardian.check(root + "/sub/../../secret/x"))
        XCTAssertThrowsError(try guardian.check("~/Documents/sub/.."))
    }

    func testOutsideRootRejected() {
        XCTAssertThrowsError(try guardian.check(outside + "/x")) { error in
            XCTAssertEqual((error as? BridgeError)?.code, "path_not_allowed")
        }
        XCTAssertThrowsError(try guardian.check("/etc/passwd"))
        // Sibling directory sharing the root as string prefix.
        XCTAssertThrowsError(try guardian.check(root + "-evil/x"))
    }

    func testRootItselfOnlyWhenAllowed() throws {
        XCTAssertThrowsError(try guardian.check("~/Documents"))
        XCTAssertEqual(try guardian.check("~/Documents", allowRoot: true), root)
    }

    func testSymlinkEscapeRejected() throws {
        let link = root + "/escape"
        try FileManager.default.createSymbolicLink(atPath: link, withDestinationPath: outside)
        XCTAssertThrowsError(try guardian.check(link + "/x")) { error in
            XCTAssertEqual((error as? BridgeError)?.code, "path_not_allowed")
        }
        XCTAssertThrowsError(try guardian.check(link + "/new/dir"))
        // The link itself (last component) lives inside the root and may be operated on.
        XCTAssertEqual(try guardian.check(link), link)
    }

    func testSymlinkedRootIsResolved() throws {
        // A root that is itself a symlink resolves to its target; paths via either spelling work.
        let realRoot = dir.sub("RealDocs").path
        try FileManager.default.createDirectory(atPath: realRoot, withIntermediateDirectories: true)
        let linkRoot = dir.sub("LinkDocs").path
        try FileManager.default.createSymbolicLink(atPath: linkRoot, withDestinationPath: realRoot)
        let g = PathGuard(roots: [linkRoot], homeDirectory: dir.path)
        XCTAssertEqual(try g.check(linkRoot + "/a"), realRoot + "/a")
        XCTAssertEqual(try g.check(realRoot + "/a"), realRoot + "/a")
    }

    func testInvalidSyntax() {
        XCTAssertThrowsError(try guardian.check("relative/path"))
        XCTAssertThrowsError(try guardian.check(""))
        XCTAssertThrowsError(try guardian.check("~root/x"))
        XCTAssertThrowsError(try guardian.check("~/Documents/a\0b"))
    }
}

final class PolicyTests: XCTestCase {
    func testDefaultPolicyCreated() throws {
        let dir = try TempDir()
        let file = dir.sub("bridge-policy.json")
        let store = PolicyStore(fileURL: file)
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))
        let p = store.current()
        XCTAssertEqual(p, BridgePolicy.default)
        XCTAssertFalse(p.allowAppleScript)
        XCTAssertFalse(p.allows(action: BridgeAction.appleScript))
        XCTAssertTrue(p.allows(action: BridgeAction.inputType))
        XCTAssertTrue(p.allows(action: BridgeAction.health))
        XCTAssertTrue(p.isBlocked(bundleId: "com.apple.keychainaccess"))
        XCTAssertTrue(p.isBlocked(bundleId: "COM.APPLE.SYSTEMPREFERENCES"))
        XCTAssertFalse(p.isBlocked(bundleId: "com.apple.Safari"))
        XCTAssertFalse(p.isBlocked(bundleId: nil))
    }

    func testReloadOnChangeAndPartialKeys() throws {
        let dir = try TempDir()
        let file = dir.sub("bridge-policy.json")
        let store = PolicyStore(fileURL: file)
        try Data(#"{"allowAppleScript": true, "allowedActions": ["applescript"]}"#.utf8).write(to: file)
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(60)], ofItemAtPath: file.path)
        let p = store.current()
        XCTAssertTrue(p.allows(action: BridgeAction.appleScript))
        XCTAssertFalse(p.allows(action: BridgeAction.inputType))
        // Missing keys take defaults.
        XCTAssertEqual(p.allowedFileRoots, BridgePolicy.default.allowedFileRoots)
        XCTAssertEqual(p.blockedApps, BridgePolicy.default.blockedApps)
    }

    func testInvalidPolicyFailsClosed() throws {
        let dir = try TempDir()
        let file = dir.sub("bridge-policy.json")
        let store = PolicyStore(fileURL: file)
        try Data("{ not json".utf8).write(to: file)
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(60)], ofItemAtPath: file.path)
        let p = store.current()
        XCTAssertFalse(p.allows(action: BridgeAction.appsList))
        XCTAssertTrue(p.allows(action: BridgeAction.killSwitch))
        XCTAssertNotNil(store.loadError)
    }
}

final class KillSwitchTests: XCTestCase {
    func testFileAndApiEngagement() throws {
        let dir = try TempDir()
        let ks = KillSwitch(killFile: dir.sub("KILL"), stateFile: dir.sub("state"))
        XCTAssertFalse(ks.isEngaged)
        ks.setEngaged(true)
        XCTAssertTrue(ks.isEngaged)
        // Persisted across instances (restart).
        XCTAssertTrue(KillSwitch(killFile: dir.sub("KILL"), stateFile: dir.sub("state")).isEngaged)
        ks.setEngaged(false)
        XCTAssertFalse(ks.isEngaged)
        XCTAssertFalse(KillSwitch(killFile: dir.sub("KILL"), stateFile: dir.sub("state")).isEngaged)

        FileManager.default.createFile(atPath: dir.sub("KILL").path, contents: nil)
        XCTAssertTrue(ks.isEngaged)
        XCTAssertTrue(ks.fileEngaged)
        // The API cannot clear the file-based switch.
        ks.setEngaged(false)
        XCTAssertTrue(ks.isEngaged)
        try FileManager.default.removeItem(at: dir.sub("KILL"))
        XCTAssertFalse(ks.isEngaged)
    }
}

final class RateLimiterTests: XCTestCase {
    func testTokenBucket() {
        let clock = ManualClock()
        let bucket = TokenBucket(capacity: 20, refillPerSecond: 20, clock: { clock.now() })
        for _ in 0..<20 { XCTAssertTrue(bucket.tryConsume()) }
        XCTAssertFalse(bucket.tryConsume())
        clock.advance(0.05)  // one token
        XCTAssertTrue(bucket.tryConsume())
        XCTAssertFalse(bucket.tryConsume())
        clock.advance(10)  // refill capped at capacity
        for _ in 0..<20 { XCTAssertTrue(bucket.tryConsume()) }
        XCTAssertFalse(bucket.tryConsume())
    }
}
