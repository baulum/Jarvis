import Foundation

/// Error produced while parsing a request; maps directly to an HTTP error response.
public struct HTTPParseError: Error, Equatable, Sendable {
    public let status: Int
    public let code: String
    public let message: String

    public init(_ status: Int, _ code: String, _ message: String) {
        self.status = status
        self.code = code
        self.message = message
    }
}

public enum HTTPParseResult: Equatable, Sendable {
    /// More bytes are needed. `headComplete` is true once the header block is parsed;
    /// `expectsContinue` indicates the client sent `Expect: 100-continue`.
    case incomplete(headComplete: Bool, expectsContinue: Bool)
    /// A full request was parsed; `consumed` bytes should be removed from the buffer.
    case complete(HTTPRequestHead, body: Data, consumed: Int)
    case failure(HTTPParseError)
}

/// Parsed request line + headers.
public struct HTTPRequestHead: Equatable, Sendable {
    public var method: String
    public var path: String
    public var query: [String: String]
    public var version: String
    public var headers: [String: String]
    public var contentLength: Int
}

/// A stateless HTTP/1.1 request parser that operates on an accumulating buffer.
/// Supports Content-Length bodies only (no chunked transfer encoding).
public struct HTTPParser: Sendable {
    public var maxHeaderBytes: Int
    public var maxBodyBytes: Int

    public static let defaultMaxHeaderBytes = 32 * 1024
    public static let defaultMaxBodyBytes = 10 * 1024 * 1024

    public init(
        maxHeaderBytes: Int = HTTPParser.defaultMaxHeaderBytes,
        maxBodyBytes: Int = HTTPParser.defaultMaxBodyBytes
    ) {
        self.maxHeaderBytes = maxHeaderBytes
        self.maxBodyBytes = maxBodyBytes
    }

    private static let crlfcrlf: [UInt8] = [13, 10, 13, 10]

    public func parse(_ buffer: Data) -> HTTPParseResult {
        // Tolerate (and skip) leading empty lines between pipelined requests (RFC 9112 2.2).
        var start = buffer.startIndex
        while start + 1 < buffer.endIndex, buffer[start] == 13, buffer[start + 1] == 10 {
            start += 2
        }
        let searchEnd = min(buffer.endIndex, start + maxHeaderBytes + 4)
        guard let headEnd = Self.find(Self.crlfcrlf, in: buffer, from: start, to: searchEnd) else {
            if buffer.endIndex - start > maxHeaderBytes {
                return .failure(HTTPParseError(431, "headers_too_large", "Request header block too large"))
            }
            return .incomplete(headComplete: false, expectsContinue: false)
        }

        let headBytes = buffer[start..<headEnd]
        guard let headText = String(data: Data(headBytes), encoding: .utf8) else {
            return .failure(HTTPParseError(400, "bad_request", "Request head is not valid UTF-8"))
        }

        let head: HTTPRequestHead
        switch parseHead(headText) {
        case .success(let h): head = h
        case .failure(let e): return .failure(e)
        }

        let bodyStart = headEnd + 4
        let available = buffer.endIndex - bodyStart
        if available < head.contentLength {
            let expects = head.headers["expect"]?.lowercased() == "100-continue"
            return .incomplete(headComplete: true, expectsContinue: expects)
        }
        let body = Data(buffer[bodyStart..<(bodyStart + head.contentLength)])
        let consumed = (bodyStart + head.contentLength) - buffer.startIndex
        return .complete(head, body: body, consumed: consumed)
    }

    func parseHead(_ text: String) -> Result<HTTPRequestHead, HTTPParseError> {
        let lines = text.components(separatedBy: "\r\n")
        guard let requestLine = lines.first, !requestLine.isEmpty else {
            return .failure(HTTPParseError(400, "bad_request", "Missing request line"))
        }
        let parts = requestLine.split(separator: " ", omittingEmptySubsequences: false)
        guard parts.count == 3 else {
            return .failure(HTTPParseError(400, "bad_request", "Malformed request line"))
        }
        let method = String(parts[0])
        let target = String(parts[1])
        let version = String(parts[2])

        guard !method.isEmpty, method.count <= 16,
            method.unicodeScalars.allSatisfy({ $0.value >= 65 && $0.value <= 90 })
        else {
            return .failure(HTTPParseError(400, "bad_request", "Invalid method"))
        }
        guard version == "HTTP/1.1" || version == "HTTP/1.0" else {
            return .failure(HTTPParseError(505, "http_version_not_supported", "Only HTTP/1.0 and HTTP/1.1 are supported"))
        }
        guard target.hasPrefix("/"), target.count <= 8192 else {
            return .failure(HTTPParseError(400, "bad_request", "Request target must be an absolute path"))
        }

        let rawPath: Substring
        let rawQuery: Substring?
        if let q = target.firstIndex(of: "?") {
            rawPath = target[target.startIndex..<q]
            rawQuery = target[target.index(after: q)...]
        } else {
            rawPath = Substring(target)
            rawQuery = nil
        }
        guard let path = String(rawPath).removingPercentEncoding, !path.contains("\0") else {
            return .failure(HTTPParseError(400, "bad_request", "Invalid path encoding"))
        }
        let query = rawQuery.map { Self.parseQuery(String($0)) } ?? [:]

        var headers: [String: String] = [:]
        var contentLength: Int? = nil
        for line in lines.dropFirst() {
            if line.isEmpty { continue }
            if line.first == " " || line.first == "\t" {
                return .failure(HTTPParseError(400, "bad_request", "Obsolete header folding is not supported"))
            }
            guard let colon = line.firstIndex(of: ":") else {
                return .failure(HTTPParseError(400, "bad_request", "Malformed header line"))
            }
            let name = String(line[line.startIndex..<colon])
            guard !name.isEmpty, name.unicodeScalars.allSatisfy({ Self.isTokenChar($0) }) else {
                return .failure(HTTPParseError(400, "bad_request", "Invalid header name"))
            }
            let value = line[line.index(after: colon)...].trimmingCharacters(in: CharacterSet(charactersIn: " \t"))
            let key = name.lowercased()

            switch key {
            case "content-length":
                guard !value.isEmpty, value.count <= 12, value.allSatisfy({ $0.isASCII && $0.isNumber }),
                    let n = Int(value)
                else {
                    return .failure(HTTPParseError(400, "bad_request", "Invalid Content-Length"))
                }
                if let existing = contentLength, existing != n {
                    return .failure(HTTPParseError(400, "bad_request", "Conflicting Content-Length headers"))
                }
                contentLength = n
                headers[key] = value
            case "transfer-encoding":
                return .failure(HTTPParseError(501, "not_implemented", "Transfer-Encoding is not supported; send Content-Length"))
            case "host":
                if headers[key] != nil {
                    return .failure(HTTPParseError(400, "bad_request", "Duplicate Host header"))
                }
                headers[key] = value
            default:
                if let existing = headers[key] {
                    headers[key] = existing + ", " + value
                } else {
                    headers[key] = value
                }
            }
        }

        let length = contentLength ?? 0
        if length > maxBodyBytes {
            return .failure(HTTPParseError(413, "payload_too_large", "Request body exceeds \(maxBodyBytes) bytes"))
        }

        return .success(
            HTTPRequestHead(
                method: method, path: path, query: query, version: version,
                headers: headers, contentLength: length))
    }

    static func parseQuery(_ raw: String) -> [String: String] {
        var out: [String: String] = [:]
        for pair in raw.split(separator: "&", omittingEmptySubsequences: true) {
            let kv = pair.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            let decode: (Substring) -> String = {
                String($0).replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? String($0)
            }
            let key = decode(kv[0])
            let value = kv.count > 1 ? decode(kv[1]) : ""
            if !key.isEmpty { out[key] = value }
        }
        return out
    }

    static func isTokenChar(_ s: Unicode.Scalar) -> Bool {
        guard s.isASCII else { return false }
        let v = s.value
        if (48...57).contains(v) || (65...90).contains(v) || (97...122).contains(v) { return true }
        return "!#$%&'*+-.^_`|~".unicodeScalars.contains(s)
    }

    static func find(_ needle: [UInt8], in data: Data, from: Data.Index, to: Data.Index) -> Data.Index? {
        guard to - from >= needle.count else { return nil }
        var i = from
        let last = to - needle.count
        while i <= last {
            if data[i] == needle[0] {
                var match = true
                for j in 1..<needle.count where data[i + j] != needle[j] {
                    match = false
                    break
                }
                if match { return i }
            }
            i += 1
        }
        return nil
    }
}

public extension HTTPRequest {
    init(head: HTTPRequestHead, body: Data) {
        self.init(
            method: head.method, path: head.path, query: head.query, version: head.version,
            headers: head.headers, body: body)
    }
}
