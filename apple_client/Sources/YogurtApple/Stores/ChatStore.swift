import Foundation
import Combine

public struct ChatTurn: Codable, Identifiable, Equatable {
    public let id: String, userText: String
    public var response: ResponseProjection?
    public var historyText: String
    public var state: String
    public var assistantText: String { response?.text ?? historyText }
    public var terminal: Bool { response?.phase.terminal ?? ["completed", "aborted", "cancelled"].contains(state) }
}
public struct ChatConversation: Codable, Identifiable, Equatable {
    public let id: String
    public var turns: [ChatTurn]
    public var title: String { String(turns.first?.userText.prefix(48) ?? "New conversation") }
}
public struct ChatSnapshot: Codable, Equatable {
    public var conversations: [ChatConversation] = []
    public var pending: PendingSend?
    public var selectedID: String?
    public init() {}
}

@MainActor public final class ChatStore: ObservableObject {
    @Published public private(set) var snapshot: ChatSnapshot
    @Published public private(set) var notice: String?
    @Published public private(set) var sending = false
    private let api: GatewayAPI, disk: JSONFile<ChatSnapshot>
    private let device: DeviceInstanceStore
    private var streams: [String: Task<Void, Never>] = [:]
    private let pause: () async throws -> Void
    public init(api: GatewayAPI, disk: JSONFile<ChatSnapshot> = JSONFile(AppPaths.root.appendingPathComponent("chat-projection.json")),
                device: DeviceInstanceStore = DeviceInstanceStore(),
                pause: @escaping () async throws -> Void = { try await Task.sleep(nanoseconds: 700_000_000) }) {
        self.api = api; self.disk = disk; self.device = device; self.pause = pause
        snapshot = disk.load() ?? ChatSnapshot()
    }
    public var selected: ChatConversation? { snapshot.conversations.first { $0.id == snapshot.selectedID } }
    public var canSend: Bool { !sending && snapshot.pending == nil && !(selected?.turns.contains { !$0.terminal } ?? false) }
    private func save() { do { try disk.save(snapshot) } catch { notice = ClientError.storage.localizedDescription } }
    public func newConversation() { snapshot.selectedID = nil; notice = nil; save() }
    public func select(_ id: String) async { snapshot.selectedID = id; save(); await recover(id) }
    public func launch() async {
        notice = nil
        do {
            let refs = try await api.conversations()
            let allowed = Set(refs.map(\.id))
            snapshot.conversations.removeAll { !allowed.contains($0.id) }
            for ref in refs where !snapshot.conversations.contains(where: { $0.id == ref.id }) {
                snapshot.conversations.append(ChatConversation(id: ref.id, turns: []))
            }
            if snapshot.selectedID == nil || !allowed.contains(snapshot.selectedID ?? "") { snapshot.selectedID = refs.first?.id }
            if let id = snapshot.selectedID { await recover(id) }
            save()
            if snapshot.pending != nil { await retrySend() }
        } catch { notice = ClientError.safe(error).localizedDescription }
    }
    public func credentialsDidChange() async {
        await stopConsumers()
        await launch()
    }
    /// Preserve a usable cursor only for the same server turn/response. History
    /// prefixes never get concatenated with cached text. Terminal history wins.
    public func reconcile(_ history: ConversationHistory) {
        let cached = snapshot.conversations.first { $0.id == history.conversationID }
        let turns = history.turns.map { turn -> ChatTurn in
            if let rid = turn.responseID, !["completed", "aborted", "cancelled"].contains(turn.state) {
                let prior = cached?.turns.first { $0.id == turn.turnRef }?.response
                let response: ResponseProjection
                if let prior, prior.isResumable(for: rid), turn.responseText.hasPrefix(prior.text) {
                    response = prior
                } else {
                    response = ResponseProjection(responseID: rid)
                }
                return ChatTurn(id: turn.turnRef, userText: turn.text, response: response, historyText: "", state: turn.state)
            }
            return ChatTurn(id: turn.turnRef, userText: turn.text, response: nil, historyText: turn.responseText, state: turn.state)
        }
        let conversation = ChatConversation(id: history.conversationID, turns: turns)
        if let i = snapshot.conversations.firstIndex(where: { $0.id == history.conversationID }) { snapshot.conversations[i] = conversation }
        else { snapshot.conversations.append(conversation) }
        save()
    }
    public func recover(_ id: String) async {
        // Cancel only this client's consumers, never a Core operation.
        if let conversation = snapshot.conversations.first(where: { $0.id == id }) {
            for rid in conversation.turns.compactMap({ $0.response?.responseID }) {
                let task = streams.removeValue(forKey: rid); task?.cancel(); await task?.value
            }
        }
        do {
            reconcile(try await api.history(id))
            for response in snapshot.conversations.first(where: { $0.id == id })?.turns.compactMap({ $0.response }) ?? [] {
                startStream(response.responseID)
            }
        } catch { notice = ClientError.safe(error).localizedDescription }
    }
    @discardableResult public func send(_ text: String, surface: SurfaceType) async -> Bool {
        guard canSend else { return false }
        do {
            let pending = try PendingSend(TurnRequest(conversationID: snapshot.selectedID, text: text, surface: surface, device: device.loadOrCreate()))
            snapshot.pending = pending
            try disk.save(snapshot) // Persist exact payload before crossing the network boundary.
            await retrySend()
            return true
        } catch { notice = ClientError.safe(error).localizedDescription; return false }
    }
    public func retrySend() async {
        guard !sending, let pending = snapshot.pending else { return }
        sending = true; defer { sending = false }
        notice = nil
        do { try pending.validate(); try disk.save(snapshot) }
        catch { notice = ClientError.safe(error).localizedDescription; return }
        for index in 0..<3 {
            do {
                let accepted = try await api.send(pending)
                try accepted.validate()
                let cid = accepted.conversationID
                if !snapshot.conversations.contains(where: { $0.id == cid }) { snapshot.conversations.append(ChatConversation(id: cid, turns: [])) }
                guard let i = snapshot.conversations.firstIndex(where: { $0.id == cid }) else { return }
                if !snapshot.conversations[i].turns.contains(where: { $0.id == accepted.turnRef }) {
                    snapshot.conversations[i].turns.append(ChatTurn(id: accepted.turnRef, userText: pending.request.text,
                        response: ResponseProjection(responseID: accepted.responseID), historyText: "", state: "processing"))
                }
                if let t = snapshot.conversations[i].turns.firstIndex(where: { $0.id == accepted.turnRef }),
                   snapshot.conversations[i].turns[t].response == nil, !snapshot.conversations[i].turns[t].terminal {
                    snapshot.conversations[i].turns[t].response = ResponseProjection(responseID: accepted.responseID)
                }
                snapshot.pending = nil; snapshot.selectedID = cid; save()
                // Existing terminal history needs no stream; active/unknown acceptance does.
                if let turn = snapshot.conversations[i].turns.first(where: { $0.id == accepted.turnRef }), !turn.terminal {
                    startStream(accepted.responseID)
                }
                return
            } catch {
                let safe = ClientError.safe(error); notice = safe.localizedDescription
                guard safe.retryable, index < 2 else { return }
                do { try await pause() } catch { return }
            }
        }
    }
    public func discardPending() { guard !sending else { return }; snapshot.pending = nil; notice = nil; save() }
    private func projection(_ id: String) -> ResponseProjection? {
        snapshot.conversations.flatMap(\.turns).compactMap(\.response).first { $0.responseID == id }
    }
    public func receive(_ event: ResponseEvent) throws {
        for c in snapshot.conversations.indices {
            if let t = snapshot.conversations[c].turns.firstIndex(where: { $0.response?.responseID == event.responseID }),
               var response = snapshot.conversations[c].turns[t].response {
                try response.apply(event)
                snapshot.conversations[c].turns[t].response = response
                save() // Text and cursor are written atomically as one projection.
                return
            }
        }
        throw ClientError.invalidSequence
    }
    private func startStream(_ id: String) {
        guard streams[id] == nil, projection(id)?.phase.terminal == false else { return }
        streams[id] = Task { [weak self] in
            guard let self else { return }
            defer { self.streams[id] = nil }
            for index in 0..<4 {
                if Task.isCancelled { return }
                guard let projection = self.projection(id), !projection.phase.terminal else { return }
                do {
                    try await self.api.events(id, after: projection.lastEventID) { [weak self] event in
                        try Task.checkCancellation()
                        try self?.receive(event)
                    }
                    if self.projection(id)?.phase.terminal == true { return }
                    throw ClientError.disconnected
                } catch is CancellationError { return }
                  catch {
                    let safe = ClientError.safe(error)
                    self.notice = safe.localizedDescription
                    guard safe.retryable, index < 3 else { return }
                    do { try await self.pause() } catch { return }
                }
            }
        }
    }
    public func reconnect() { for response in snapshot.conversations.flatMap(\.turns).compactMap(\.response) where !response.phase.terminal { startStream(response.responseID) } }
    public func stopConsumers() async {
        let tasks = Array(streams.values); tasks.forEach { $0.cancel() }; for task in tasks { await task.value }; streams.removeAll()
    }
}
