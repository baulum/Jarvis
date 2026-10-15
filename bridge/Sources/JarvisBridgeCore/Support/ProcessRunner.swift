import Foundation
import os

/// Runs an executable with an argument array (never via a shell), with timeout and output caps.
enum ProcessRunner {
    struct Output: Sendable {
        var exitCode: Int32
        var stdout: Data
        var stderr: Data
        var timedOut: Bool
        /// True if stdout exceeded `maxOutputBytes` and the process was terminated early.
        var truncated: Bool

        var stdoutString: String { String(decoding: stdout, as: UTF8.self) }
        var stderrString: String { String(decoding: stderr, as: UTF8.self) }
    }

    enum RunError: Error {
        case launchFailed(String)
    }

    private struct State {
        var stdout = Data()
        var stderr = Data()
        var truncated = false
        var timedOut = false
        var finished = false
    }

    static func run(
        executable: String,
        arguments: [String],
        timeout: TimeInterval,
        maxOutputBytes: Int = 4 * 1024 * 1024
    ) async throws -> Output {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe

        let state = OSAllocatedUnfairLock(initialState: State())
        // Wrap non-Sendable Foundation objects so they can be captured by @Sendable handlers.
        let proc = UncheckedBox(process)

        outPipe.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            if chunk.isEmpty { return }
            let overflow = state.withLock { s -> Bool in
                guard !s.truncated else { return false }
                if s.stdout.count + chunk.count > maxOutputBytes {
                    s.stdout.append(chunk.prefix(max(0, maxOutputBytes - s.stdout.count)))
                    s.truncated = true
                    return true
                }
                s.stdout.append(chunk)
                return false
            }
            if overflow { proc.value.terminate() }
        }
        errPipe.fileHandleForReading.readabilityHandler = { handle in
            let chunk = handle.availableData
            if chunk.isEmpty { return }
            state.withLock { s in
                if s.stderr.count < 256 * 1024 { s.stderr.append(chunk) }
            }
        }

        return try await withCheckedThrowingContinuation { cont in
            process.terminationHandler = { p in
                // Drain remaining output.
                outPipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil
                let restOut = (try? outPipe.fileHandleForReading.readToEnd()) ?? nil
                let restErr = (try? errPipe.fileHandleForReading.readToEnd()) ?? nil
                let result = state.withLock { s -> Output? in
                    guard !s.finished else { return nil }
                    s.finished = true
                    if let restOut, !s.truncated {
                        let room = max(0, maxOutputBytes - s.stdout.count)
                        s.stdout.append(restOut.prefix(room))
                        if restOut.count > room { s.truncated = true }
                    }
                    if let restErr, s.stderr.count < 256 * 1024 { s.stderr.append(restErr) }
                    return Output(
                        exitCode: p.terminationStatus, stdout: s.stdout, stderr: s.stderr,
                        timedOut: s.timedOut, truncated: s.truncated)
                }
                if let result { cont.resume(returning: result) }
            }
            do {
                try process.run()
            } catch {
                outPipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil
                cont.resume(throwing: RunError.launchFailed(error.localizedDescription))
                return
            }
            let pid = process.processIdentifier
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) {
                guard proc.value.isRunning else { return }
                state.withLock { $0.timedOut = true }
                proc.value.terminate()  // SIGTERM
                DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
                    if proc.value.isRunning { kill(pid, SIGKILL) }
                }
            }
        }
    }
}

/// Box for passing a non-Sendable reference where access is externally synchronized
/// or the type is thread-safe in practice (e.g. `Process.terminate()`).
final class UncheckedBox<T>: @unchecked Sendable {
    let value: T
    init(_ value: T) { self.value = value }
}
