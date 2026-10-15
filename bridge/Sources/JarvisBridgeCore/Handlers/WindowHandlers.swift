import AppKit
import CoreGraphics
import Foundation

struct WindowInfo: Sendable {
    var windowId: Int
    var ownerName: String?
    var ownerPid: Int32
    var title: String?
    var x: Double, y: Double, w: Double, h: Double
    var layer: Int
    var onScreen: Bool
    var alpha: Double

    var json: JSON {
        [
            "windowId": .int(windowId),
            "ownerName": .optional(ownerName),
            "ownerPid": .int(Int(ownerPid)),
            "title": .optional(title),
            "bounds": ["x": .double(x), "y": .double(y), "w": .double(w), "h": .double(h)],
            "layer": .int(layer),
            "onScreen": .bool(onScreen),
        ]
    }

    func contains(x px: Double, y py: Double) -> Bool {
        px >= x && px < x + w && py >= y && py < y + h
    }
}

enum Windows {
    /// Lists windows front-to-back using CGWindowListCopyWindowInfo.
    /// Note: window titles require the Screen Recording permission; without it `title` is null.
    static func list(onScreenOnly: Bool) -> [WindowInfo] {
        var options: CGWindowListOption = [.excludeDesktopElements]
        options.insert(onScreenOnly ? .optionOnScreenOnly : .optionAll)
        guard let raw = CGWindowListCopyWindowInfo(options, kCGNullWindowID) as? [[String: Any]] else { return [] }
        return raw.compactMap { d in
            guard let number = (d["kCGWindowNumber"] as? NSNumber)?.intValue,
                let pid = (d["kCGWindowOwnerPID"] as? NSNumber)?.int32Value
            else { return nil }
            let b = d["kCGWindowBounds"] as? [String: Any] ?? [:]
            func num(_ k: String) -> Double { (b[k] as? NSNumber)?.doubleValue ?? 0 }
            return WindowInfo(
                windowId: number,
                ownerName: d["kCGWindowOwnerName"] as? String,
                ownerPid: pid,
                title: d["kCGWindowName"] as? String,
                x: num("X"), y: num("Y"), w: num("Width"), h: num("Height"),
                layer: (d["kCGWindowLayer"] as? NSNumber)?.intValue ?? 0,
                onScreen: (d["kCGWindowIsOnscreen"] as? NSNumber)?.boolValue ?? false,
                alpha: (d["kCGWindowAlpha"] as? NSNumber)?.doubleValue ?? 1
            )
        }
    }

    /// The pid owning the top-most visible window at a global point (top-left origin coordinates).
    static func ownerPid(atX x: Double, y: Double) -> Int32? {
        list(onScreenOnly: true)
            .first { $0.alpha > 0 && $0.layer < 1000 && $0.contains(x: x, y: y) }?
            .ownerPid
    }
}

enum WindowHandlers {
    static func list(_ ctx: RequestContext) async throws -> JSON {
        let allLayers = ctx.queryBool("allLayers")
        let includeOffscreen = ctx.queryBool("includeOffscreen")
        let windows = try await runBlocking { Windows.list(onScreenOnly: !includeOffscreen) }
        let filtered = allLayers ? windows : windows.filter { $0.layer == 0 }
        return .array(filtered.map(\.json))
    }

    /// Frontmost app info (flattened) plus focused window/element via AX when trusted.
    static func frontmost(_ ctx: RequestContext) async throws -> JSON {
        guard let app = await MainActor.run(body: { Apps.frontmost() }) else {
            throw BridgeError.notFound("app_not_found", "No frontmost application")
        }
        var out = app.json.objectValue ?? [:]
        let trusted = AXSupport.isTrusted
        out["accessibility"] = .bool(trusted)
        out["focusedWindowTitle"] = .null
        out["focusedWindow"] = .null
        out["focusedElement"] = .null
        if trusted && !ctx.policy.isBlocked(bundleId: app.bundleId) {
            let pid = app.pid
            let ax = try await runBlocking { AXSupport.focusSummary(pid: pid) }
            out["focusedWindow"] = ax.window
            out["focusedWindowTitle"] = ax.window["title"] ?? .null
            out["focusedElement"] = ax.element
        }
        // Top-most layer-0 window of the app from the window server (titles need Screen Recording).
        let pid = app.pid
        let top = try await runBlocking { Windows.list(onScreenOnly: true).first { $0.ownerPid == pid && $0.layer == 0 } }
        out["frontWindow"] = top?.json ?? .null
        return .object(out)
    }
}
