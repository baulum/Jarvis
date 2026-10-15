import AppKit
import Foundation

/// Clipboard access via NSPasteboard. Contents are never logged.
enum ClipboardHandlers {
    static let maxWriteLength = 1_000_000

    static func read(_ ctx: RequestContext) async throws -> JSON {
        let (text, types) = await MainActor.run { () -> (String?, [String]) in
            let pb = NSPasteboard.general
            return (pb.string(forType: .string), (pb.types ?? []).map(\.rawValue))
        }
        return ["text": .optional(text), "hasText": .bool(text != nil), "types": .array(types.prefix(20).map { .string($0) })]
    }

    static func write(_ ctx: RequestContext) async throws -> JSON {
        let text = try ctx.string("text", maxLength: maxWriteLength, allowEmpty: true)
        let ok = await MainActor.run { () -> Bool in
            let pb = NSPasteboard.general
            pb.clearContents()
            return pb.setString(text, forType: .string)
        }
        guard ok else { throw BridgeError.unprocessable("clipboard_failed", "Could not write to the clipboard") }
        return ["ok": true, "length": .int(text.count)]
    }
}
