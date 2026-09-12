import Foundation

public protocol HTTPTransport {
    func data(_ request: URLRequest) async throws -> (Data, HTTPURLResponse)
    func stream(_ request: URLRequest, receive: @escaping (Data) async throws -> Void) async throws
}
private final class NoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}
public final class AppleTransport: HTTPTransport {
    private let session: URLSession
    public init() {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.urlCache = nil
        config.timeoutIntervalForRequest = 120; config.timeoutIntervalForResource = 86400
        config.connectionProxyDictionary = [:]
        session = URLSession(configuration: config, delegate: NoRedirect(), delegateQueue: nil)
    }
    public func data(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw ClientError.invalidPayload }
        var data = Data()
        for try await byte in bytes { guard data.count < 8_000_000 else { throw ClientError.invalidPayload }; data.append(byte) }
        return (data, http)
    }
    public func stream(_ request: URLRequest, receive: @escaping (Data) async throws -> Void) async throws {
        let (bytes, response) = try await session.bytes(for: request)
        guard let http = response as? HTTPURLResponse else { throw ClientError.invalidPayload }
        guard http.statusCode == 200 else {
            var body = Data()
            for try await byte in bytes {
                try Task.checkCancellation()
                guard body.count < 4096 else { throw NativeGateway.serverError(http.statusCode, Data()) }
                body.append(byte)
            }
            throw NativeGateway.serverError(http.statusCode, body)
        }
        guard http.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("text/event-stream") == true
        else { throw ClientError.invalidPayload }
        var line = Data()
        for try await byte in bytes {
            try Task.checkCancellation()
            guard line.count < 65536 else { throw ClientError.invalidPayload }
            line.append(byte)
            if byte == 10 { try await receive(line); line.removeAll(keepingCapacity: true) }
        }
        if !line.isEmpty { try await receive(line) }
    }
}
public protocol GatewayAPI {
    func send(_ pending: PendingSend) async throws -> TurnAccepted
    func conversations() async throws -> [ConversationRef]
    func history(_ id: String) async throws -> ConversationHistory
    func events(_ responseID: String, after: Int, receive: @escaping (ResponseEvent) async throws -> Void) async throws
}
public final class NativeGateway: GatewayAPI {
    private let token: TokenStore, transport: HTTPTransport
    private let base = URL(string: "http://127.0.0.1:17622")!
    public init(token: TokenStore, transport: HTTPTransport = AppleTransport()) { self.token = token; self.transport = transport }
    private func request(_ path: String) throws -> URLRequest {
        #if os(iOS) && !targetEnvironment(simulator)
        throw ClientError.unsupportedDevice
        #else
        var request = URLRequest(url: base.appendingPathComponent(path))
        request.setValue("Bearer " + (try token.read()), forHTTPHeaderField: "Authorization")
        request.cachePolicy = .reloadIgnoringLocalCacheData
        return request
        #endif
    }
    static func serverError(_ status: Int, _ data: Data) -> ClientError {
        let known = ["UNAUTHORIZED", "NOT_FOUND", "CORE_UNAVAILABLE", "INVALID_REQUEST", "INPUT_TOO_LARGE",
                     "CLIENT_MESSAGE_CONFLICT", "CONVERSATION_BUSY", "TURN_REJECTED", "TURN_TERMINATED_BEFORE_RESPONSE"]
        let code = (try? JSONSerialization.jsonObject(with: data) as? [String: String])?["code"] ?? "UNKNOWN"
        return .server(status, known.contains(code) ? code : "UNKNOWN")
    }
    private func decode<T: Decodable>(_ type: T.Type, _ request: URLRequest, status: Int = 200) async throws -> T {
        do {
            let (data, response) = try await transport.data(request)
            guard response.statusCode == status else { throw Self.serverError(response.statusCode, data) }
            guard let value = try? JSONDecoder().decode(type, from: data) else { throw ClientError.invalidPayload }
            return value
        } catch is CancellationError { throw CancellationError() }
          catch { throw ClientError.safe(error) }
    }
    public func send(_ pending: PendingSend) async throws -> TurnAccepted {
        try pending.validate()
        var req = try request("v1/native/turns"); req.httpMethod = "POST"; req.httpBody = pending.body
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let result = try await decode(TurnAccepted.self, req, status: 202); try result.validate(); return result
    }
    public func conversations() async throws -> [ConversationRef] {
        let value = try await decode([ConversationRef].self, request("v1/native/conversations"))
        guard value.allSatisfy({ validID($0.id) }) else { throw ClientError.invalidPayload }; return value
    }
    public func history(_ id: String) async throws -> ConversationHistory {
        guard validID(id) else { throw ClientError.invalidPayload }
        let value = try await decode(ConversationHistory.self, request("v1/native/conversations/\(id)"))
        guard value.conversationID == id, Set(value.turns.map(\.turnRef)).count == value.turns.count,
              value.turns.allSatisfy({ validID($0.turnRef) && ($0.responseID.map(validID) ?? true) &&
                  ["input_committed", "processing", "completed", "aborted", "cancelled"].contains($0.state) })
        else { throw ClientError.invalidPayload }; return value
    }
    public func health() async throws -> Health { try await decode(Health.self, request("v1/native/health")) }
    public func events(_ responseID: String, after: Int, receive: @escaping (ResponseEvent) async throws -> Void) async throws {
        guard validID(responseID), after >= 0 else { throw ClientError.invalidPayload }
        var req = try request("v1/native/responses/\(responseID)/events")
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        req.setValue(String(after), forHTTPHeaderField: "Last-Event-ID")
        var parser = SSEDecoder()
        do {
            try await transport.stream(req) { data in
                for event in try parser.feed(data) { try await receive(event) }
            }
            try parser.finish()
        } catch is CancellationError { throw CancellationError() }
          catch { throw ClientError.safe(error) }
    }
}
