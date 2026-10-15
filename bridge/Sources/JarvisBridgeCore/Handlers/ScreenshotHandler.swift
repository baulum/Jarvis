import AppKit
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

enum ScreenshotHandler {
    static func capture(_ ctx: RequestContext) async throws -> JSON {
        // Never trigger the TCC prompt implicitly; use POST /permissions/request for that.
        guard CGPreflightScreenCaptureAccess() else { throw BridgeError.permissionDenied("screenRecording") }

        let displayId = try ctx.optionalInt("displayId")
        let windowId = try ctx.optionalInt("windowId")
        let maxWidth = min(max(try ctx.optionalInt("maxWidth") ?? 1600, 64), 8192)
        let policy = ctx.policy

        let content: SCShareableContent
        do {
            content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: windowId == nil)
        } catch {
            if !CGPreflightScreenCaptureAccess() { throw BridgeError.permissionDenied("screenRecording") }
            throw BridgeError.unprocessable("capture_failed", "Could not enumerate shareable content: \(error.localizedDescription)")
        }

        let filter: SCContentFilter
        var meta: [String: JSON] = [:]
        if let windowId {
            guard let window = content.windows.first(where: { Int($0.windowID) == windowId }) else {
                throw BridgeError.notFound("window_not_found", "No capturable window with id \(windowId)")
            }
            let bundleId = window.owningApplication?.bundleIdentifier
            if policy.isBlocked(bundleId: bundleId) { throw BridgeError.appBlocked(bundleId ?? "?") }
            filter = SCContentFilter(desktopIndependentWindow: window)
            meta["windowId"] = .int(windowId)
            meta["ownerName"] = .optional(window.owningApplication?.applicationName)
        } else {
            let wanted = displayId.map { CGDirectDisplayID(truncatingIfNeeded: $0) } ?? CGMainDisplayID()
            guard let display = content.displays.first(where: { $0.displayID == wanted })
                ?? (displayId == nil ? content.displays.first : nil)
            else {
                throw BridgeError.notFound("display_not_found", "No display with id \(displayId ?? 0)")
            }
            filter = SCContentFilter(display: display, excludingWindows: [])
            meta["displayId"] = .int(Int(display.displayID))
        }

        let info = SCShareableContent.info(for: filter)
        let scale = Double(info.pointPixelScale)
        let pixelW = max(1, Double(info.contentRect.width) * scale)
        let pixelH = max(1, Double(info.contentRect.height) * scale)
        let targetW = min(Double(maxWidth), pixelW)
        let targetH = max(1, (pixelH * targetW / pixelW).rounded())

        let config = SCStreamConfiguration()
        config.width = Int(targetW.rounded())
        config.height = Int(targetH)
        config.showsCursor = false
        config.scalesToFit = true
        config.capturesAudio = false

        let image: CGImage
        do {
            image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
        } catch {
            if !CGPreflightScreenCaptureAccess() { throw BridgeError.permissionDenied("screenRecording") }
            throw BridgeError.unprocessable("capture_failed", "Screenshot failed: \(error.localizedDescription)")
        }

        guard let png = encodePNG(image) else { throw BridgeError.internalError("PNG encoding failed") }
        meta["png_base64"] = .string(png.base64EncodedString())
        meta["width"] = .int(image.width)
        meta["height"] = .int(image.height)
        meta["scale"] = .double(targetW / pixelW * scale)
        return .object(meta)
    }

    static func encodePNG(_ image: CGImage) -> Data? {
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data as CFMutableData, UTType.png.identifier as CFString, 1, nil)
        else { return nil }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return data as Data
    }
}
