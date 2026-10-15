import Foundation

/// An error that maps to a JSON error response `{"error": code, "message": ..., ...extra}`.
public struct BridgeError: Error, Sendable, CustomStringConvertible {
    public var status: Int
    public var code: String
    public var message: String
    public var extra: [String: JSON]

    public init(status: Int, code: String, message: String, extra: [String: JSON] = [:]) {
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra
    }

    public var description: String { "\(status) \(code): \(message)" }

    public var response: HTTPResponse { .error(status, code, message, extra: extra) }

    public static func badRequest(_ code: String = "bad_request", _ message: String) -> BridgeError {
        BridgeError(status: 400, code: code, message: message)
    }

    public static func notFound(_ code: String = "not_found", _ message: String) -> BridgeError {
        BridgeError(status: 404, code: code, message: message)
    }

    public static func forbidden(_ code: String, _ message: String, extra: [String: JSON] = [:]) -> BridgeError {
        BridgeError(status: 403, code: code, message: message, extra: extra)
    }

    public static func unprocessable(_ code: String, _ message: String, extra: [String: JSON] = [:]) -> BridgeError {
        BridgeError(status: 422, code: code, message: message, extra: extra)
    }

    public static func internalError(_ message: String) -> BridgeError {
        BridgeError(status: 500, code: "internal_error", message: message)
    }

    public static func permissionDenied(_ permission: String) -> BridgeError {
        let hint: String
        switch permission {
        case "accessibility":
            hint = "Grant Accessibility to JarvisBridge.app in System Settings > Privacy & Security > Accessibility"
        case "screenRecording":
            hint = "Grant Screen Recording to JarvisBridge.app in System Settings > Privacy & Security > Screen & System Audio Recording"
        default:
            hint = "Grant the \(permission) permission to JarvisBridge.app"
        }
        return BridgeError(status: 403, code: "permission_denied", message: hint, extra: ["permission": .string(permission)])
    }

    public static func appBlocked(_ bundleId: String) -> BridgeError {
        BridgeError(
            status: 403, code: "app_blocked",
            message: "The target app is blocked by policy", extra: ["bundleId": .string(bundleId)])
    }
}

/// Access class of a route; drives authentication, kill switch and rate limiting.
public enum RouteAccess: Sendable {
    /// No authentication (GET /health only).
    case open
    /// Authenticated, read-only status; works while the kill switch is engaged; not rate limited.
    case status
    /// Authenticated read of user content (screen, clipboard, UI tree, files);
    /// blocked by the kill switch and rate limited.
    case sensitiveRead
    /// Authenticated action that changes state; blocked by the kill switch and rate limited.
    case act
}

/// Everything a handler needs.
public struct RequestContext: Sendable {
    public let request: HTTPRequest
    /// The JSON body (always an object; `{}` for empty bodies and GET requests).
    public let body: JSON
    public let env: BridgeEnvironment

    public init(request: HTTPRequest, body: JSON, env: BridgeEnvironment) {
        self.request = request
        self.body = body
        self.env = env
    }

    public var policy: BridgePolicy { env.policy.current() }

    // MARK: Parameter helpers

    public func optionalString(_ key: String, maxLength: Int = 10_000) throws -> String? {
        guard let v = body[key], !v.isNull else { return nil }
        guard let s = v.stringValue else { throw BridgeError.badRequest("invalid_param", "'\(key)' must be a string") }
        guard s.count <= maxLength else {
            throw BridgeError.badRequest("invalid_param", "'\(key)' exceeds \(maxLength) characters")
        }
        return s
    }

    public func string(_ key: String, maxLength: Int = 10_000, allowEmpty: Bool = false) throws -> String {
        guard let s = try optionalString(key, maxLength: maxLength) else {
            throw BridgeError.badRequest("missing_param", "'\(key)' is required")
        }
        if !allowEmpty && s.isEmpty { throw BridgeError.badRequest("invalid_param", "'\(key)' must not be empty") }
        return s
    }

    public func optionalInt(_ key: String) throws -> Int? {
        guard let v = body[key], !v.isNull else { return nil }
        guard let i = v.intValue else { throw BridgeError.badRequest("invalid_param", "'\(key)' must be an integer") }
        return i
    }

    public func optionalDouble(_ key: String) throws -> Double? {
        guard let v = body[key], !v.isNull else { return nil }
        guard let d = v.doubleValue, d.isFinite else {
            throw BridgeError.badRequest("invalid_param", "'\(key)' must be a number")
        }
        return d
    }

    public func optionalBool(_ key: String) throws -> Bool? {
        guard let v = body[key], !v.isNull else { return nil }
        guard let b = v.boolValue else { throw BridgeError.badRequest("invalid_param", "'\(key)' must be a boolean") }
        return b
    }

    public func queryBool(_ key: String) -> Bool {
        guard let v = request.query[key]?.lowercased() else { return false }
        return v == "1" || v == "true" || v == "yes"
    }
}

/// Shared services available to handlers.
public struct BridgeEnvironment: Sendable {
    public let version: String
    public let paths: BridgePaths
    public let policy: PolicyStore
    public let killSwitch: KillSwitch

    public init(version: String, paths: BridgePaths, policy: PolicyStore, killSwitch: KillSwitch) {
        self.version = version
        self.paths = paths
        self.policy = policy
        self.killSwitch = killSwitch
    }
}

public typealias RouteHandler = @Sendable (RequestContext) async throws -> JSON

public struct Route: Sendable {
    public let method: String
    public let path: String
    public let action: String
    public let access: RouteAccess
    public let handler: RouteHandler

    public init(_ method: String, _ path: String, action: String, access: RouteAccess, handler: @escaping RouteHandler) {
        self.method = method
        self.path = path
        self.action = action
        self.access = access
        self.handler = handler
    }
}

public enum RouteMatch: Sendable {
    case found(Route)
    case methodNotAllowed(allowed: [String])
    case notFound
}

public struct Router: Sendable {
    private var table: [String: [String: Route]] = [:]

    public init(routes: [Route] = []) {
        for r in routes { add(r) }
    }

    public mutating func add(_ route: Route) {
        table[route.path, default: [:]][route.method] = route
    }

    public var routes: [Route] { table.values.flatMap { $0.values } }

    public static func normalize(_ path: String) -> String {
        var p = path
        while p.count > 1 && p.hasSuffix("/") { p.removeLast() }
        return p
    }

    public func match(method: String, path: String) -> RouteMatch {
        guard let byMethod = table[Self.normalize(path)] else { return .notFound }
        if let route = byMethod[method] { return .found(route) }
        return .methodNotAllowed(allowed: byMethod.keys.sorted())
    }
}
