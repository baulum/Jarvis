import AppKit
import Foundation

/// Sendable snapshot of a running application.
struct AppInfo: Sendable {
    var name: String?
    var bundleId: String?
    var pid: Int32
    var active: Bool
    var hidden: Bool
    var path: String?

    var json: JSON {
        [
            "name": .optional(name),
            "bundleId": .optional(bundleId),
            "pid": .int(Int(pid)),
            "active": .bool(active),
            "hidden": .bool(hidden),
            "path": .optional(path),
        ]
    }
}

/// Identifies a running app by bundle id, name or pid (in that order of precedence: pid, bundleId, name).
struct AppSelector: Sendable {
    var bundleId: String?
    var name: String?
    var pid: Int32?

    var isEmpty: Bool { bundleId == nil && name == nil && pid == nil }

    init(bundleId: String? = nil, name: String? = nil, pid: Int32? = nil) {
        self.bundleId = bundleId
        self.name = name
        self.pid = pid
    }

    init(_ ctx: RequestContext) throws {
        bundleId = try ctx.optionalString("bundleId", maxLength: 255)
        name = try ctx.optionalString("name", maxLength: 255)
        if let p = try ctx.optionalInt("pid") {
            guard p > 0, p <= Int(Int32.max) else { throw BridgeError.badRequest("invalid_param", "'pid' is invalid") }
            pid = Int32(p)
        }
    }

    var summary: String {
        if let pid { return "pid \(pid)" }
        if let bundleId { return bundleId }
        return name ?? "frontmost app"
    }
}

@MainActor
enum Apps {
    static func info(_ app: NSRunningApplication) -> AppInfo {
        AppInfo(
            name: app.localizedName, bundleId: app.bundleIdentifier, pid: app.processIdentifier,
            active: app.isActive, hidden: app.isHidden, path: app.bundleURL?.path)
    }

    static func regularApps() -> [AppInfo] {
        NSWorkspace.shared.runningApplications
            .filter { $0.activationPolicy == .regular && !$0.isTerminated }
            .map(info)
    }

    static func frontmost() -> AppInfo? {
        NSWorkspace.shared.frontmostApplication.map(info)
    }

    static func find(_ sel: AppSelector) -> NSRunningApplication? {
        let running = NSWorkspace.shared.runningApplications.filter { !$0.isTerminated }
        if let pid = sel.pid {
            return NSRunningApplication(processIdentifier: pid).flatMap { $0.isTerminated ? nil : $0 }
        }
        if let bid = sel.bundleId {
            let matches = running.filter { $0.bundleIdentifier?.caseInsensitiveCompare(bid) == .orderedSame }
            return matches.first(where: { $0.activationPolicy == .regular }) ?? matches.first
        }
        if let name = sel.name {
            let candidates = running.filter { $0.activationPolicy == .regular } + running
            if let exact = candidates.first(where: { $0.localizedName?.caseInsensitiveCompare(name) == .orderedSame }) {
                return exact
            }
            return candidates.first(where: {
                $0.bundleURL?.deletingPathExtension().lastPathComponent.caseInsensitiveCompare(name) == .orderedSame
            })
        }
        return nil
    }

    /// Resolves the selector (or the frontmost app if empty) to a running app.
    static func resolve(_ sel: AppSelector) throws -> AppInfo {
        if sel.isEmpty {
            guard let front = frontmost() else { throw BridgeError.notFound("app_not_found", "No frontmost application") }
            return front
        }
        guard let app = find(sel) else {
            throw BridgeError.notFound("app_not_found", "No running application matches \(sel.summary)")
        }
        return info(app)
    }
}

/// Locates application bundles on disk.
enum AppLocator {
    static func searchDirectories() -> [URL] {
        let home = FileManager.default.homeDirectoryForCurrentUser
        return [
            "/Applications", "/Applications/Utilities", "/System/Applications",
            "/System/Applications/Utilities", "/System/Library/CoreServices/Applications",
            "/System/Library/CoreServices",
        ].map { URL(fileURLWithPath: $0, isDirectory: true) }
            + [home.appendingPathComponent("Applications", isDirectory: true)]
    }

    static func url(forName name: String) -> URL? {
        let fm = FileManager.default
        let wanted = name.lowercased().hasSuffix(".app") ? String(name.dropLast(4)) : name
        guard !wanted.contains("/"), !wanted.isEmpty else { return nil }
        var subdirs: [URL] = []
        for dir in searchDirectories() {
            guard let items = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.isDirectoryKey]) else { continue }
            for item in items {
                if item.pathExtension.lowercased() == "app" {
                    if item.deletingPathExtension().lastPathComponent.caseInsensitiveCompare(wanted) == .orderedSame {
                        return item
                    }
                } else if (try? item.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true,
                    dir.path == "/Applications" || dir.path.hasSuffix("/Applications")
                {
                    subdirs.append(item)
                }
            }
        }
        // One level deeper (e.g. /Applications/Microsoft Office/...).
        for dir in subdirs {
            guard let items = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) else { continue }
            for item in items where item.pathExtension.lowercased() == "app" {
                if item.deletingPathExtension().lastPathComponent.caseInsensitiveCompare(wanted) == .orderedSame {
                    return item
                }
            }
        }
        // Fall back to matching the localized display name via Launch Services metadata.
        for dir in searchDirectories() {
            guard let items = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) else { continue }
            for item in items where item.pathExtension.lowercased() == "app" {
                if fm.displayName(atPath: item.path).replacingOccurrences(of: ".app", with: "")
                    .caseInsensitiveCompare(wanted) == .orderedSame
                {
                    return item
                }
            }
        }
        return nil
    }
}

enum AppHandlers {
    static func list(_ ctx: RequestContext) async throws -> JSON {
        let apps = await MainActor.run { Apps.regularApps() }
        return .array(apps.map(\.json))
    }

    static func launch(_ ctx: RequestContext) async throws -> JSON {
        let bundleId = try ctx.optionalString("bundleId", maxLength: 255)
        let name = try ctx.optionalString("name", maxLength: 255)
        guard bundleId != nil || name != nil else {
            throw BridgeError.badRequest("missing_param", "Provide 'bundleId' or 'name'")
        }
        let appURL: URL? = await MainActor.run {
            if let bundleId {
                return NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleId)
            }
            return nil
        }
        let resolved: URL
        if let appURL {
            resolved = appURL
        } else if let name, let u = try await runBlocking({ AppLocator.url(forName: name) }) {
            resolved = u
        } else {
            throw BridgeError.notFound("app_not_found", "No application found for \(bundleId ?? name ?? "?")")
        }

        let activates = try ctx.optionalBool("activate") ?? true
        let pid = try await openApplication(at: resolved, activates: activates)
        let info = await MainActor.run { NSRunningApplication(processIdentifier: pid).map(Apps.info) }
        var out: [String: JSON] = ["ok": true, "launched": .string(resolved.path)]
        if let info { out["app"] = info.json }
        return .object(out)
    }

    /// Opens (or re-activates) an application via Launch Services. Returns its pid.
    static func openApplication(at url: URL, activates: Bool) async throws -> Int32 {
        let config = NSWorkspace.OpenConfiguration()
        config.activates = activates
        config.addsToRecentItems = false
        let pid: Int32 = try await withCheckedThrowingContinuation { cont in
            NSWorkspace.shared.openApplication(at: url, configuration: config) { app, error in
                if let app {
                    cont.resume(returning: app.processIdentifier)
                } else {
                    cont.resume(
                        throwing: BridgeError.unprocessable(
                            "launch_failed", error?.localizedDescription ?? "Could not launch application"))
                }
            }
        }
        return pid
    }

    static func activate(_ ctx: RequestContext) async throws -> JSON {
        let sel = try AppSelector(ctx)
        guard !sel.isEmpty else { throw BridgeError.badRequest("missing_param", "Provide 'bundleId', 'name' or 'pid'") }
        let (info, bundleURL): (AppInfo, URL?) = try await MainActor.run {
            guard let app = Apps.find(sel) else {
                throw BridgeError.notFound("app_not_found", "No running application matches \(sel.summary)")
            }
            if app.isHidden { app.unhide() }
            app.activate(options: [.activateAllWindows])
            return (Apps.info(app), app.bundleURL)
        }
        // macOS 14+ may ignore activation requests from background apps; fall back to Launch Services.
        try await Task.sleep(nanoseconds: 300_000_000)
        var frontPid = await MainActor.run { NSWorkspace.shared.frontmostApplication?.processIdentifier }
        if frontPid != info.pid, let bundleURL {
            _ = try? await openApplication(at: bundleURL, activates: true)
            try await Task.sleep(nanoseconds: 300_000_000)
            frontPid = await MainActor.run { NSWorkspace.shared.frontmostApplication?.processIdentifier }
        }
        return ["ok": true, "activated": .bool(frontPid == info.pid), "app": info.json]
    }

    static func quit(_ ctx: RequestContext) async throws -> JSON {
        let sel = try AppSelector(ctx)
        guard !sel.isEmpty else { throw BridgeError.badRequest("missing_param", "Provide 'bundleId', 'name' or 'pid'") }
        return try await MainActor.run {
            guard let app = Apps.find(sel) else {
                throw BridgeError.notFound("app_not_found", "No running application matches \(sel.summary)")
            }
            if app.processIdentifier == getpid() {
                throw BridgeError.badRequest("invalid_target", "The bridge cannot quit itself")
            }
            let info = Apps.info(app)
            let requested = app.terminate()  // graceful; the app may ask to save documents
            return ["ok": .bool(requested), "terminateRequested": .bool(requested), "app": info.json]
        }
    }
}
