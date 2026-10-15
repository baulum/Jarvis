import AppKit
import JarvisBridgeCore

/// Background agent app (LSUIElement / .accessory): no Dock icon, no menu bar.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var runtime: BridgeRuntime?
    private var signalSources: [DispatchSourceSignal] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        let runtime: BridgeRuntime
        do {
            runtime = try BridgeRuntime()
        } catch {
            BridgeLog.error("Startup failed: \(error)")
            exit(1)
        }
        self.runtime = runtime
        runtime.start { result in
            if case .failure(let error) = result {
                BridgeLog.error("Could not start HTTP server on 127.0.0.1:\(BridgeRuntime.defaultPort): \(error)")
                exit(1)
            }
        }
        installSignalHandlers()
    }

    func applicationWillTerminate(_ notification: Notification) {
        runtime?.stop()
    }

    private func installSignalHandlers() {
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler { [weak self] in
                BridgeLog.info("Received signal \(sig); shutting down")
                self?.runtime?.stop()
                exit(0)
            }
            source.resume()
            signalSources.append(source)
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
