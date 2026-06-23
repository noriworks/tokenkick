import Foundation

/// One record from the `tk app setup` JSON-lines stream. Progress records
/// carry `event` plus event-specific fields; the terminal record additionally
/// embeds the app envelope keys (`ok`, `error_code`, `warnings`, `payload`).
public struct TKSetupEvent: Decodable, Sendable {
    public let schemaVersion: Int
    public let event: String
    public let message: String?
    public let summary: String?
    public let accounts: Int?
    public let path: String?
    public let version: String?
    public let ok: Bool?
    public let errorCode: String?
    public let warnings: [String]?
    public let payload: TKJSONValue?

    /// Terminal records (`setup_completed`/`setup_failed`/`setup_cancelled`)
    /// are the only ones carrying envelope keys.
    public var isTerminal: Bool { ok != nil }

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case event
        case message
        case summary
        case accounts
        case path
        case version
        case ok
        case errorCode = "error_code"
        case warnings
        case payload
    }
}

public enum TKSetupStreamError: Error {
    case invalidLine(index: Int, underlying: Error, line: String)
    case missingTerminalRecord
}

public enum TKSetupStream {
    /// Decode a complete JSON-lines stream. For live streaming the app will
    /// feed lines one at a time through `event(fromLine:index:)`.
    public static func events(from data: Data) throws -> [TKSetupEvent] {
        let text = String(decoding: data, as: UTF8.self)
        var events: [TKSetupEvent] = []
        for (index, rawLine) in text.split(separator: "\n", omittingEmptySubsequences: true).enumerated() {
            events.append(try event(fromLine: String(rawLine), index: index))
        }
        return events
    }

    public static func event(fromLine line: String, index: Int = 0) throws -> TKSetupEvent {
        do {
            return try JSONDecoder().decode(TKSetupEvent.self, from: Data(line.utf8))
        } catch {
            throw TKSetupStreamError.invalidLine(index: index, underlying: error, line: line)
        }
    }

    public static func terminalEvent(in events: [TKSetupEvent]) throws -> TKSetupEvent {
        guard let terminal = events.last, terminal.isTerminal else {
            throw TKSetupStreamError.missingTerminalRecord
        }
        return terminal
    }
}
