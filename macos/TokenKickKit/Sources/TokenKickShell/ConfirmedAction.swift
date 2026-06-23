import Foundation
import TokenKickKit

/// One definition for every risky or quota-consuming action (UX plan §7,
/// §16): what happens, what it costs, what else it touches, and the exact
/// `tk` invocation. Confirmation UX stays uniform by construction.
public struct ConfirmedAction: Identifiable, Equatable, Sendable {
    public let id: String
    /// Sheet title, e.g. `Kick "codex (reserve)"?`
    public let title: String
    /// One sentence: what will happen.
    public let explanation: String
    /// The quota cost disclosure; nil only for non-quota actions.
    public let costLine: String?
    /// Extra things the user should know before confirming
    /// (stale status, clearing a planned kick, …).
    public let disclosures: [String]
    /// The account (or other scope) this acts on.
    public let scopeLabel: String
    /// Verb button title, e.g. "Kick Now". Never OK/Yes.
    public let verb: String
    /// Destructive styling for the verb button. Cancel stays the default
    /// (Return) either way — the safe choice always has the keyboard.
    public let isDestructive: Bool
    /// The exact bundled-tk invocation the action performs when confirmed.
    public let tkArguments: [String]

    public init(
        id: String,
        title: String,
        explanation: String,
        costLine: String?,
        disclosures: [String],
        scopeLabel: String,
        verb: String,
        isDestructive: Bool,
        tkArguments: [String]
    ) {
        self.id = id
        self.title = title
        self.explanation = explanation
        self.costLine = costLine
        self.disclosures = disclosures
        self.scopeLabel = scopeLabel
        self.verb = verb
        self.isDestructive = isDestructive
        self.tkArguments = tkArguments
    }

    static func providerDisplayName(_ provider: String) -> String {
        switch provider {
        case "codex": return "Codex"
        case "claude": return "Claude"
        case "gemini": return "Gemini"
        default: return provider.capitalized
        }
    }

    /// The kick action for one account, with disclosures derived from the
    /// same snapshot the user is looking at.
    public static func kick(
        row: SnapshotAccountRow,
        pendingKicks: [PendingKickRow] = [],
        now: Date = Date()
    ) -> ConfirmedAction {
        let provider = providerDisplayName(row.provider)
        var disclosures: [String] = []
        if row.stale {
            disclosures.append(
                "Status data is stale; the window may have moved since it was read."
            )
        }
        if let pending = pendingKicks.first(where: { $0.accountLabel == row.label }) {
            let when = RelativeTimeText.until(pending.kickAt, now: now)
            disclosures.append(
                "This clears the planned kick (\(when)) and kicks immediately."
            )
        }
        return ConfirmedAction(
            id: "kick:\(row.label)",
            title: "Kick \"\(row.label)\"?",
            explanation: "Sends a tiny prompt so the quota window starts now instead of on first use.",
            costLine: "Uses a small amount of \(provider) quota to start the fresh window.",
            disclosures: disclosures,
            scopeLabel: row.label,
            verb: "Kick Now",
            isDestructive: false,
            tkArguments: ["kick", row.label, "--json-output", "--yes"]
        )
    }
}

/// Verified outcome of a kick, worded truthfully (UX plan §7): the app
/// never claims success the core did not confirm.
public enum KickOutcome: Equatable, Sendable {
    case confirmed(message: String)
    case unconfirmed(message: String)
    case failed(message: String)
    case skipped(message: String)

    public static func from(envelope: TKEnvelope<TKKickResultPayload>) -> KickOutcome {
        let payload = envelope.payload
        let coreMessage = envelope.message
        if envelope.ok {
            switch payload?.decision {
            case "attempted":
                switch payload?.result {
                case "confirmed":
                    return .confirmed(message: "Kicked — provider confirmed the new window.")
                case "unconfirmed":
                    return .unconfirmed(
                        message: "Attempted — "
                            + (coreMessage ?? "the provider has not confirmed the new window yet.")
                    )
                default:
                    return .failed(message: coreMessage ?? "The kick attempt failed.")
                }
            case "skipped":
                return .skipped(message: coreMessage ?? "Nothing to kick right now.")
            case "would_kick":
                return .skipped(message: "Preview only — no kick was sent.")
            default:
                return .failed(message: coreMessage ?? "Unexpected kick response.")
            }
        }
        return .failed(message: coreMessage ?? "The kick attempt failed.")
    }

    public var message: String {
        switch self {
        case .confirmed(let message),
             .unconfirmed(let message),
             .failed(let message),
             .skipped(let message):
            return message
        }
    }

    public var symbolName: String {
        switch self {
        case .confirmed: return "checkmark.circle.fill"
        case .unconfirmed: return "clock.badge.questionmark"
        case .failed: return "xmark.circle.fill"
        case .skipped: return "minus.circle"
        }
    }
}
