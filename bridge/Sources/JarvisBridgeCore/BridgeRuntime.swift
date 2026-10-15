import Foundation

/// Wires together token, policy, kill switch, rate limiter, audit log, router and HTTP server.
public final class BridgeRuntime: Sendable {
    public static let version = "0.1.0"
    public static let defaultPort: UInt16 = 8766

    public let paths: BridgePaths
    public let port: UInt16
    public let pipeline: RequestPipeline
    public let audit: AuditLogger
    private let server: HTTPServer

    public init(paths: BridgePaths = .standard, port: UInt16 = BridgeRuntime.defaultPort) throws {
        self.paths = paths
        self.port = port
        try paths.ensureDirectories()

        let token = try TokenStore.loadOrCreate(at: paths.tokenFile)
        let policy = PolicyStore(fileURL: paths.policyFile)
        let killSwitch = KillSwitch(killFile: paths.killFile, stateFile: paths.killStateFile)
        let audit = AuditLogger(fileURL: paths.auditLog)
        let env = BridgeEnvironment(version: Self.version, paths: paths, policy: policy, killSwitch: killSwitch)

        let pipeline = RequestPipeline(
            router: Router(routes: BridgeRoutes.all()), env: env, token: token,
            rateLimiter: TokenBucket(capacity: 20, refillPerSecond: 20), audit: audit, port: port)
        self.pipeline = pipeline
        self.audit = audit

        var options = HTTPServer.Options()
        options.host = "127.0.0.1"
        options.port = port
        self.server = HTTPServer(options: options) { request in
            await pipeline.handle(request)
        }

        if killSwitch.isEngaged {
            BridgeLog.warn("Kill switch is ENGAGED at startup (api=\(killSwitch.apiEngaged), file=\(killSwitch.fileEngaged))")
        }
    }

    public func start(onState: @escaping @Sendable (Result<Void, Error>) -> Void) {
        server.start { result in
            if case .success = result {
                BridgeLog.info("JarvisBridge \(Self.version) listening on http://127.0.0.1:\(self.port) (pid \(getpid()))")
                self.audit.log(["ts": .string(Timestamp.now()), "action": "bridge.start", "result": "ok", "pid": .int(Int(getpid()))])
            }
            onState(result)
        }
    }

    public func stop() {
        server.stop()
        audit.log(["ts": .string(Timestamp.now()), "action": "bridge.stop", "result": "ok"])
        audit.flush()
    }
}
