import Foundation

/// Applies the security pipeline to every request and dispatches to route handlers:
///
/// 1. Host / Origin checks (DNS-rebinding and browser CSRF protection)
/// 2. Route lookup (404 / 405)
/// 3. Bearer token authentication (all routes except `.open`)
/// 4. Kill switch (423 for `.act` and `.sensitiveRead`)
/// 5. Policy `allowedActions` (403)
/// 6. Rate limit (429 for `.act` and `.sensitiveRead`)
/// 7. JSON body parsing, handler invocation, audit logging
public final class RequestPipeline: Sendable {
    public let router: Router
    public let env: BridgeEnvironment
    private let token: String
    private let rateLimiter: TokenBucket
    private let audit: AuditLogger?
    private let allowedHosts: Set<String>

    public init(
        router: Router, env: BridgeEnvironment, token: String, rateLimiter: TokenBucket,
        audit: AuditLogger?, port: UInt16 = 8766
    ) {
        self.router = router
        self.env = env
        self.token = token
        self.rateLimiter = rateLimiter
        self.audit = audit
        self.allowedHosts = [
            "127.0.0.1", "localhost", "[::1]",
            "127.0.0.1:\(port)", "localhost:\(port)", "[::1]:\(port)",
        ]
    }

    public func handle(_ request: HTTPRequest) async -> HTTPResponse {
        let started = Date()

        // 1. Host / Origin
        if let host = request.header("host"), !allowedHosts.contains(host.lowercased()) {
            return .error(421, "invalid_host", "Host header must refer to the loopback interface")
        }
        if request.header("origin") != nil {
            return .error(403, "origin_not_allowed", "Browser-originated requests are not accepted")
        }

        // 2. Routing
        let route: Route
        switch router.match(method: request.method, path: request.path) {
        case .notFound:
            return .error(404, "not_found", "No such endpoint: \(request.path)")
        case .methodNotAllowed(let allowed):
            var r = HTTPResponse.error(405, "method_not_allowed", "Use \(allowed.joined(separator: ", "))")
            r.headers["Allow"] = allowed.joined(separator: ", ")
            return r
        case .found(let r):
            route = r
        }

        // 3. Authentication
        if route.access != .open {
            guard Auth.isAuthorized(header: request.header("authorization"), expected: token) else {
                record(route, request: request, params: nil, status: 401, code: "unauthorized", started: started)
                var r = HTTPResponse.error(401, "unauthorized", "Missing or invalid bearer token")
                r.headers["WWW-Authenticate"] = "Bearer realm=\"jarvis-bridge\""
                return r
            }
        }

        let isGuarded = route.access == .act || route.access == .sensitiveRead

        // 4. Kill switch (checked on every request, including the KILL file)
        if isGuarded && env.killSwitch.isEngaged {
            record(route, request: request, params: nil, status: 423, code: "kill_switch_engaged", started: started)
            return .error(423, "kill_switch_engaged", "The kill switch is engaged; acting endpoints are disabled")
        }

        // 5. Policy
        let policy = env.policy.current()
        if !policy.allows(action: route.action) {
            let code = route.action == BridgeAction.appleScript ? "applescript_disabled" : "action_not_allowed"
            let message = route.action == BridgeAction.appleScript
                ? "AppleScript is disabled by policy (set allowAppleScript: true in bridge-policy.json)"
                : "Action '\(route.action)' is not allowed by policy"
            record(route, request: request, params: nil, status: 403, code: code, started: started)
            return .error(403, code, message, extra: ["action": .string(route.action)])
        }

        // 6. Rate limit
        if isGuarded && !rateLimiter.tryConsume() {
            record(route, request: request, params: nil, status: 429, code: "rate_limited", started: started)
            var r = HTTPResponse.error(429, "rate_limited", "Too many requests")
            r.headers["Retry-After"] = "1"
            return r
        }

        // 7. Body
        var body: JSON = .object([:])
        if !request.body.isEmpty {
            do {
                body = try JSON.parse(request.body)
            } catch {
                record(route, request: request, params: nil, status: 400, code: "invalid_json", started: started)
                return .error(400, "invalid_json", "Request body must be a JSON object")
            }
            guard case .object = body else {
                record(route, request: request, params: nil, status: 400, code: "invalid_json", started: started)
                return .error(400, "invalid_json", "Request body must be a JSON object")
            }
        }

        let params = AuditLogger.summarize(body)
        let ctx = RequestContext(request: request, body: body, env: env)
        do {
            let result = try await route.handler(ctx)
            record(route, request: request, params: params, status: 200, code: nil, started: started)
            return .json(200, result)
        } catch let e as BridgeError {
            record(route, request: request, params: params, status: e.status, code: e.code, started: started)
            return e.response
        } catch {
            BridgeLog.error("Unhandled error in \(route.action): \(error)")
            record(route, request: request, params: params, status: 500, code: "internal_error", started: started)
            return .error(500, "internal_error", "Unexpected error: \(error.localizedDescription)")
        }
    }

    private func record(
        _ route: Route, request: HTTPRequest, params: JSON?, status: Int, code: String?, started: Date
    ) {
        // Health checks are polled frequently and carry no parameters; keep the audit log focused.
        guard let audit, route.access != .open else { return }
        var entry: [String: JSON] = [
            "ts": .string(Timestamp.now()),
            "action": .string(route.action),
            "method": .string(request.method),
            "path": .string(request.path),
            "result": .string(status < 400 ? "ok" : "error"),
            "status": .int(status),
            "durationMs": .int(Int(Date().timeIntervalSince(started) * 1000)),
        ]
        if let params, case .object(let o) = params, !o.isEmpty { entry["params"] = params }
        if let code { entry["error"] = .string(code) }
        audit.log(.object(entry))
    }
}
