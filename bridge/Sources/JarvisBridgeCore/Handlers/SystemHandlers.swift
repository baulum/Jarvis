import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

enum SystemHandlers {
    static func permissionsJSON() -> JSON {
        ["accessibility": .bool(AXIsProcessTrusted()), "screenRecording": .bool(CGPreflightScreenCaptureAccess())]
    }

    static func health(_ ctx: RequestContext) async throws -> JSON {
        [
            "ok": true,
            "version": .string(ctx.env.version),
            "pid": .int(Int(getpid())),
            "killSwitch": .bool(ctx.env.killSwitch.isEngaged),
            "permissions": permissionsJSON(),
        ]
    }

    static func requestPermission(_ ctx: RequestContext) async throws -> JSON {
        let kind = try ctx.string("kind", maxLength: 32)
        switch kind {
        case "accessibility":
            _ = await MainActor.run { AXSupport.promptForTrust() }
        case "screenRecording":
            _ = await MainActor.run { CGRequestScreenCaptureAccess() }
        default:
            throw BridgeError.badRequest("invalid_param", "'kind' must be 'accessibility' or 'screenRecording'")
        }
        return ["ok": true, "kind": .string(kind), "permissions": permissionsJSON()]
    }

    static func killSwitch(_ ctx: RequestContext) async throws -> JSON {
        guard let engaged = try ctx.optionalBool("engaged") else {
            throw BridgeError.badRequest("missing_param", "'engaged' (boolean) is required")
        }
        ctx.env.killSwitch.setEngaged(engaged)
        BridgeLog.warn("Kill switch \(engaged ? "ENGAGED" : "released") via API")
        var out = ctx.env.killSwitch.statusJSON.objectValue ?? [:]
        out["ok"] = true
        if !engaged && ctx.env.killSwitch.fileEngaged {
            out["message"] = .string("KILL file present at \(ctx.env.paths.killFile.path); remove it to disengage")
        }
        return .object(out)
    }

    static func killSwitchStatus(_ ctx: RequestContext) async throws -> JSON {
        ctx.env.killSwitch.statusJSON
    }

    static func policy(_ ctx: RequestContext) async throws -> JSON {
        let p = ctx.env.policy.current()
        return [
            "policy": p.json,
            "file": .string(ctx.env.policy.fileURL.path),
            "error": .optional(ctx.env.policy.loadError),
        ]
    }
}
