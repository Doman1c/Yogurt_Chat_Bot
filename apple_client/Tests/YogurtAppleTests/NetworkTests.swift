import XCTest
@testable import YogurtApple

final class MemoryToken: TokenStore {
    let value = "TEST_ONLY_NOT_A_REAL_CREDENTIAL_000000"
    func read() throws -> String { value }
    func save(_ token: String) throws {}
}
final class MockTransport: HTTPTransport {
    var requests: [URLRequest] = []
    var status = 202
    var response = Data()
    var fragments: [Data] = []
    var failure: Error?
    func data(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        if let failure { throw failure }
        return (response, HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
    }
    func stream(_ request: URLRequest, receive: @escaping (Data) async throws -> Void) async throws {
        requests.append(request)
        for data in fragments { try await receive(data) }
        if let failure { throw failure }
    }
}
func accepted() -> TurnAccepted {
    TurnAccepted(conversationID: "c1", turnRef: "t1", responseID: "r1", eventStreamURL: "/v1/native/responses/r1/events", admission: "NEWLY_ADMITTED")
}
func event(_ n: Int, _ kind: ResponseEvent.Kind, _ text: String? = nil) -> ResponseEvent {
    ResponseEvent(responseID: "r1", sequence: n, event: kind, text: text, reason: nil)
}
func frame(_ e: ResponseEvent) throws -> Data {
    Data("id: \(e.sequence)\nevent: \(e.event.rawValue)\ndata: \(String(decoding: try JSONEncoder().encode(e), as: UTF8.self))\n\n".utf8)
}
final class NetworkTests: XCTestCase {
    func testTypedAcceptanceExactPayloadAndNoPresentationFields() async throws {
        let transport = MockTransport(); transport.response = try JSONEncoder().encode(accepted())
        let api = NativeGateway(token: MemoryToken(), transport: transport)
        let pending = try PendingSend(TurnRequest(conversationID: nil, text: " hi  \n🙂", surface: .mac))
        let result = try await api.send(pending)
        XCTAssertEqual(result, accepted())
        XCTAssertEqual(transport.requests[0].httpBody, pending.body)
        XCTAssertEqual(transport.requests[0].url?.absoluteString, "http://127.0.0.1:17622/v1/native/turns")
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: pending.body) as? [String: Any])
        XCTAssertEqual(Set(body.keys), ["client_message_id", "text", "surface_type"])
        XCTAssertEqual(body["text"] as? String, " hi  \n🙂")
        XCTAssertFalse(String(decoding: pending.body, as: UTF8.self).contains(MemoryToken().value))
    }
    func testFragmentedSSEExactWhitespaceEmojiAndTerminals() throws {
        for terminal in [ResponseEvent.Kind.completed, .failed, .cancelled] {
            var parser = SSEDecoder(), projection = ResponseProjection(responseID: "r1")
            let expected = "  hello\n中🙂  "
            let wire = try [event(1, .started), event(2, .delta, "  hello"), event(3, .delta, "\n中🙂  "), event(4, terminal)].reduce(Data()) { try $0 + frame($1) }
            for byte in wire { for value in try parser.feed(Data([byte])) { try projection.apply(value) } }
            try parser.finish()
            XCTAssertEqual(projection.text, expected); XCTAssertEqual(projection.lastEventID, 4)
            XCTAssertTrue(projection.phase.terminal)
        }
    }
    func testDuplicatesGapsAndPostTerminalEvents() throws {
        var value = ResponseProjection(responseID: "r1")
        try value.apply(event(1, .started)); try value.apply(event(2, .delta, "one "))
        try value.apply(event(2, .delta, "one ")); try value.apply(event(1, .started))
        XCTAssertEqual(value.text, "one ")
        let previous = value
        XCTAssertThrowsError(try value.apply(event(4, .delta, "gap")))
        XCTAssertEqual(value, previous)
        try value.apply(event(3, .completed))
        XCTAssertThrowsError(try value.apply(event(4, .delta, "late")))
        XCTAssertThrowsError(try value.apply(event(0, .started)))
    }
    func testResumeHeaderAndReplayHasNoDuplicates() async throws {
        let transport = MockTransport()
        transport.fragments = try [event(2, .delta, "already"), event(3, .delta, " tail"), event(4, .completed)].map(frame)
        var projection = ResponseProjection(responseID: "r1")
        try projection.apply(event(1, .started)); try projection.apply(event(2, .delta, "already"))
        try await NativeGateway(token: MemoryToken(), transport: transport).events("r1", after: 2) { try projection.apply($0) }
        XCTAssertEqual(projection.text, "already tail")
        XCTAssertEqual(transport.requests[0].value(forHTTPHeaderField: "Last-Event-ID"), "2")
    }
    func testMalformedAndBoundedSSEFailsClosed() throws {
        for bytes in [Data("id: 1\nevent: response.started\ndata: {}\n\n".utf8), Data(repeating: 65, count: 65537), Data([255, 10]),
                      Data("id: 2\nevent: response.delta\ndata: nope\n\n".utf8)] {
            var parser = SSEDecoder(); XCTAssertThrowsError(try parser.feed(bytes))
        }
        var parser = SSEDecoder(); _ = try parser.feed(Data("data: unfinished".utf8)); XCTAssertThrowsError(try parser.finish())
    }
    func testMalformedAcceptanceAndSecretSafeErrors() async throws {
        let transport = MockTransport(); transport.response = Data("{}".utf8)
        let api = NativeGateway(token: MemoryToken(), transport: transport)
        let pending = try PendingSend(TurnRequest(conversationID: nil, text: "hello", surface: .ios))
        do { _ = try await api.send(pending); XCTFail() } catch { XCTAssertEqual(error as? ClientError, .invalidPayload) }
        transport.failure = NSError(domain: "test", code: 1, userInfo: [NSLocalizedDescriptionKey: MemoryToken().value])
        do { _ = try await api.send(pending); XCTFail() } catch { XCTAssertFalse(error.localizedDescription.contains(MemoryToken().value)) }
        let error = NativeGateway.serverError(503, Data("{\"code\":\"\(MemoryToken().value)\"}".utf8))
        XCTAssertFalse(error.localizedDescription.contains(MemoryToken().value))
    }
    func testPendingRoundTripPreservesExactRetryBytes() throws {
        let pending = try PendingSend(TurnRequest(conversationID: nil, text: "same\n payload", surface: .ipad))
        let restored = try JSONDecoder().decode(PendingSend.self, from: JSONEncoder().encode(pending))
        try restored.validate(); XCTAssertEqual(restored, pending)
    }
    func testTypedHistoryHealthAndInvalidHistory() async throws {
        let transport = MockTransport(); transport.status = 200
        let api = NativeGateway(token: MemoryToken(), transport: transport)
        transport.response = Data("[{\"conversation_id\":\"c1\"}]".utf8)
        let refs = try await api.conversations(); XCTAssertEqual(refs.map(\.id), ["c1"])
        transport.response = Data("{\"status\":\"ok\",\"model_token_streaming\":true}".utf8)
        let health = try await api.health(); XCTAssertEqual(health.status, "ok"); XCTAssertTrue(health.model_token_streaming)
        let history = ConversationHistory(conversationID: "c1", turns: [HistoryTurn(turnRef: "t1", text: "user", responseID: "r1", responseText: " exact  \n", state: "completed")])
        transport.response = try JSONEncoder().encode(history)
        let fetched = try await api.history("c1"); XCTAssertEqual(fetched, history)
        do { _ = try await api.history("c2"); XCTFail() } catch { XCTAssertEqual(error as? ClientError, .invalidPayload) }
    }
    func testAppleURLSessionActualLoopbackSSE() async throws {
        guard ProcessInfo.processInfo.environment["YOGURT_RUN_LOOPBACK_TESTS"] == "1" else { throw XCTSkip("Start the local fixture to enable URLSession integration") }
        let api = NativeGateway(token: MemoryToken())
        let pending = try PendingSend(TurnRequest(conversationID: nil, text: "loopback test", surface: .mac))
        let accepted = try await api.send(pending); XCTAssertEqual(accepted.responseID, "r1")
        var value = ResponseProjection(responseID: "r1")
        var firstOutput: Date?
        try await api.events("r1", after: 0) { event in
            try value.apply(event)
            if event.event == .delta && firstOutput == nil { firstOutput = Date() }
        }
        XCTAssertEqual(value.text, " live 🙂\n  "); XCTAssertEqual(value.lastEventID, 4)
        XCTAssertGreaterThan(Date().timeIntervalSince(try XCTUnwrap(firstOutput)), 0.04)
        var resumed: [Int] = []
        try await api.events("r1", after: 2) { resumed.append($0.sequence) }
        XCTAssertEqual(resumed, [3, 4])
    }

}
