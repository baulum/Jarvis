import Foundation
import Network
import XCTest

@testable import JarvisBridgeCore

final class HTTPServerTests: XCTestCase {
    func testLoopbackDetection() {
        func ep(_ host: String) -> NWEndpoint { .hostPort(host: NWEndpoint.Host(host), port: 1234) }
        XCTAssertTrue(HTTPServer.isLoopback(ep("127.0.0.1")))
        XCTAssertTrue(HTTPServer.isLoopback(ep("127.10.0.1")))
        XCTAssertTrue(HTTPServer.isLoopback(ep("::1")))
        XCTAssertTrue(HTTPServer.isLoopback(ep("::ffff:127.0.0.1")))
        XCTAssertFalse(HTTPServer.isLoopback(ep("192.168.1.10")))
        XCTAssertFalse(HTTPServer.isLoopback(ep("10.0.0.1")))
        XCTAssertFalse(HTTPServer.isLoopback(ep("fe80::1")))
        XCTAssertFalse(HTTPServer.isLoopback(ep("example.com")))
    }

    /// End-to-end over a real socket on a random high port.
    func testServesRequestsOverLoopbackWithKeepAlive() async throws {
        let port = UInt16.random(in: 40000...60000)
        var options = HTTPServer.Options()
        options.port = port
        let server = HTTPServer(options: options) { req in
            .json(200, ["path": .string(req.path), "len": .int(req.body.count)])
        }
        let started = expectation(description: "started")
        server.start { result in
            if case .failure(let e) = result { XCTFail("start failed: \(e)") }
            started.fulfill()
        }
        await fulfillment(of: [started], timeout: 5)
        defer { server.stop() }

        let session = URLSession(configuration: .ephemeral)
        for i in 0..<3 {
            var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/echo/\(i)")!)
            req.httpMethod = "POST"
            req.httpBody = Data(repeating: 0x41, count: 1000 * (i + 1))
            let (data, resp) = try await session.data(for: req)
            XCTAssertEqual((resp as? HTTPURLResponse)?.statusCode, 200)
            let json = try JSON.parse(data)
            XCTAssertEqual(json["path"], .string("/echo/\(i)"))
            XCTAssertEqual(json["len"], .int(1000 * (i + 1)))
        }
    }
}
