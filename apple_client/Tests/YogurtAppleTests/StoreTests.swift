import XCTest
@testable import YogurtApple

@MainActor final class FakeAPI: GatewayAPI {
    var sends: [PendingSend] = []
    var failures: [ClientError] = []
    var histories: [String: ConversationHistory] = [:]
    var cursors: [Int] = []
    var streamCalls = 0
    var conversationFailure: ClientError?
    var eventHandler: ((String, Int, @escaping (ResponseEvent) async throws -> Void) async throws -> Void)?
    func send(_ pending: PendingSend) async throws -> TurnAccepted {
        sends.append(pending)
        if !failures.isEmpty { throw failures.removeFirst() }
        return accepted()
    }
    func conversations() async throws -> [ConversationRef] {
        if let conversationFailure { throw conversationFailure }
        return histories.keys.sorted().map { ConversationRef(conversationID: $0) }
    }
    func history(_ id: String) async throws -> ConversationHistory { histories[id]! }
    func events(_ id: String, after: Int, receive: @escaping (ResponseEvent) async throws -> Void) async throws {
        cursors.append(after); streamCalls += 1
        if let eventHandler { try await eventHandler(id, after, receive); return }
        if after == 0 { try await receive(event(1, .started)); try await receive(event(2, .delta, " partial ")); throw ClientError.disconnected }
        try await receive(event(2, .delta, " partial "))
        try await receive(event(3, .delta, "🙂\n")); try await receive(event(4, .completed))
    }
}
@MainActor final class StoreTests: XCTestCase {
    func file() -> JSONFile<ChatSnapshot> { JSONFile(FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("projection.json")) }
    func settle(_ predicate: @escaping @MainActor () -> Bool) async {
        for _ in 0..<100 {
            if predicate() { return }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
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
    func testStorageFailureCannotSendUnpersistedRetry() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try Data("file blocks directory".utf8).write(to: directory)
        defer { try? FileManager.default.removeItem(at: directory) }
        let api = FakeAPI()
        let store = ChatStore(api: api, disk: JSONFile(directory.appendingPathComponent("projection.json")), pause: {})
        let sent = await store.send("keep this draft", surface: .mac)
        XCTAssertFalse(sent)
        await store.retrySend()
        XCTAssertTrue(api.sends.isEmpty)
        XCTAssertNotNil(store.snapshot.pending)
    }
    func testRestartPreservesSequence12AndExactText() async throws {
        let disk = file(), api = FakeAPI()
        var projection = ResponseProjection(responseID: "r1")
        try projection.apply(event(1, .started))
        try projection.apply(event(2, .delta, "already streamed "))
        for n in 3...12 { try projection.apply(event(n, .delta, "")) }
        var snapshot = ChatSnapshot(); snapshot.selectedID = "c1"
        snapshot.conversations = [ChatConversation(id: "c1", turns: [ChatTurn(id: "t1", userText: "hello", response: projection, historyText: "", state: "processing")])]
        try disk.save(snapshot)
        api.histories["c1"] = ConversationHistory(conversationID: "c1", turns: [HistoryTurn(turnRef: "t1", text: "hello", responseID: "r1", responseText: "already streamed next", state: "processing")])
        api.eventHandler = { _, after, receive in
            XCTAssertEqual(after, 12)
            try await receive(event(12, .delta, "duplicate"))
            try await receive(event(13, .delta, "next")); try await receive(event(14, .completed))
        }
        let restored = ChatStore(api: api, disk: disk, pause: {})
        await restored.launch()
        await settle { restored.selected?.turns.first?.terminal == true }
        XCTAssertEqual(api.cursors, [12])
        XCTAssertEqual(restored.selected?.turns.first?.assistantText, "already streamed next")
        XCTAssertEqual(restored.selected?.turns.first?.response?.lastEventID, 14)
        await restored.stopConsumers()
    }
    func testMismatchedOrInvalidCachedProjectionReplaysFromZero() async throws {
        for (id, sequence, phase, text) in [("other", 12, "streaming", "wrong"), ("r1", -1, "streaming", "wrong"),
                                           ("r1", 12, "waiting", "wrong"), ("r1", 12, "completed", "wrong"),
                                           ("r1", 1, "streaming", "impossible") ] {
            let disk = file(), api = FakeAPI()
            let raw: [String: Any] = ["responseID": id, "lastEventID": sequence, "phase": phase, "text": text]
            let projection = try JSONDecoder().decode(ResponseProjection.self, from: JSONSerialization.data(withJSONObject: raw))
            var snapshot = ChatSnapshot(); snapshot.selectedID = "c1"
            snapshot.conversations = [ChatConversation(id: "c1", turns: [ChatTurn(id: "t1", userText: "hello", response: projection, historyText: "", state: "processing")])]
            try disk.save(snapshot)
            api.histories["c1"] = ConversationHistory(conversationID: "c1", turns: [HistoryTurn(turnRef: "t1", text: "hello", responseID: "r1", responseText: "server prefix", state: "processing")])
            let store = ChatStore(api: api, disk: disk, pause: {})
            await store.launch(); await settle { store.selected?.turns.first?.terminal == true }
            XCTAssertEqual(api.cursors, [0, 2]); XCTAssertEqual(store.selected?.turns.first?.assistantText, " partial 🙂\n")
            await store.stopConsumers()
        }
    }
    func testCredentialReloadWithoutActiveStreamReplacesNoticeAndHistory() async throws {
        let api = FakeAPI(); api.conversationFailure = .credentials
        let disk = file(), store = ChatStore(api: api, disk: file(), pause: {})
        await store.launch(); XCTAssertNotNil(store.notice)
        XCTAssertTrue(store.snapshot.conversations.isEmpty)
        api.conversationFailure = nil
        api.histories["c1"] = ConversationHistory(conversationID: "c1", turns: [HistoryTurn(turnRef: "t1", text: "server", responseID: "r1", responseText: "final", state: "completed")])
        await store.credentialsDidChange()
        XCTAssertNil(store.notice); XCTAssertEqual(store.selected?.turns.first?.assistantText, "final")
        XCTAssertTrue(api.sends.isEmpty); XCTAssertTrue(api.cursors.isEmpty)
        try disk.save(store.snapshot)
        let restarted = ChatStore(api: api, disk: disk, pause: {})
        api.histories["c1"] = ConversationHistory(conversationID: "c1", turns: [HistoryTurn(turnRef: "t1", text: "server", responseID: "r1", responseText: "authoritative final", state: "completed")])
        await restarted.launch()
        XCTAssertEqual(restarted.selected?.turns.first?.assistantText, "authoritative final")
        XCTAssertNil(restarted.selected?.turns.first?.response)
    }
}
