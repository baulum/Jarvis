import AppKit
import Foundation

/// AppleScript, Shortcuts and URL opening.
enum AutomationHandlers {
    // MARK: AppleScript

    static func appleScript(_ ctx: RequestContext) async throws -> JSON {
        // The pipeline already enforces allowAppleScript; re-check defensively.
        guard ctx.policy.allowAppleScript else {
            throw BridgeError.forbidden("applescript_disabled", "AppleScript is disabled by policy")
        }
        let source = try ctx.string("source", maxLength: 100_000)
        // NSAppleScript is not thread-safe; run it on the main thread.
        let result: Result<String?, BridgeError> = await MainActor.run {
            guard let script = NSAppleScript(source: source) else {
                return .failure(BridgeError.badRequest("invalid_script", "Could not create AppleScript"))
            }
            var errorInfo: NSDictionary?
            let descriptor = script.executeAndReturnError(&errorInfo)
            if let errorInfo {
                let message = errorInfo[NSAppleScript.errorMessage] as? String ?? "AppleScript error"
                let number = (errorInfo[NSAppleScript.errorNumber] as? NSNumber)?.intValue
                return .failure(
                    BridgeError.unprocessable(
                        "applescript_error", message, extra: ["errorNumber": .optional(number)]))
            }
            if let s = descriptor.stringValue { return .success(s) }
            let coerced = descriptor.coerce(toDescriptorType: typeUnicodeText)?.stringValue
            return .success(coerced)
        }
        switch result {
        case .success(let s): return ["ok": true, "result": .optional(s)]
        case .failure(let e): throw e
        }
    }

    // MARK: Shortcuts

    static let shortcutsBinary = "/usr/bin/shortcuts"

    static func listShortcuts(_ ctx: RequestContext) async throws -> JSON {
        let out: ProcessRunner.Output
        do {
            out = try await ProcessRunner.run(executable: shortcutsBinary, arguments: ["list"], timeout: 20)
        } catch {
            throw BridgeError.internalError("Could not run shortcuts")
        }
        if out.timedOut { throw BridgeError(status: 504, code: "timeout", message: "shortcuts list timed out") }
        guard out.exitCode == 0 else {
            throw BridgeError.unprocessable("shortcuts_failed", String(out.stderrString.prefix(500)))
        }
        let names = out.stdoutString.split(separator: "\n")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        return .array(names.map { .string($0) })
    }

    static func runShortcut(_ ctx: RequestContext) async throws -> JSON {
        let name = try ctx.string("name", maxLength: 256)
        guard !name.hasPrefix("-"), !name.contains("\n"), !name.contains("\0") else {
            throw BridgeError.badRequest("invalid_param", "Invalid shortcut name")
        }
        let input = try ctx.optionalString("input", maxLength: 1_000_000)

        let fm = FileManager.default
        let tmp = fm.temporaryDirectory.appendingPathComponent("jarvis-bridge-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: tmp, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        defer { try? fm.removeItem(at: tmp) }

        let outputURL = tmp.appendingPathComponent("output")
        var args = ["run", name, "--output-path", outputURL.path]
        if let input {
            let inputURL = tmp.appendingPathComponent("input.txt")
            guard fm.createFile(atPath: inputURL.path, contents: Data(input.utf8), attributes: [.posixPermissions: 0o600])
            else { throw BridgeError.internalError("Could not write shortcut input") }
            args += ["--input-path", inputURL.path]
        }

        let out: ProcessRunner.Output
        do {
            out = try await ProcessRunner.run(executable: shortcutsBinary, arguments: args, timeout: 60)
        } catch {
            throw BridgeError.internalError("Could not run shortcuts")
        }
        if out.timedOut {
            throw BridgeError(status: 504, code: "timeout", message: "Shortcut did not finish within 60 seconds")
        }
        guard out.exitCode == 0 else {
            throw BridgeError.unprocessable(
                "shortcut_failed", String(out.stderrString.trimmingCharacters(in: .whitespacesAndNewlines).prefix(500)),
                extra: ["exitCode": .int(Int(out.exitCode))])
        }

        var result: [String: JSON] = ["ok": true, "name": .string(name)]
        if let data = try? Data(contentsOf: outputURL) {
            if let text = String(data: data.prefix(1_000_000), encoding: .utf8) {
                result["output"] = .string(text)
            } else {
                result["output"] = .null
                result["outputBytes"] = .int(data.count)
            }
        } else {
            let text = out.stdoutString.trimmingCharacters(in: .whitespacesAndNewlines)
            result["output"] = text.isEmpty ? .null : .string(String(text.prefix(1_000_000)))
        }
        return .object(result)
    }

    // MARK: Open URL

    static func openURL(_ ctx: RequestContext) async throws -> JSON {
        let raw = try ctx.string("url", maxLength: 8192)
        guard let url = URL(string: raw), let scheme = url.scheme?.lowercased() else {
            throw BridgeError.badRequest("invalid_url", "Not a valid URL")
        }
        guard ["http", "https", "mailto"].contains(scheme) else {
            throw BridgeError.forbidden("scheme_not_allowed", "Only http, https and mailto URLs are allowed")
        }
        if scheme != "mailto" {
            guard let host = url.host, !host.isEmpty else { throw BridgeError.badRequest("invalid_url", "URL has no host") }
        }
        let ok = await MainActor.run { NSWorkspace.shared.open(url) }
        guard ok else { throw BridgeError.unprocessable("open_failed", "The URL could not be opened") }
        return ["ok": true, "url": .string(url.absoluteString)]
    }
}
