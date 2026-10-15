import Foundation
import XCTest
import os

@testable import JarvisBridgeCore

/// Creates an isolated temp directory (resolved through /private) and removes it on deinit.
final class TempDir {
    let url: URL

    init() throws {
        let base = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("jarvis-bridge-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        url = URL(fileURLWithPath: PathGuard.realPath(base.path)!, isDirectory: true)
    }

    var path: String { url.path }

    func sub(_ name: String) -> URL { url.appendingPathComponent(name) }

    deinit { try? FileManager.default.removeItem(at: url) }
}

/// A manually advanced clock for rate limiter tests.
final class ManualClock: Sendable {
    private let t = OSAllocatedUnfairLock(initialState: 1000.0)
    func now() -> TimeInterval { t.withLock { $0 } }
    func advance(_ dt: TimeInterval) { t.withLock { $0 += dt } }
}

/// Builds a pipeline with fake routes in an isolated directory.
struct PipelineFixture {
    let dir: TempDir
    let paths: BridgePaths
    let env: BridgeEnvironment
    let pipeline: RequestPipeline
    let audit: AuditLogger
    let token = "a3f1c0ffee0123456789abcdef0123456789abcdef0123456789abcdef012345"

    init(routes: [Route]? = nil, rateLimiter: TokenBucket = TokenBucket(capacity: 1000, refillPerSecond: 1000)) throws {
        dir = try TempDir()
        paths = BridgePaths(supportDirectory: dir.sub("support"), logDirectory: dir.sub("logs"))
        try paths.ensureDirectories()
        let policy = PolicyStore(fileURL: paths.policyFile)
        let ks = KillSwitch(killFile: paths.killFile, stateFile: paths.killStateFile)
        env = BridgeEnvironment(version: "test", paths: paths, policy: policy, killSwitch: ks)
        audit = AuditLogger(fileURL: paths.auditLog)
        let defaultRoutes: [Route] = [
            Route("GET", "/health", action: BridgeAction.health, access: .open) { _ in ["ok": true] },
            Route("GET", "/apps", action: BridgeAction.appsList, access: .status) { _ in ["apps": []] },
            Route("POST", "/killswitch", action: BridgeAction.killSwitch, access: .status, handler: SystemHandlers.killSwitch),
            Route("POST", "/apps/activate", action: BridgeAction.appsActivate, access: .act) { ctx in
                ["echo": ctx.body]
            },
            Route("POST", "/input/type", action: BridgeAction.inputType, access: .act) { ctx in
                ["length": .int(ctx.body["text"]?.stringValue?.count ?? 0)]
            },
            Route("GET", "/clipboard", action: BridgeAction.clipboardRead, access: .sensitiveRead) { _ in ["text": "secret"] },
            Route("POST", "/applescript", action: BridgeAction.appleScript, access: .act) { _ in ["ok": true] },
            Route("POST", "/files/mkdir", action: BridgeAction.filesMkdir, access: .act, handler: FileHandlers.mkdir),
            Route("POST", "/boom", action: BridgeAction.openURL, access: .act) { _ in
                throw BridgeError.unprocessable("boom", "exploded")
            },
        ]
        pipeline = RequestPipeline(
            router: Router(routes: routes ?? defaultRoutes), env: env, token: token,
            rateLimiter: rateLimiter, audit: audit)
    }

    func request(
        _ method: String, _ path: String, body: String? = nil, auth: Bool = true,
        headers: [String: String] = ["Host": "127.0.0.1:8766"]
    ) async -> HTTPResponse {
        var h = headers
        if auth { h["Authorization"] = "Bearer \(token)" }
        let req = HTTPRequest(method: method, path: path, headers: h, body: Data((body ?? "").utf8))
        return await pipeline.handle(req)
    }

    func writePolicy(_ json: String) throws {
        // Ensure a different mtime/size is observed.
        try Data(json.utf8).write(to: paths.policyFile)
        let future = Date().addingTimeInterval(Double.random(in: 1...1000))
        try FileManager.default.setAttributes([.modificationDate: future], ofItemAtPath: paths.policyFile.path)
    }
}
