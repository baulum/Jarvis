import Foundation
import XCTest

@testable import JarvisBridgeCore

final class RouterTests: XCTestCase {
    func testMatching() {
        let router = Router(routes: [
            Route("GET", "/clipboard", action: "a", access: .status) { _ in nil },
            Route("POST", "/clipboard", action: "b", access: .act) { _ in nil },
        ])
        if case .found(let r) = router.match(method: "GET", path: "/clipboard") { XCTAssertEqual(r.action, "a") } else { XCTFail() }
        if case .found(let r) = router.match(method: "POST", path: "/clipboard/") { XCTAssertEqual(r.action, "b") } else { XCTFail() }
        if case .methodNotAllowed(let allowed) = router.match(method: "PUT", path: "/clipboard") {
            XCTAssertEqual(allowed, ["GET", "POST"])
        } else {
            XCTFail()
        }
        if case .notFound = router.match(method: "GET", path: "/nope") {} else { XCTFail() }
    }

    func testAllRoutesAreUniqueAndPolicyKnown() {
        let routes = BridgeRoutes.all()
        let keys = routes.map { "\($0.method) \($0.path)" }
        XCTAssertEqual(Set(keys).count, keys.count)
        for r in routes {
            XCTAssertTrue(
                BridgeAction.all.contains(r.action) || BridgeAction.alwaysAllowed.contains(r.action),
                "unknown action \(r.action)")
        }
        // Only /health is unauthenticated.
        XCTAssertEqual(routes.filter { $0.access == .open }.map(\.path), ["/health"])
    }
}

final class PipelineTests: XCTestCase {
    func testHealthIsOpen() async throws {
        let f = try PipelineFixture()
        let r = await f.request("GET", "/health", auth: false)
        XCTAssertEqual(r.status, 200)
    }

    func testAuthRequired() async throws {
        let f = try PipelineFixture()
        var r = await f.request("GET", "/apps", auth: false)
        XCTAssertEqual(r.status, 401)
        XCTAssertEqual(r.jsonBody?["error"], "unauthorized")
        XCTAssertNotNil(r.headers["WWW-Authenticate"])

        r = await f.request("GET", "/apps", auth: false, headers: ["Authorization": "Bearer wrong"])
        XCTAssertEqual(r.status, 401)

        r = await f.request("GET", "/apps")
        XCTAssertEqual(r.status, 200)
    }

    func testNotFoundAndMethodNotAllowed() async throws {
        let f = try PipelineFixture()
        var r = await f.request("GET", "/nope")
        XCTAssertEqual(r.status, 404)
        r = await f.request("DELETE", "/apps")
        XCTAssertEqual(r.status, 405)
        XCTAssertEqual(r.headers["Allow"], "GET")
    }

    func testHostAndOriginChecks() async throws {
        let f = try PipelineFixture()
        var r = await f.request("GET", "/health", auth: false, headers: ["Host": "evil.example:8766"])
        XCTAssertEqual(r.status, 421)
        r = await f.request("GET", "/health", auth: false, headers: ["Host": "localhost:8766"])
        XCTAssertEqual(r.status, 200)
        r = await f.request("GET", "/apps", headers: ["Host": "127.0.0.1:8766", "Origin": "https://evil.example"])
        XCTAssertEqual(r.status, 403)
        XCTAssertEqual(r.jsonBody?["error"], "origin_not_allowed")
    }

    func testInvalidJSONBody() async throws {
        let f = try PipelineFixture()
        var r = await f.request("POST", "/apps/activate", body: "{nope")
        XCTAssertEqual(r.status, 400)
        XCTAssertEqual(r.jsonBody?["error"], "invalid_json")
        r = await f.request("POST", "/apps/activate", body: "[1,2]")
        XCTAssertEqual(r.status, 400)
        r = await f.request("POST", "/apps/activate", body: #"{"name":"Finder"}"#)
        XCTAssertEqual(r.status, 200)
        XCTAssertEqual(r.jsonBody?["echo"]?["name"], "Finder")
    }

    func testKillSwitchBlocksActingButNotStatus() async throws {
        let f = try PipelineFixture()
        var r = await f.request("POST", "/killswitch", body: #"{"engaged": true}"#)
        XCTAssertEqual(r.status, 200)
        XCTAssertEqual(r.jsonBody?["killSwitch"], true)

        r = await f.request("POST", "/apps/activate", body: #"{"name":"Finder"}"#)
        XCTAssertEqual(r.status, 423)
        XCTAssertEqual(r.jsonBody?["error"], "kill_switch_engaged")
        r = await f.request("GET", "/clipboard")
        XCTAssertEqual(r.status, 423, "sensitive reads are blocked too")
        r = await f.request("GET", "/apps")
        XCTAssertEqual(r.status, 200, "status endpoints keep working")
        r = await f.request("GET", "/health", auth: false)
        XCTAssertEqual(r.status, 200)

        r = await f.request("POST", "/killswitch", body: #"{"engaged": false}"#)
        XCTAssertEqual(r.jsonBody?["killSwitch"], false)
        r = await f.request("POST", "/apps/activate", body: #"{"name":"Finder"}"#)
        XCTAssertEqual(r.status, 200)
    }

    func testKillFileEngagesOnEveryRequest() async throws {
        let f = try PipelineFixture()
        FileManager.default.createFile(atPath: f.paths.killFile.path, contents: nil)
        var r = await f.request("POST", "/apps/activate", body: "{}")
        XCTAssertEqual(r.status, 423)
        // API cannot release a file-based kill switch.
        r = await f.request("POST", "/killswitch", body: #"{"engaged": false}"#)
        XCTAssertEqual(r.jsonBody?["killSwitch"], true)
        XCTAssertEqual(r.jsonBody?["fileEngaged"], true)
        try FileManager.default.removeItem(at: f.paths.killFile)
        r = await f.request("POST", "/apps/activate", body: "{}")
        XCTAssertEqual(r.status, 200)
    }

    func testPolicyDenial() async throws {
        let f = try PipelineFixture()
        var r = await f.request("POST", "/applescript", body: #"{"source":"return 1"}"#)
        XCTAssertEqual(r.status, 403)
        XCTAssertEqual(r.jsonBody?["error"], "applescript_disabled")

        try f.writePolicy(#"{"allowedActions": ["apps.list"]}"#)
        r = await f.request("POST", "/apps/activate", body: "{}")
        XCTAssertEqual(r.status, 403)
        XCTAssertEqual(r.jsonBody?["error"], "action_not_allowed")
        r = await f.request("GET", "/apps")
        XCTAssertEqual(r.status, 200)
        // Kill switch is never policy-gated.
        r = await f.request("POST", "/killswitch", body: #"{"engaged": false}"#)
        XCTAssertEqual(r.status, 200)
    }

    func testRateLimit() async throws {
        let clock = ManualClock()
        let f = try PipelineFixture(rateLimiter: TokenBucket(capacity: 3, refillPerSecond: 3, clock: { clock.now() }))
        for _ in 0..<3 {
            let ok = await f.request("POST", "/apps/activate", body: "{}")
            XCTAssertEqual(ok.status, 200)
        }
        var r = await f.request("POST", "/apps/activate", body: "{}")
        XCTAssertEqual(r.status, 429)
        XCTAssertEqual(r.headers["Retry-After"], "1")
        // Status endpoints are not rate limited.
        r = await f.request("GET", "/apps")
        XCTAssertEqual(r.status, 200)
        clock.advance(1)
        r = await f.request("POST", "/apps/activate", body: "{}")
        XCTAssertEqual(r.status, 200)
    }

    func testHandlerErrorsMapToJSON() async throws {
        let f = try PipelineFixture()
        let r = await f.request("POST", "/boom", body: "{}")
        XCTAssertEqual(r.status, 422)
        XCTAssertEqual(r.jsonBody?["error"], "boom")
        XCTAssertEqual(r.jsonBody?["message"], "exploded")
    }

    func testMkdirTraversalRejectedThroughPipeline() async throws {
        let f = try PipelineFixture()
        let r = await f.request("POST", "/files/mkdir", body: #"{"path":"~/Documents/../.ssh/jarvis-test"}"#)
        XCTAssertEqual(r.status, 403)
        XCTAssertEqual(r.jsonBody?["error"], "path_traversal")
        let r2 = await f.request("POST", "/files/mkdir", body: #"{"path":"/etc/jarvis-test"}"#)
        XCTAssertEqual(r2.status, 403)
        XCTAssertEqual(r2.jsonBody?["error"], "path_not_allowed")
    }

    func testAuditLogRedactsSensitiveFields() async throws {
        let f = try PipelineFixture()
        let secret = "my secret typed text"
        _ = await f.request("POST", "/input/type", body: #"{"text":"\#(secret)"}"#)
        _ = await f.request("GET", "/apps", auth: false)
        f.audit.flush()
        let log = try String(contentsOf: f.paths.auditLog, encoding: .utf8)
        XCTAssertFalse(log.contains(secret))
        XCTAssertFalse(log.contains(f.token))
        let lines = log.split(separator: "\n")
        XCTAssertEqual(lines.count, 2)
        let first = try JSON.parse(Data(lines[0].utf8))
        XCTAssertEqual(first["action"], "input.type")
        XCTAssertEqual(first["result"], "ok")
        XCTAssertEqual(first["params"]?["text"]?["redacted"], true)
        XCTAssertEqual(first["params"]?["text"]?["length"], .int(secret.count))
        let second = try JSON.parse(Data(lines[1].utf8))
        XCTAssertEqual(second["status"], 401)
    }

    func testSummarizeRedaction() {
        let s = AuditLogger.summarize(["text": "abc", "value": "xyz", "source": "tell app", "path": "/x", "nested": ["input": "hi"]])
        XCTAssertEqual(s["text"]?["length"], 3)
        XCTAssertEqual(s["value"]?["redacted"], true)
        XCTAssertEqual(s["source"]?["redacted"], true)
        XCTAssertEqual(s["path"], "/x")
        XCTAssertEqual(s["nested"]?["input"]?["redacted"], true)
    }
}

final class InputTests: XCTestCase {
    func testTypeSegmentsChunking() {
        let segs = InputHandlers.segments(for: String(repeating: "a", count: 45))
        XCTAssertEqual(segs, [.text(String(repeating: "a", count: 20)), .text(String(repeating: "a", count: 20)), .text("aaaaa")])

        let withNewline = InputHandlers.segments(for: "hi\nthere\tx")
        XCTAssertEqual(withNewline.count, 5)
        XCTAssertEqual(withNewline[0], .text("hi"))
        XCTAssertEqual(withNewline[2], .text("there"))

        // Emoji (2 UTF-16 units, multi-scalar clusters) are never split across chunks.
        let emoji = String(repeating: "👍🏽", count: 7)  // 4 UTF-16 units each
        for seg in InputHandlers.segments(for: emoji) {
            if case .text(let s) = seg {
                XCTAssertLessThanOrEqual(s.utf16.count, 20)
                XCTAssertTrue(s.allSatisfy { $0 == "👍🏽" })
            }
        }
    }

    func testKeyMap() {
        XCTAssertEqual(KeyMap.keyCode(for: "return", layout: [:])?.code, 36)
        XCTAssertEqual(KeyMap.keyCode(for: "Escape", layout: [:])?.code, 53)
        XCTAssertEqual(KeyMap.keyCode(for: "a", layout: [:])?.code, 0)
        XCTAssertEqual(KeyMap.keyCode(for: "A", layout: [:])?.extraFlags, .maskShift)
        // Layout map wins over the ANSI fallback (German QWERTZ: z is at ANSI y position 16).
        XCTAssertEqual(KeyMap.keyCode(for: "z", layout: ["z": 16])?.code, 16)
        XCTAssertNil(KeyMap.keyCode(for: "notakey", layout: [:]))
        XCTAssertEqual(KeyMap.modifier("CMD"), .maskCommand)
        XCTAssertEqual(KeyMap.modifier("option"), .maskAlternate)
        XCTAssertNil(KeyMap.modifier("hyper"))
    }
}
