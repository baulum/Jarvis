import Foundation

/// A parsed HTTP/1.x request.
public struct HTTPRequest: Sendable {
    public var method: String
    /// Percent-decoded path without query string, e.g. `/apps/launch`.
    public var path: String
    public var query: [String: String]
    /// `HTTP/1.1` or `HTTP/1.0`.
    public var version: String
    /// Header names are lowercased.
    public var headers: [String: String]
    public var body: Data

    public init(
        method: String, path: String, query: [String: String] = [:], version: String = "HTTP/1.1",
        headers: [String: String] = [:], body: Data = Data()
    ) {
        self.method = method
        self.path = path
        self.query = query
        self.version = version
        var lowered: [String: String] = [:]
        for (k, v) in headers { lowered[k.lowercased()] = v }
        self.headers = lowered
        self.body = body
    }

    public func header(_ name: String) -> String? {
        headers[name.lowercased()]
    }

    /// Whether the client wants the connection kept open after the response.
    public var wantsKeepAlive: Bool {
        let conn = header("connection")?.lowercased() ?? ""
        let tokens = conn.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
        if version == "HTTP/1.0" { return tokens.contains("keep-alive") }
        return !tokens.contains("close")
    }
}

/// An HTTP response. Bodies are always sent with Content-Length.
public struct HTTPResponse: Sendable {
    public var status: Int
    public var headers: [String: String]
    public var body: Data

    public init(status: Int, headers: [String: String] = [:], body: Data = Data()) {
        self.status = status
        self.headers = headers
        self.body = body
    }

    public static func json(_ status: Int, _ value: JSON) -> HTTPResponse {
        var data = value.serialized()
        data.append(0x0A)  // trailing newline is friendlier for curl
        return HTTPResponse(
            status: status,
            headers: ["Content-Type": "application/json; charset=utf-8"],
            body: data
        )
    }

    public static func error(
        _ status: Int, _ code: String, _ message: String, extra: [String: JSON] = [:]
    ) -> HTTPResponse {
        var obj: [String: JSON] = ["error": .string(code), "message": .string(message)]
        for (k, v) in extra { obj[k] = v }
        return .json(status, .object(obj))
    }

    /// The parsed JSON body (for tests and diagnostics).
    public var jsonBody: JSON? {
        try? JSON.parse(body)
    }

    public func serialized(keepAlive: Bool) -> Data {
        var head = "HTTP/1.1 \(status) \(HTTPResponse.reasonPhrase(status))\r\n"
        var hdrs = headers
        hdrs["Content-Length"] = String(body.count)
        hdrs["Connection"] = keepAlive ? "keep-alive" : "close"
        hdrs["Cache-Control"] = "no-store"
        hdrs["X-Content-Type-Options"] = "nosniff"
        hdrs["Server"] = "JarvisBridge"
        for key in hdrs.keys.sorted() {
            // Header values are generated internally; strip CR/LF defensively anyway.
            let value = hdrs[key]!.replacingOccurrences(of: "\r", with: "")
                .replacingOccurrences(of: "\n", with: "")
            head += "\(key): \(value)\r\n"
        }
        head += "\r\n"
        var data = Data(head.utf8)
        data.append(body)
        return data
    }

    public static func reasonPhrase(_ status: Int) -> String {
        switch status {
        case 100: return "Continue"
        case 200: return "OK"
        case 201: return "Created"
        case 204: return "No Content"
        case 400: return "Bad Request"
        case 401: return "Unauthorized"
        case 403: return "Forbidden"
        case 404: return "Not Found"
        case 405: return "Method Not Allowed"
        case 408: return "Request Timeout"
        case 409: return "Conflict"
        case 411: return "Length Required"
        case 413: return "Payload Too Large"
        case 415: return "Unsupported Media Type"
        case 421: return "Misdirected Request"
        case 422: return "Unprocessable Content"
        case 423: return "Locked"
        case 429: return "Too Many Requests"
        case 431: return "Request Header Fields Too Large"
        case 500: return "Internal Server Error"
        case 501: return "Not Implemented"
        case 503: return "Service Unavailable"
        case 504: return "Gateway Timeout"
        case 505: return "HTTP Version Not Supported"
        default: return "Status \(status)"
        }
    }
}
