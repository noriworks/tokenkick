import Foundation

/// The app JSON contract: every app-facing `tk` command answers with one of
/// these (see docs/TOKENKICK_COMMANDS.md, "App Mode And `tk app`").
public struct TKEnvelope<Payload: Decodable & Sendable>: Decodable, Sendable {
    public let schemaVersion: Int
    public let ok: Bool
    public let errorCode: String?
    public let message: String?
    public let warnings: [String]
    public let payload: Payload?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case ok
        case errorCode = "error_code"
        case message
        case warnings
        case payload
    }
}

public enum TKDecodingError: Error, CustomStringConvertible {
    case emptyOutput
    case invalidEnvelope(underlying: Error, raw: String)

    public var description: String {
        switch self {
        case .emptyOutput:
            return "tk produced no stdout output"
        case .invalidEnvelope(let underlying, let raw):
            let preview = raw.prefix(300)
            return "tk output did not decode as an app envelope: \(underlying) — output: \(preview)"
        }
    }
}

public enum TKJSONDecoding {
    public static func envelope<Payload: Decodable & Sendable>(
        _ payloadType: Payload.Type,
        from data: Data
    ) throws -> TKEnvelope<Payload> {
        guard !data.isEmpty else { throw TKDecodingError.emptyOutput }
        do {
            return try JSONDecoder().decode(TKEnvelope<Payload>.self, from: data)
        } catch {
            let raw = String(data: data, encoding: .utf8) ?? "<non-utf8>"
            throw TKDecodingError.invalidEnvelope(underlying: error, raw: raw)
        }
    }

    /// Legacy commands (`tk history`, `tk reset-log`, …) answer with bare
    /// payloads instead of the app envelope; decode them generically.
    public static func bareValue(from data: Data) throws -> TKJSONValue {
        guard !data.isEmpty else { throw TKDecodingError.emptyOutput }
        do {
            return try JSONDecoder().decode(TKJSONValue.self, from: data)
        } catch {
            let raw = String(data: data, encoding: .utf8) ?? "<non-utf8>"
            throw TKDecodingError.invalidEnvelope(underlying: error, raw: raw)
        }
    }

    public static func bare<Payload: Decodable & Sendable>(
        _ payloadType: Payload.Type,
        from data: Data
    ) throws -> Payload {
        guard !data.isEmpty else { throw TKDecodingError.emptyOutput }
        do {
            return try JSONDecoder().decode(Payload.self, from: data)
        } catch {
            let raw = String(data: data, encoding: .utf8) ?? "<non-utf8>"
            throw TKDecodingError.invalidEnvelope(underlying: error, raw: raw)
        }
    }
}
