import Foundation

/// Payload of `tk kick LABEL --json-output`.
///
/// `decision` values: `skipped` (eligibility said no, with `reasonCode`),
/// `would_kick` (dry run), `attempted` (a kick ran; `result` carries the
/// verified outcome), `confirmation_required` (needs `--yes`), `stopped`
/// (hard error such as account not found).
public struct TKKickResultPayload: Decodable, Sendable {
    public let action: String
    public let account: String
    public let dryRun: Bool
    public let decision: String
    public let reasonCode: String?
    public let kicked: Bool?
    /// `confirmed` | `unconfirmed` | `failed` — only present for `attempted`.
    /// The core never claims success it did not verify.
    public let result: String?
    public let kickType: String?
    public let confirmations: [String]?
    public let clearsPendingKick: Bool?
    public let event: TKJSONValue?

    enum CodingKeys: String, CodingKey {
        case action
        case account
        case dryRun = "dry_run"
        case decision
        case reasonCode = "reason_code"
        case kicked
        case result
        case kickType = "kick_type"
        case confirmations
        case clearsPendingKick = "clears_pending_kick"
        case event
    }
}

extension TKClient {
    /// Run one confirmed kick. The caller is responsible for having shown
    /// the user a confirmation first — this always passes `--yes`.
    public func kick(label: String) async throws -> TKEnvelope<TKKickResultPayload> {
        try await envelope(
            TKKickResultPayload.self,
            arguments: ["kick", label, "--json-output", "--yes"]
        )
    }

    /// Preview a kick without acting (`--dry-run`).
    public func kickPreview(label: String) async throws -> TKEnvelope<TKKickResultPayload> {
        try await envelope(
            TKKickResultPayload.self,
            arguments: ["kick", label, "--dry-run", "--json-output"]
        )
    }
}
