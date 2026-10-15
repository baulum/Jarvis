import Foundation
import Network

/// A minimal, dependency-free HTTP/1.1 server built on Network.framework.
///
/// - Binds only to the loopback interface.
/// - Rejects connections whose remote endpoint is not a loopback address.
/// - Supports keep-alive, Content-Length bodies, `Expect: 100-continue`,
///   request read timeouts and idle timeouts.
public final class HTTPServer: @unchecked Sendable {
    public typealias Handler = @Sendable (HTTPRequest) async -> HTTPResponse

    public struct Options: Sendable {
        public var host: String = "127.0.0.1"
        public var port: UInt16 = 8766
        public var maxBodyBytes: Int = HTTPParser.defaultMaxBodyBytes
        /// Max time to receive one complete request once its first byte arrived (or the connection opened).
        public var requestTimeout: TimeInterval = 15
        /// Max idle time on a keep-alive connection between requests.
        public var idleTimeout: TimeInterval = 30
        public var maxConnections: Int = 64

        public init() {}
    }

    private let options: Options
    private let handler: Handler
    private let queue = DispatchQueue(label: "com.jarvis.bridge.http")
    // All mutable state below is only touched on `queue`.
    private var listener: NWListener?
    private var connections: [ObjectIdentifier: HTTPConnection] = [:]
    private var startReported = false

    public init(options: Options = Options(), handler: @escaping Handler) {
        self.options = options
        self.handler = handler
    }

    /// Starts listening. `onState` is called once with `.success` when ready or `.failure` on error.
    public func start(onState: @escaping @Sendable (Result<Void, Error>) -> Void) {
        queue.async { [self] in
            do {
                let params = NWParameters.tcp
                params.allowLocalEndpointReuse = true
                params.acceptLocalOnly = true
                guard let port = NWEndpoint.Port(rawValue: options.port) else {
                    throw NSError(domain: "JarvisBridge", code: 1, userInfo: [NSLocalizedDescriptionKey: "Invalid port"])
                }
                params.requiredLocalEndpoint = .hostPort(host: NWEndpoint.Host(options.host), port: port)
                let listener = try NWListener(using: params)
                self.listener = listener

                // The state handler runs on `queue`, so `startReported` is queue-confined.
                listener.stateUpdateHandler = { [weak self] state in
                    guard let self else { return }
                    switch state {
                    case .ready:
                        if !self.startReported {
                            self.startReported = true
                            onState(.success(()))
                        }
                    case .failed(let error):
                        BridgeLog.error("HTTP listener failed: \(error)")
                        if !self.startReported {
                            self.startReported = true
                            onState(.failure(error))
                        }
                        self.listener?.cancel()
                    case .waiting(let error):
                        // e.g. address in use; treat as fatal at startup.
                        BridgeLog.error("HTTP listener waiting: \(error)")
                        if !self.startReported {
                            self.startReported = true
                            onState(.failure(error))
                            self.listener?.cancel()
                        }
                    default:
                        break
                    }
                }
                listener.newConnectionHandler = { [weak self] conn in
                    self?.accept(conn)
                }
                listener.start(queue: queue)
            } catch {
                onState(.failure(error))
            }
        }
    }

    public func stop() {
        queue.sync {
            listener?.cancel()
            listener = nil
            for c in connections.values { c.close() }
            connections.removeAll()
        }
    }

    /// True if the endpoint is a loopback address (127.0.0.0/8 or ::1, incl. IPv4-mapped).
    public static func isLoopback(_ endpoint: NWEndpoint) -> Bool {
        guard case .hostPort(let host, _) = endpoint else { return false }
        // Network's `isLoopback` only matches 127.0.0.1 exactly; check 127.0.0.0/8 by hand.
        func v4Loopback(_ a: IPv4Address) -> Bool { a.rawValue.first == 127 }
        switch host {
        case .ipv4(let addr):
            return v4Loopback(addr)
        case .ipv6(let addr):
            if addr.isLoopback { return true }
            if addr.isIPv4Mapped, let v4 = addr.asIPv4 { return v4Loopback(v4) }
            return false
        case .name:
            // Remote endpoints of accepted connections are always numeric; refuse names.
            return false
        @unknown default:
            return false
        }
    }

    // MARK: - Connection management (on queue)

    private func accept(_ nw: NWConnection) {
        guard Self.isLoopback(nw.endpoint) else {
            BridgeLog.warn("Rejected non-loopback connection from \(nw.endpoint)")
            nw.cancel()
            return
        }
        guard connections.count < options.maxConnections else {
            BridgeLog.warn("Rejected connection: too many open connections")
            nw.cancel()
            return
        }
        let conn = HTTPConnection(
            connection: nw, queue: queue, options: options, handler: handler,
            onClose: { [weak self] c in
                self?.connections.removeValue(forKey: ObjectIdentifier(c))
            })
        connections[ObjectIdentifier(conn)] = conn
        conn.start()
    }
}

/// One TCP connection. All state is confined to the server queue.
final class HTTPConnection: @unchecked Sendable {
    private let connection: NWConnection
    private let queue: DispatchQueue
    private let options: HTTPServer.Options
    private let handler: HTTPServer.Handler
    private let parser: HTTPParser
    private let onClose: (HTTPConnection) -> Void

    private var buffer = Data()
    private var busy = false
    private var receiving = false
    private var closed = false
    private var sentContinue = false
    private var timer: DispatchWorkItem?

    init(
        connection: NWConnection, queue: DispatchQueue, options: HTTPServer.Options,
        handler: @escaping HTTPServer.Handler, onClose: @escaping (HTTPConnection) -> Void
    ) {
        self.connection = connection
        self.queue = queue
        self.options = options
        self.handler = handler
        self.onClose = onClose
        self.parser = HTTPParser(maxBodyBytes: options.maxBodyBytes)
    }

    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .failed, .cancelled:
                self?.close()
            default:
                break
            }
        }
        connection.start(queue: queue)
        armTimer(options.requestTimeout, isIdle: false)
        receive()
    }

    func close() {
        guard !closed else { return }
        closed = true
        timer?.cancel()
        timer = nil
        connection.cancel()
        onClose(self)
    }

    private func armTimer(_ seconds: TimeInterval, isIdle: Bool) {
        timer?.cancel()
        let item = DispatchWorkItem { [weak self] in
            guard let self, !self.closed, !self.busy else { return }
            if isIdle && self.buffer.isEmpty {
                self.close()
            } else {
                self.sendAndClose(.error(408, "request_timeout", "Request not received in time"))
            }
        }
        timer = item
        queue.asyncAfter(deadline: .now() + seconds, execute: item)
    }

    private func receive() {
        guard !closed, !busy, !receiving else { return }
        receiving = true
        connection.receive(minimumIncompleteLength: 1, maximumLength: 256 * 1024) {
            [weak self] data, _, isComplete, error in
            guard let self else { return }
            self.receiving = false
            if self.closed { return }
            if let data, !data.isEmpty {
                if self.buffer.isEmpty && !self.busy {
                    // First bytes of a new request: switch from idle to request timeout.
                    self.armTimer(self.options.requestTimeout, isIdle: false)
                }
                self.buffer.append(data)
            }
            self.process()
            if error != nil || isComplete {
                if !self.busy { self.close() }
                return
            }
            self.receive()
        }
    }

    private func process() {
        guard !closed, !busy, !buffer.isEmpty else { return }
        switch parser.parse(buffer) {
        case .incomplete(_, let expectsContinue):
            if expectsContinue && !sentContinue {
                sentContinue = true
                connection.send(
                    content: Data("HTTP/1.1 100 Continue\r\n\r\n".utf8),
                    completion: .contentProcessed { _ in })
            }
        case .failure(let err):
            sendAndClose(.error(err.status, err.code, err.message))
        case .complete(let head, let body, let consumed):
            buffer.removeFirst(consumed)
            buffer = Data(buffer)  // re-base indices to 0
            busy = true
            timer?.cancel()
            let request = HTTPRequest(head: head, body: body)
            let keepAlive = request.wantsKeepAlive
            let handler = self.handler
            Task {
                let response = await handler(request)
                self.queue.async {
                    self.send(response, keepAlive: keepAlive)
                }
            }
        }
    }

    private func send(_ response: HTTPResponse, keepAlive: Bool) {
        guard !closed else { return }
        connection.send(
            content: response.serialized(keepAlive: keepAlive),
            completion: .contentProcessed { [weak self] error in
                guard let self else { return }
                self.queue.async {
                    guard !self.closed else { return }
                    self.busy = false
                    self.sentContinue = false
                    if error != nil || !keepAlive {
                        self.close()
                        return
                    }
                    if self.buffer.isEmpty {
                        self.armTimer(self.options.idleTimeout, isIdle: true)
                    } else {
                        self.armTimer(self.options.requestTimeout, isIdle: false)
                        self.process()
                    }
                    self.receive()
                }
            })
    }

    private func sendAndClose(_ response: HTTPResponse) {
        guard !closed else { return }
        busy = true
        timer?.cancel()
        connection.send(
            content: response.serialized(keepAlive: false),
            completion: .contentProcessed { [weak self] _ in
                guard let self else { return }
                self.queue.async { self.close() }
            })
    }
}
