import Foundation
import XCTest

@testable import JarvisBridgeCore

final class HTTPParserTests: XCTestCase {
    let parser = HTTPParser(maxHeaderBytes: 1024, maxBodyBytes: 100)

    private func complete(_ raw: String, file: StaticString = #filePath, line: UInt = #line) -> (HTTPRequestHead, Data, Int)? {
        guard case .complete(let head, let body, let consumed) = parser.parse(Data(raw.utf8)) else {
            XCTFail("expected complete parse, got \(parser.parse(Data(raw.utf8)))", file: file, line: line)
            return nil
        }
        return (head, body, consumed)
    }

    private func failureStatus(_ raw: String) -> Int? {
        if case .failure(let e) = parser.parse(Data(raw.utf8)) { return e.status }
        return nil
    }

    func testSimpleGet() throws {
        let raw = "GET /apps?x=1&name=Safari%20Tech&flag HTTP/1.1\r\nHost: 127.0.0.1:8766\r\nAuthorization: Bearer abc\r\n\r\n"
        let (head, body, consumed) = try XCTUnwrap(complete(raw))
        XCTAssertEqual(head.method, "GET")
        XCTAssertEqual(head.path, "/apps")
        XCTAssertEqual(head.query["x"], "1")
        XCTAssertEqual(head.query["name"], "Safari Tech")
        XCTAssertEqual(head.query["flag"], "")
        XCTAssertEqual(head.headers["host"], "127.0.0.1:8766")
        XCTAssertEqual(head.headers["authorization"], "Bearer abc")
        XCTAssertTrue(body.isEmpty)
        XCTAssertEqual(consumed, raw.utf8.count)
    }

    func testPostWithBody() throws {
        let json = #"{"text":"hällo"}"#
        let raw = "POST /input/type HTTP/1.1\r\nContent-Type: application/json\r\nContent-Length: \(json.utf8.count)\r\n\r\n\(json)"
        let (head, body, _) = try XCTUnwrap(complete(raw))
        XCTAssertEqual(head.contentLength, json.utf8.count)
        XCTAssertEqual(String(decoding: body, as: UTF8.self), json)
    }

    func testIncompleteHeadAndBody() {
        XCTAssertEqual(parser.parse(Data("GET / HTTP/1.1\r\nHost: x".utf8)), .incomplete(headComplete: false, expectsContinue: false))
        let partial = "POST /x HTTP/1.1\r\nContent-Length: 10\r\nExpect: 100-continue\r\n\r\nabc"
        XCTAssertEqual(parser.parse(Data(partial.utf8)), .incomplete(headComplete: true, expectsContinue: true))
    }

    func testPipelinedRequestsConsumeExactly() throws {
        let first = "POST /a HTTP/1.1\r\nContent-Length: 2\r\n\r\nhi"
        let second = "GET /b HTTP/1.1\r\n\r\n"
        var buffer = Data((first + second).utf8)
        guard case .complete(let h1, let b1, let c1) = parser.parse(buffer) else { return XCTFail() }
        XCTAssertEqual(h1.path, "/a")
        XCTAssertEqual(String(decoding: b1, as: UTF8.self), "hi")
        XCTAssertEqual(c1, first.utf8.count)
        buffer.removeFirst(c1)
        guard case .complete(let h2, _, _) = parser.parse(buffer) else { return XCTFail() }
        XCTAssertEqual(h2.path, "/b")
    }

    func testBodyTooLarge() {
        XCTAssertEqual(failureStatus("POST /x HTTP/1.1\r\nContent-Length: 101\r\n\r\n"), 413)
    }

    func testDefaultMaxBodyIsTenMegabytes() {
        XCTAssertEqual(HTTPParser.defaultMaxBodyBytes, 10 * 1024 * 1024)
        let p = HTTPParser()
        if case .failure(let e) = p.parse(Data("POST /x HTTP/1.1\r\nContent-Length: \(10 * 1024 * 1024 + 1)\r\n\r\n".utf8)) {
            XCTAssertEqual(e.status, 413)
        } else {
            XCTFail("expected 413")
        }
    }

    func testMalformedRequests() {
        XCTAssertEqual(failureStatus("POST /x HTTP/1.1\r\nContent-Length: abc\r\n\r\n"), 400)
        XCTAssertEqual(failureStatus("POST /x HTTP/1.1\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\n"), 400)
        XCTAssertEqual(failureStatus("POST /x HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"), 501)
        XCTAssertEqual(failureStatus("GET /x HTTP/2.0\r\n\r\n"), 505)
        XCTAssertEqual(failureStatus("GET http://evil/x HTTP/1.1\r\n\r\n"), 400)
        XCTAssertEqual(failureStatus("get /x HTTP/1.1\r\n\r\n"), 400)
        XCTAssertEqual(failureStatus("GET /x HTTP/1.1\r\nBad Header: 1\r\n\r\n"), 400)
        XCTAssertEqual(failureStatus("GET /x HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n"), 400)
        XCTAssertEqual(failureStatus("GET /x HTTP/1.1\r\nX: 1\r\n folded\r\n\r\n"), 400)
        XCTAssertEqual(failureStatus("GET / x HTTP/1.1\r\n\r\n"), 400)
    }

    func testHeaderTooLarge() {
        let big = "GET / HTTP/1.1\r\nX-Big: " + String(repeating: "a", count: 2000)
        XCTAssertEqual(failureStatus(big), 431)
    }

    func testKeepAliveSemantics() {
        XCTAssertTrue(HTTPRequest(method: "GET", path: "/", version: "HTTP/1.1").wantsKeepAlive)
        XCTAssertFalse(HTTPRequest(method: "GET", path: "/", version: "HTTP/1.1", headers: ["Connection": "close"]).wantsKeepAlive)
        XCTAssertFalse(HTTPRequest(method: "GET", path: "/", version: "HTTP/1.0").wantsKeepAlive)
        XCTAssertTrue(HTTPRequest(method: "GET", path: "/", version: "HTTP/1.0", headers: ["Connection": "keep-alive"]).wantsKeepAlive)
    }

    func testResponseSerialization() {
        let r = HTTPResponse.error(423, "kill_switch_engaged", "locked")
        let text = String(decoding: r.serialized(keepAlive: false), as: UTF8.self)
        XCTAssertTrue(text.hasPrefix("HTTP/1.1 423 Locked\r\n"))
        XCTAssertTrue(text.contains("Content-Length: \(r.body.count)\r\n"))
        XCTAssertTrue(text.contains("Connection: close\r\n"))
        XCTAssertEqual(r.jsonBody?["error"], "kill_switch_engaged")
    }

    func testJSONRoundTrip() throws {
        let v = try JSON.parse(Data(#"{"a":1,"b":2.5,"c":true,"d":null,"e":["x"],"f":{"g":"h"}}"#.utf8))
        XCTAssertEqual(v["a"], .int(1))
        XCTAssertEqual(v["b"], .double(2.5))
        XCTAssertEqual(v["c"], .bool(true))
        XCTAssertEqual(v["d"], .null)
        XCTAssertEqual(v["e"], .array(["x"]))
        XCTAssertEqual(v["f"]?["g"], "h")
        XCTAssertEqual(try JSON.parse(v.serialized()), v)
        XCTAssertEqual(JSON.double(.nan).serializedString, "null")
    }
}
