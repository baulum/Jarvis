import ApplicationServices
import Foundation

/// Thin helpers around the Accessibility (AXUIElement) API.
/// All functions are synchronous and must be called off the main thread via `runBlocking`.
enum AXSupport {
    static var isTrusted: Bool { AXIsProcessTrusted() }

    static func requireTrusted() throws {
        guard AXIsProcessTrusted() else { throw BridgeError.permissionDenied("accessibility") }
    }

    /// Shows the system prompt (only if not yet trusted). Returns current trust state.
    static func promptForTrust() -> Bool {
        // "AXTrustedCheckOptionPrompt" == kAXTrustedCheckOptionPrompt (avoids a non-Sendable global).
        let options = ["AXTrustedCheckOptionPrompt": true] as CFDictionary
        return AXIsProcessTrustedWithOptions(options)
    }

    static func appElement(pid: Int32) -> AXUIElement {
        let el = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(el, 2.0)
        return el
    }

    // MARK: Attribute access

    static func copy(_ el: AXUIElement, _ attr: String) -> CFTypeRef? {
        var value: CFTypeRef?
        let err = AXUIElementCopyAttributeValue(el, attr as CFString, &value)
        return err == .success ? value : nil
    }

    static func string(_ el: AXUIElement, _ attr: String) -> String? {
        copy(el, attr).flatMap(stringify)
    }

    static func asElement(_ v: CFTypeRef?) -> AXUIElement? {
        guard let v, CFGetTypeID(v) == AXUIElementGetTypeID() else { return nil }
        return unsafeDowncast(v, to: AXUIElement.self)
    }

    static func children(_ el: AXUIElement, limit: Int = 1000) -> [AXUIElement] {
        var value: CFArray?
        let err = AXUIElementCopyAttributeValues(el, kAXChildrenAttribute as CFString, 0, limit, &value)
        guard err == .success, let arr = value as? [AnyObject] else { return [] }
        return arr.compactMap { asElement($0) }
    }

    static func actions(_ el: AXUIElement) -> [String] {
        var names: CFArray?
        guard AXUIElementCopyActionNames(el, &names) == .success, let list = names as? [String] else { return [] }
        return list
    }

    static func isAXError(_ v: AnyObject) -> Bool {
        guard CFGetTypeID(v) == AXValueGetTypeID() else { return false }
        return AXValueGetType(unsafeDowncast(v, to: AXValue.self)) == .axError
    }

    /// Converts simple AX values (strings, numbers, booleans, attributed strings) to a string.
    static func stringify(_ v: CFTypeRef) -> String? {
        let tid = CFGetTypeID(v)
        if tid == CFStringGetTypeID() { return (v as! CFString) as String }
        if tid == CFBooleanGetTypeID() { return CFBooleanGetValue((v as! CFBoolean)) ? "true" : "false" }
        if tid == CFNumberGetTypeID() { return (v as! NSNumber).stringValue }
        if tid == CFAttributedStringGetTypeID() { return (v as! NSAttributedString).string }
        if tid == CFURLGetTypeID() { return (v as! NSURL).absoluteString }
        return nil
    }

    static func point(_ v: CFTypeRef?) -> CGPoint? {
        guard let v, CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        var p = CGPoint.zero
        return AXValueGetValue(unsafeDowncast(v, to: AXValue.self), .cgPoint, &p) ? p : nil
    }

    static func size(_ v: CFTypeRef?) -> CGSize? {
        guard let v, CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        var s = CGSize.zero
        return AXValueGetValue(unsafeDowncast(v, to: AXValue.self), .cgSize, &s) ? s : nil
    }

    static func truncate(_ s: String?, _ n: Int = 200) -> String? {
        guard let s else { return nil }
        return s.count > n ? String(s.prefix(n)) + "…" : s
    }

    static func errorName(_ e: AXError) -> String {
        switch e {
        case .success: return "success"
        case .failure: return "failure"
        case .illegalArgument: return "illegalArgument"
        case .invalidUIElement: return "invalidUIElement"
        case .invalidUIElementObserver: return "invalidUIElementObserver"
        case .cannotComplete: return "cannotComplete"
        case .attributeUnsupported: return "attributeUnsupported"
        case .actionUnsupported: return "actionUnsupported"
        case .notificationUnsupported: return "notificationUnsupported"
        case .notImplemented: return "notImplemented"
        case .notificationAlreadyRegistered: return "notificationAlreadyRegistered"
        case .notificationNotRegistered: return "notificationNotRegistered"
        case .apiDisabled: return "apiDisabled"
        case .noValue: return "noValue"
        case .parameterizedAttributeUnsupported: return "parameterizedAttributeUnsupported"
        case .notEnoughPrecision: return "notEnoughPrecision"
        @unknown default: return "axError(\(e.rawValue))"
        }
    }

    // MARK: Node description

    struct NodeAttributes {
        var role: String?
        var subrole: String?
        var title: String?
        var value: String?
        var description: String?
        var identifier: String?
        var enabled: Bool?
        var focused: Bool?
        var frame: CGRect?
    }

    private static let nodeAttributeNames: [String] = [
        kAXRoleAttribute, kAXSubroleAttribute, kAXTitleAttribute, kAXValueAttribute,
        kAXDescriptionAttribute, kAXIdentifierAttribute, kAXEnabledAttribute, kAXFocusedAttribute,
        kAXPositionAttribute, kAXSizeAttribute,
    ]

    static func attributes(_ el: AXUIElement) -> NodeAttributes {
        var values: CFArray?
        let err = AXUIElementCopyMultipleAttributeValues(
            el, nodeAttributeNames as CFArray, AXCopyMultipleAttributeOptions(rawValue: 0), &values)
        var a = NodeAttributes()
        guard err == .success, let arr = values as? [AnyObject], arr.count == nodeAttributeNames.count else {
            return a
        }
        func val(_ i: Int) -> CFTypeRef? { isAXError(arr[i]) ? nil : arr[i] }
        a.role = val(0).flatMap(stringify)
        a.subrole = val(1).flatMap(stringify)
        a.title = val(2).flatMap(stringify)
        a.value = val(3).flatMap(stringify)
        a.description = val(4).flatMap(stringify)
        a.identifier = val(5).flatMap(stringify)
        if let e = val(6), CFGetTypeID(e) == CFBooleanGetTypeID() { a.enabled = CFBooleanGetValue((e as! CFBoolean)) }
        if let f = val(7), CFGetTypeID(f) == CFBooleanGetTypeID() { a.focused = CFBooleanGetValue((f as! CFBoolean)) }
        if let p = point(val(8)), let s = size(val(9)) { a.frame = CGRect(origin: p, size: s) }
        return a
    }

    static func nodeJSON(_ el: AXUIElement, id: String, includeActions: Bool = true) -> [String: JSON] {
        let a = attributes(el)
        var node: [String: JSON] = ["id": .string(id)]
        if let v = a.role { node["role"] = .string(v) }
        if let v = a.subrole { node["subrole"] = .string(v) }
        if let v = truncate(a.title) { node["title"] = .string(v) }
        if let v = truncate(a.value) { node["value"] = .string(v) }
        if let v = truncate(a.description) { node["description"] = .string(v) }
        if let v = truncate(a.identifier, 100) { node["identifier"] = .string(v) }
        if let v = a.enabled { node["enabled"] = .bool(v) }
        if a.focused == true { node["focused"] = true }
        if let f = a.frame {
            node["frame"] = [
                "x": .double(Double(f.origin.x)), "y": .double(Double(f.origin.y)),
                "w": .double(Double(f.size.width)), "h": .double(Double(f.size.height)),
            ]
        }
        if includeActions {
            let acts = actions(el)
            if !acts.isEmpty { node["actions"] = .array(acts.map { .string($0) }) }
        }
        return node
    }

    // MARK: Tree

    struct TreeResult: Sendable {
        var root: JSON
        var nodeCount: Int
        var truncated: Bool
    }

    /// Builds a nested tree. The application element has id "0"; children append their index.
    static func tree(pid: Int32, maxDepth: Int, maxNodes: Int) -> TreeResult {
        let root = appElement(pid: pid)
        var count = 0
        var truncated = false

        func build(_ el: AXUIElement, id: String, depth: Int) -> JSON {
            count += 1
            var node = nodeJSON(el, id: id)
            let kids = children(el)
            if kids.isEmpty { return .object(node) }
            if depth >= maxDepth {
                node["childCount"] = .int(kids.count)
                truncated = true
                return .object(node)
            }
            var out: [JSON] = []
            for (i, child) in kids.enumerated() {
                if count >= maxNodes {
                    truncated = true
                    node["childCount"] = .int(kids.count)
                    break
                }
                out.append(build(child, id: "\(id).\(i)", depth: depth + 1))
            }
            node["children"] = .array(out)
            return .object(node)
        }

        let tree = build(root, id: "0", depth: 0)
        return TreeResult(root: tree, nodeCount: count, truncated: truncated)
    }

    // MARK: Element lookup

    /// Resolves a path like "0.3.1" starting at the application element.
    static func element(pid: Int32, path: String) throws -> AXUIElement {
        let parts = path.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.first == "0", parts.count <= 64 else {
            throw BridgeError.badRequest("invalid_path", "Element path must start with '0' (the application element)")
        }
        var el = appElement(pid: pid)
        for part in parts.dropFirst() {
            guard let idx = Int(part), idx >= 0 else {
                throw BridgeError.badRequest("invalid_path", "Element path components must be non-negative integers")
            }
            let kids = children(el)
            guard idx < kids.count else {
                throw BridgeError.notFound("element_not_found", "No element at path \(path) (UI may have changed)")
            }
            el = kids[idx]
        }
        return el
    }

    /// Breadth-first search for the first element matching role and/or title
    /// (title is compared case-insensitively against AXTitle, AXDescription and AXValue).
    static func find(pid: Int32, role: String?, title: String?, maxNodes: Int = 5000, maxDepth: Int = 25)
        -> (AXUIElement, String)?
    {
        var queue: [(AXUIElement, String, Int)] = [(appElement(pid: pid), "0", 0)]
        var head = 0
        var visited = 0
        while head < queue.count, visited < maxNodes {
            let (el, path, depth) = queue[head]
            head += 1
            visited += 1
            let a = attributes(el)
            var ok = true
            if let role, a.role?.caseInsensitiveCompare(role) != .orderedSame { ok = false }
            if ok, let title {
                let candidates = [a.title, a.description, a.value].compactMap { $0 }
                ok = candidates.contains { $0.caseInsensitiveCompare(title) == .orderedSame }
            }
            if ok { return (el, path) }
            if depth < maxDepth {
                for (i, child) in children(el).enumerated() {
                    queue.append((child, "\(path).\(i)", depth + 1))
                }
            }
        }
        return nil
    }

    // MARK: Focus

    struct FocusSummary: Sendable {
        var window: JSON
        var element: JSON
    }

    static func focusSummary(pid: Int32) -> FocusSummary {
        let app = appElement(pid: pid)
        var window: JSON = .null
        if let w = asElement(copy(app, kAXFocusedWindowAttribute)) {
            let a = attributes(w)
            var obj: [String: JSON] = ["title": .optional(truncate(a.title))]
            if let f = a.frame {
                obj["frame"] = [
                    "x": .double(Double(f.origin.x)), "y": .double(Double(f.origin.y)),
                    "w": .double(Double(f.size.width)), "h": .double(Double(f.size.height)),
                ]
            }
            window = .object(obj)
        }
        var element: JSON = .null
        if let e = asElement(copy(app, kAXFocusedUIElementAttribute)) {
            let a = attributes(e)
            element = [
                "role": .optional(a.role),
                "subrole": .optional(a.subrole),
                "title": .optional(truncate(a.title)),
                "description": .optional(truncate(a.description)),
            ]
        }
        return FocusSummary(window: window, element: element)
    }
}
