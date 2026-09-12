import Foundation

public enum ClientError: Error, LocalizedError, Equatable {
    case invalidPayload, invalidSequence, disconnected, credentials, storage, image, unsupportedDevice
    case server(Int, String)
    public var errorDescription: String? {
        switch self {
        case .invalidPayload: return "The gateway returned an invalid message."
        case .invalidSequence: return "Stream sequence mismatch. Reload conversation to recover."
        case .disconnected: return "Connection interrupted. Retry uses the same message."
        case .credentials: return "Set the gateway token in Settings."
        case .storage: return "Local storage is unavailable."
        case .image: return "This image is unavailable, too large, or invalid."
        case .unsupportedDevice: return "S3-A supports Mac and Apple simulators only."
        case .server(let status, let code): return "Gateway \(status): \(code)"
        }
    }
    public var retryable: Bool {
        switch self { case .disconnected: return true
        case .server(let status, let code): return status == 503 || status == 429 || code == "CONVERSATION_BUSY"
        default: return false }
    }
    static func safe(_ error: Error) -> ClientError { error as? ClientError ?? .disconnected }
}

public enum SurfaceType: String, Codable { case ios = "native_ios", ipad = "native_ipados", mac = "native_macos" }
public struct TurnRequest: Codable, Equatable {
    public let conversationID: String?
    public let clientMessageID: String
    public let text: String
    public let surfaceType: SurfaceType
    public let deviceInstanceID: String?
    enum CodingKeys: String, CodingKey {
        case conversationID = "conversation_id", clientMessageID = "client_message_id", text
        case surfaceType = "surface_type", deviceInstanceID = "device_instance_id"
    }
    public init(conversationID: String?, text: String, surface: SurfaceType, device: String? = nil) throws {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              text.unicodeScalars.count <= 16000, (device?.count ?? 0) <= 128,
              conversationID.map(validID) ?? true else { throw ClientError.invalidPayload }
        self.conversationID = conversationID; self.text = text; self.surfaceType = surface
        self.deviceInstanceID = device; self.clientMessageID = UUID().uuidString
    }
}
func validID(_ value: String) -> Bool {
    !value.isEmpty && value.count <= 128 && value.utf8.allSatisfy {
        (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 95
    }
}
public struct PendingSend: Codable, Equatable {
    public let request: TurnRequest
    public let body: Data
    func validate() throws {
        let canonical = try PendingSend(request)
        guard canonical.body == body, validID(request.clientMessageID),
              request.conversationID.map(validID) ?? true,
              !request.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              request.text.unicodeScalars.count <= 16000, (request.deviceInstanceID?.count ?? 0) <= 128
        else { throw ClientError.invalidPayload }
    }
    public init(_ request: TurnRequest) throws {
        self.request = request
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        self.body = try encoder.encode(request)
    }
}
public struct TurnAccepted: Codable, Equatable {
    public let conversationID: String, turnRef: String, responseID: String, eventStreamURL: String, admission: String
    enum CodingKeys: String, CodingKey {
        case conversationID = "conversation_id", turnRef = "turn_ref", responseID = "response_id"
        case eventStreamURL = "event_stream_url", admission
    }
    func validate() throws {
        guard validID(conversationID), validID(turnRef), validID(responseID),
            eventStreamURL == "/v1/native/responses/\(responseID)/events",
            ["NEWLY_ADMITTED", "SAME_LOGICAL_TURN"].contains(admission) else { throw ClientError.invalidPayload }
    }
}
public struct ConversationRef: Codable, Identifiable, Equatable {
    public let conversationID: String
    public var id: String { conversationID }
    enum CodingKeys: String, CodingKey { case conversationID = "conversation_id" }
}
public struct HistoryTurn: Codable, Equatable {
    public let turnRef: String, text: String, responseID: String?, responseText: String, state: String
    enum CodingKeys: String, CodingKey {
        case turnRef = "turn_ref", text, responseID = "response_id", responseText = "response_text", state
    }
}
public struct ConversationHistory: Codable, Equatable {
    public let conversationID: String, turns: [HistoryTurn]
    enum CodingKeys: String, CodingKey { case conversationID = "conversation_id", turns }
}
public struct Health: Decodable { public let status: String; public let model_token_streaming: Bool }
public struct ResponseEvent: Codable, Equatable {
    public enum Kind: String, Codable {
        case started = "response.started", delta = "response.delta", text = "response.text"
        case completed = "response.completed", failed = "response.failed", cancelled = "response.cancelled"
    }
    public let responseID: String, sequence: Int, event: Kind, text: String?, reason: String?
    enum CodingKeys: String, CodingKey { case responseID = "response_id", sequence, event, text, reason }
}
