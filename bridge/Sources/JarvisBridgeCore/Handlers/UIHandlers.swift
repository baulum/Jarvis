import ApplicationServices
import Foundation

enum UIHandlers {
    /// Resolves the target app and enforces the blockedApps policy.
    static func target(_ ctx: RequestContext) async throws -> AppInfo {
        let sel = try AppSelector(ctx)
        let app = try await MainActor.run { try Apps.resolve(sel) }
        if ctx.policy.isBlocked(bundleId: app.bundleId) {
            throw BridgeError.appBlocked(app.bundleId ?? "?")
        }
        return app
    }

    static func tree(_ ctx: RequestContext) async throws -> JSON {
        try AXSupport.requireTrusted()
        let maxDepth = min(max(try ctx.optionalInt("maxDepth") ?? 6, 0), 12)
        let maxNodes = min(max(try ctx.optionalInt("maxNodes") ?? 400, 1), 2000)
        let app = try await target(ctx)
        let pid = app.pid
        let result = try await runBlocking {
            AXSupport.tree(pid: pid, maxDepth: maxDepth, maxNodes: maxNodes)
        }
        // The response is the root node (the application element, id "0") with metadata keys added.
        var root = result.root.objectValue ?? [:]
        root["app"] = app.json
        root["nodeCount"] = .int(result.nodeCount)
        root["truncated"] = .bool(result.truncated)
        return .object(root)
    }

    /// Locates an element by `path` or by `role`/`title`; returns it plus its path.
    private static func locate(_ ctx: RequestContext, pid: Int32) async throws -> (UncheckedBox<AXUIElement>, String) {
        let path = try ctx.optionalString("path", maxLength: 512)
        let role = try ctx.optionalString("role", maxLength: 128)
        let title = try ctx.optionalString("title", maxLength: 512)
        if let path {
            let el = try await runBlocking { UncheckedBox(try AXSupport.element(pid: pid, path: path)) }
            return (el, path)
        }
        guard role != nil || title != nil else {
            throw BridgeError.badRequest("missing_param", "Provide 'path' or 'role'/'title'")
        }
        let found = try await runBlocking { UncheckedBox(AXSupport.find(pid: pid, role: role, title: title)) }
        guard let (el, p) = found.value else {
            throw BridgeError.notFound("element_not_found", "No element matches role=\(role ?? "*") title=\(title ?? "*")")
        }
        return (UncheckedBox(el), p)
    }

    static func press(_ ctx: RequestContext) async throws -> JSON {
        try AXSupport.requireTrusted()
        let app = try await target(ctx)
        let (box, path) = try await locate(ctx, pid: app.pid)
        let action = try ctx.optionalString("action", maxLength: 64) ?? (kAXPressAction as String)
        guard action.hasPrefix("AX") else { throw BridgeError.badRequest("invalid_param", "'action' must be an AX action name") }
        let result: (AXError, [String: JSON]) = try await runBlocking {
            let el = box.value
            let node = AXSupport.nodeJSON(el, id: path)
            let err = AXUIElementPerformAction(el, action as CFString)
            return (err, node)
        }
        guard result.0 == .success else {
            throw BridgeError.unprocessable(
                "ax_error", "AX action \(action) failed: \(AXSupport.errorName(result.0))",
                extra: ["element": .object(result.1)])
        }
        return ["ok": true, "path": .string(path), "action": .string(action), "element": .object(result.1)]
    }

    static func setValue(_ ctx: RequestContext) async throws -> JSON {
        try AXSupport.requireTrusted()
        let value = try ctx.string("value", maxLength: 100_000, allowEmpty: true)
        let app = try await target(ctx)
        let (box, path) = try await locate(ctx, pid: app.pid)
        let result: (AXError, Bool, String?) = try await runBlocking {
            let el = box.value
            var settable = DarwinBoolean(false)
            AXUIElementIsAttributeSettable(el, kAXValueAttribute as CFString, &settable)
            let role = AXSupport.string(el, kAXRoleAttribute)
            guard settable.boolValue else { return (.attributeUnsupported, false, role) }
            let err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, value as CFString)
            return (err, true, role)
        }
        guard result.1 else {
            throw BridgeError.unprocessable(
                "not_settable", "AXValue is not settable on this element", extra: ["role": .optional(result.2)])
        }
        guard result.0 == .success else {
            throw BridgeError.unprocessable("ax_error", "Setting AXValue failed: \(AXSupport.errorName(result.0))")
        }
        return ["ok": true, "path": .string(path), "role": .optional(result.2), "length": .int(value.count)]
    }
}
