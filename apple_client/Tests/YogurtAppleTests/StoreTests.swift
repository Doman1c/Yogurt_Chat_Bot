import XCTest
@testable import YogurtApple

@MainActor final class FakeAPI: GatewayAPI {
    var sends: [PendingSend] = []
    var failures: [ClientError] = []
    var histories: [String: ConversationHistory] = [:]
    var cursors: [Int] = []
    var streamCalls = 0
    func send(_ pending: PendingSend) async throws -> TurnAccepted {
        sends.append(pending)
        if !failures.isEmpty { throw failures.removeFirst() }
        return accepted()
    }
    func conversations() async throws -> [ConversationRef] { histories.keys.sorted().map { ConversationRef(conversationID: $0) } }
    func history(_ id: String) async throws -> ConversationHistory { histories[id]! }
    func events(_ id: String, after: Int, receive: @escaping (ResponseEvent) async throws -> Void) async throws {
        cursors.append(after); streamCalls += 1
        if after == 0 { try await receive(event(1, .started)); try await receive(event(2, .delta, " partial ")); throw ClientError.disconnected }
        try await receive(event(2, .delta, " partial "))
        try await receive(event(3, .delta, "🙂\n")); try await receive(event(4, .completed))
    }
}
@MainActor final class StoreTests: XCTestCase {
    func file() -> JSONFile<ChatSnapshot> { JSONFile(FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("projection.json")) }
    func settle(_ predicate: () -> Bool) async {
        for _ in 0..<100 where !predicate() { try? await Task.sleep(nanoseconds: 10_000_000) }
    }
    func test503AndUnknownOutcomeKeepOneIDAndBody() async throws {
        let api = FakeAPI(); api.failures = [.server(503, "CORE_UNAVAILABLE"), .disconnected]
        let store = ChatStore(api: api, disk: file(), pause: {})
        await store.send(" hello ", surface: .mac)
        await settle { store.selected?.turns.first?.terminal == true }
        XCTAssertEqual(api.sends.count, 3)
        XCTAssertEqual(Set(api.sends.map { $0.request.clientMessageID }).count, 1)
        XCTAssertTrue(api.sends.allSatisfy { $0.body == api.sends[0].body })
        XCTAssertEqual(store.selected?.turns.first?.assistantText, " partial 🙂\n")
        XCTAssertEqual(api.cursors, [0, 2])
        await store.stopConsumers()
    }
    func testConflictAndTerminatedOutcomeDoNotGenerateNewID() async throws {
        for code in ["CLIENT_MESSAGE_CONFLICT", "TURN_TERMINATED_BEFORE_RESPONSE"] {
            let api = FakeAPI(); api.failures = [.server(409, code), .server(409, code)]
            let store = ChatStore(api: api, disk: file(), pause: {})
            await store.send("hello", surface: .mac)
            let id = store.snapshot.pending?.request.clientMessageID
            await store.retrySend()
            XCTAssertEqual(api.sends.count, 2)
            XCTAssertEqual(store.snapshot.pending?.request.clientMessageID, id)
            XCTAssertEqual(api.sends[0], api.sends[1]); XCTAssertTrue(store.snapshot.conversations.isEmpty)
        }
    }
    func testRestartHistoryRebuildAndPendingRetry() async throws {
        let disk = file(), api = FakeAPI()
        api.failures = [.disconnected, .disconnected, .disconnected]
        let first = ChatStore(api: api, disk: disk, pause: {})
        await first.send("hello", surface: .mac)
        let pending = try XCTUnwrap(first.snapshot.pending)
        let restored = ChatStore(api: api, disk: disk, pause: {})
        XCTAssertEqual(restored.snapshot.pending, pending)
        api.histories["c1"] = ConversationHistory(conversationID: "c1", turns: [HistoryTurn(turnRef: "t1", text: "hello", responseID: "r1", responseText: " partial ", state: "processing")])
        await restored.launch()
        await settle { restored.selected?.turns.first?.terminal == true }
        XCTAssertEqual(api.sends.last, pending)
        XCTAssertEqual(restored.selected?.turns.count, 1)
        XCTAssertEqual(restored.selected?.turns.first?.assistantText, " partial 🙂\n")
        await restored.stopConsumers()
    }
    func testTerminalHistoryReplacesCachedProjection() async {
        let store = ChatStore(api: FakeAPI(), disk: file(), pause: {})
        store.reconcile(ConversationHistory(conversationID: "c1", turns: [HistoryTurn(turnRef: "t1", text: "server user text", responseID: "r1", responseText: "authoritative final  ", state: "completed")]))
        XCTAssertEqual(store.snapshot.conversations[0].turns[0].assistantText, "authoritative final  ")
        XCTAssertNil(store.snapshot.conversations[0].turns[0].response)
    }
}
