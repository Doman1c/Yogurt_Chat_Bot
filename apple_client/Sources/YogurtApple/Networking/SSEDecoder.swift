import Foundation

/// Bounded UTF-8 SSE framing. Packet/line boundaries never become text boundaries.
public struct SSEDecoder {
    private var line = Data(), fields: [String: String] = [:], payload = "", frameBytes = 0
    public init() {}
    public mutating func feed(_ bytes: Data) throws -> [ResponseEvent] {
        var output: [ResponseEvent] = []
        for byte in bytes {
            frameBytes += 1
            guard frameBytes <= 131072, line.count < 65536 else { throw ClientError.invalidPayload }
            if byte == 10 {
                if line.last == 13 { line.removeLast() }
                guard let string = String(data: line, encoding: .utf8) else { throw ClientError.invalidPayload }
                line.removeAll(keepingCapacity: true)
                if string.isEmpty {
                    if !payload.isEmpty {
                        guard let eventID = fields["id"], eventID.utf8.allSatisfy({ (48...57).contains($0) }),
                              let sequence = Int(eventID), sequence > 0,
                              let data = payload.data(using: .utf8),
                              let event = try? JSONDecoder().decode(ResponseEvent.self, from: data),
                              event.sequence == sequence, event.event.rawValue == fields["event"], validID(event.responseID)
                        else { throw ClientError.invalidPayload }
                        output.append(event)
                    }
                    fields.removeAll(); payload = ""; frameBytes = 0
                } else if !string.hasPrefix(":") {
                    let parts = string.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
                    let name = String(parts[0]); var value = parts.count == 2 ? String(parts[1]) : ""
                    if value.hasPrefix(" ") { value.removeFirst() }
                    if name == "data" { payload += (payload.isEmpty ? "" : "\n") + value }
                    else if name == "id" || name == "event" {
                        guard fields[name] == nil else { throw ClientError.invalidPayload }; fields[name] = value
                    }
                }
            } else { line.append(byte) }
        }
        return output
    }
    public func finish() throws {
        guard line.isEmpty, payload.isEmpty, fields.isEmpty else { throw ClientError.disconnected }
    }
}

public struct ResponseProjection: Codable, Equatable {
    public enum Phase: String, Codable { case waiting, streaming, completed, failed, cancelled
        public var terminal: Bool { [.completed, .failed, .cancelled].contains(self) }
    }
    public let responseID: String
    public private(set) var lastEventID = 0
    public private(set) var text = ""
    public private(set) var phase: Phase = .waiting
    public init(responseID: String) { self.responseID = responseID }
    public mutating func apply(_ event: ResponseEvent) throws {
        guard event.responseID == responseID, event.sequence > 0 else { throw ClientError.invalidSequence }
        if event.sequence <= lastEventID { return }
        guard lastEventID < Int.max, event.sequence == lastEventID + 1, !phase.terminal else { throw ClientError.invalidSequence }
        switch event.event {
        case .started:
            guard lastEventID == 0, event.text == nil else { throw ClientError.invalidSequence }; phase = .streaming
        case .delta, .text:
            guard phase == .streaming, let delta = event.text else { throw ClientError.invalidPayload }
            text += delta // Exact provider/server text; no trimming, normalization, or animation.
        case .completed, .failed, .cancelled:
            guard phase == .streaming, event.text == nil else { throw ClientError.invalidSequence }
            phase = event.event == .completed ? .completed : event.event == .failed ? .failed : .cancelled
        }
        lastEventID = event.sequence
    }
}
