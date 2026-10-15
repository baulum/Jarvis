import AppKit
import Foundation

enum FileHandlers {
    static func guardFor(_ ctx: RequestContext) -> PathGuard {
        PathGuard(roots: ctx.policy.allowedFileRoots)
    }

    // MARK: Search (Spotlight via mdfind)

    static func search(_ ctx: RequestContext) async throws -> JSON {
        let query = try ctx.string("query", maxLength: 500)
        // mdfind has no "--" separator; a leading "-" would be parsed as an option.
        guard !query.hasPrefix("-") else {
            throw BridgeError.badRequest("invalid_param", "'query' must not start with '-'")
        }
        let limit = min(max(try ctx.optionalInt("limit") ?? 50, 1), 500)
        let match = (try ctx.optionalString("match", maxLength: 16) ?? "any").lowercased()
        guard match == "any" || match == "name" else {
            throw BridgeError.badRequest("invalid_param", "'match' must be 'any' or 'name'")
        }

        var args: [String] = []
        if let onlyIn = try ctx.optionalString("onlyIn", maxLength: 4096) {
            let expanded = try PathGuard(roots: []).expand(onlyIn)
            var isDir: ObjCBool = false
            guard FileManager.default.fileExists(atPath: expanded, isDirectory: &isDir), isDir.boolValue else {
                throw BridgeError.notFound("directory_not_found", "onlyIn directory does not exist")
            }
            args += ["-onlyin", expanded]
        }
        if match == "name" {
            args += ["-name", query]
        } else {
            args.append(query)
        }

        let output: ProcessRunner.Output
        do {
            output = try await ProcessRunner.run(
                executable: "/usr/bin/mdfind", arguments: args, timeout: 20, maxOutputBytes: 2 * 1024 * 1024)
        } catch {
            throw BridgeError.internalError("Spotlight search could not be started")
        }
        if output.exitCode != 0 && !output.truncated && !output.timedOut && output.stdout.isEmpty {
            throw BridgeError.unprocessable(
                "search_failed", "mdfind failed: \(output.stderrString.prefix(300))")
        }

        let paths = output.stdoutString.split(separator: "\n").prefix(limit).map(String.init)
        let results: [JSON] = try await runBlocking {
            paths.compactMap { describe(path: $0) }
        }
        return .array(results)
    }

    static func describe(path: String) -> JSON? {
        let url = URL(fileURLWithPath: path)
        let keys: Set<URLResourceKey> = [
            .isDirectoryKey, .isPackageKey, .isSymbolicLinkKey, .contentModificationDateKey,
            .fileSizeKey, .localizedTypeDescriptionKey,
        ]
        guard let v = try? url.resourceValues(forKeys: keys) else { return nil }
        let kind: String
        if v.isSymbolicLink == true {
            kind = "symlink"
        } else if v.isPackage == true {
            kind = "package"
        } else if v.isDirectory == true {
            kind = "directory"
        } else {
            kind = "file"
        }
        return [
            "path": .string(path),
            "name": .string(url.lastPathComponent),
            "kind": .string(kind),
            "type": .optional(v.localizedTypeDescription),
            "modified": .optional(v.contentModificationDate.map(Timestamp.string)),
            "size": .optional(v.fileSize),
        ]
    }

    // MARK: Move / rename / mkdir / reveal

    static func move(_ ctx: RequestContext) async throws -> JSON {
        let guardian = guardFor(ctx)
        let from = try guardian.check(try ctx.string("from", maxLength: 4096))
        var to = try guardian.check(try ctx.string("to", maxLength: 4096))
        let fm = FileManager.default

        var st = stat()
        guard lstat(from, &st) == 0 else { throw BridgeError.notFound("source_not_found", "Source does not exist") }

        // Like mv(1): moving onto an existing directory moves the item into it.
        var isDir: ObjCBool = false
        if fm.fileExists(atPath: to, isDirectory: &isDir), isDir.boolValue {
            to = try guardian.check((to as NSString).appendingPathComponent((from as NSString).lastPathComponent))
        }
        return try await performMove(from: from, to: to)
    }

    static func rename(_ ctx: RequestContext) async throws -> JSON {
        let guardian = guardFor(ctx)
        let path = try guardian.check(try ctx.string("path", maxLength: 4096))
        let newName = try ctx.string("newName", maxLength: 255)
        guard !newName.contains("/"), !newName.contains("\0"), newName != ".", newName != ".." else {
            throw BridgeError.badRequest("invalid_param", "'newName' must be a plain file name")
        }
        var st = stat()
        guard lstat(path, &st) == 0 else { throw BridgeError.notFound("source_not_found", "Source does not exist") }
        let to = try guardian.check(((path as NSString).deletingLastPathComponent as NSString).appendingPathComponent(newName))
        return try await performMove(from: path, to: to)
    }

    private static func performMove(from: String, to: String) async throws -> JSON {
        let fm = FileManager.default
        var st = stat()
        if lstat(to, &st) == 0 {
            throw BridgeError(status: 409, code: "destination_exists", message: "Destination already exists")
        }
        let parent = (to as NSString).deletingLastPathComponent
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: parent, isDirectory: &isDir), isDir.boolValue else {
            throw BridgeError(status: 409, code: "destination_parent_missing", message: "Destination directory does not exist")
        }
        do {
            try fm.moveItem(atPath: from, toPath: to)
        } catch {
            throw BridgeError.unprocessable("move_failed", error.localizedDescription)
        }
        return ["ok": true, "from": .string(from), "to": .string(to)]
    }

    static func mkdir(_ ctx: RequestContext) async throws -> JSON {
        let path = try guardFor(ctx).check(try ctx.string("path", maxLength: 4096))
        let fm = FileManager.default
        var isDir: ObjCBool = false
        if fm.fileExists(atPath: path, isDirectory: &isDir) {
            if isDir.boolValue { return ["ok": true, "path": .string(path), "created": false] }
            throw BridgeError(status: 409, code: "exists_not_directory", message: "A file with that name already exists")
        }
        do {
            try fm.createDirectory(atPath: path, withIntermediateDirectories: true)
        } catch {
            throw BridgeError.unprocessable("mkdir_failed", error.localizedDescription)
        }
        return ["ok": true, "path": .string(path), "created": true]
    }

    static func reveal(_ ctx: RequestContext) async throws -> JSON {
        let path = try guardFor(ctx).check(try ctx.string("path", maxLength: 4096), allowRoot: true)
        var st = stat()
        guard lstat(path, &st) == 0 else { throw BridgeError.notFound("not_found", "Path does not exist") }
        await MainActor.run {
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        }
        return ["ok": true, "path": .string(path)]
    }
}
