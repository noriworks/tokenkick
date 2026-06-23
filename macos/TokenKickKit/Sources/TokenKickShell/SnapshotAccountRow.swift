import Foundation
import TokenKickKit

/// Defensive projection of one generic status row (`status.accounts[n]`)
/// into what the shell renders. Unknown or missing fields degrade to "—",
/// never to fabricated values (UX plan §6).
public struct SnapshotAccountRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let label: String
    public let provider: String
    public let state: String
    public let resetsInText: String
    public let usedPercent: Double?
    public let visible: Bool
    public let kickable: Bool
    public let kickBlockedReason: String?
    public let stale: Bool
    public let errorText: String?
    public let observedAt: Date?

    public init?(json: TKJSONValue) {
        guard let label = json["label"]?.stringValue else { return nil }
        self.label = label
        self.id = json["account_key"]?.stringValue ?? label
        self.provider = json["provider"]?.stringValue ?? "unknown"
        self.state = json["state"]?.stringValue ?? "unknown"
        self.resetsInText = json["resets_in_human"]?.stringValue ?? "—"
        self.usedPercent = json["used_percent"]?.numberValue
        self.visible = json["visible"]?.boolValue ?? true
        self.kickable = json["kickable"]?.boolValue ?? false
        self.kickBlockedReason = json["kick_blocked_reason"]?.stringValue
        self.stale = json["stale"]?.boolValue ?? false
        self.errorText = json["error"]?.stringValue
        self.observedAt = json["observed_at"]?.stringValue.flatMap(parseUTCISO)
    }

    public static func rows(from snapshot: TKSnapshotPayload?) -> [SnapshotAccountRow] {
        guard let snapshot else { return [] }
        return snapshot.status.accounts.compactMap(SnapshotAccountRow.init(json:))
    }

    /// Two-letter provider badge per UX plan §2 (no third-party logos).
    public var providerBadge: String {
        switch provider {
        case "codex": return "CX"
        case "claude": return "CL"
        case "gemini": return "GM"
        default: return provider.prefix(2).uppercased()
        }
    }

    public var stateSymbolName: String {
        switch state {
        case "fresh": return "bolt.circle"
        case "active": return "play.circle"
        case "exhausted": return "hourglass"
        case "unknown": return "questionmark.circle"
        default: return "circle"
        }
    }

    /// Display form of the raw state word ("active" → "Active").
    public var stateDisplay: String {
        state.prefix(1).uppercased() + state.dropFirst()
    }

    /// "resets 1h 26m" for durations; core phrases like "reset ready"
    /// stand on their own. Nil when the core sent no value.
    public var resetsPhrase: String? {
        guard resetsInText != "—" else { return nil }
        return resetsInText.first?.isNumber == true
            ? "resets \(resetsInText)"
            : resetsInText
    }
}

/// Minimal projection of one pending kick for the popover's next-action line.
public struct PendingKickRow: Equatable, Sendable {
    public let accountLabel: String
    public let kickAt: Date
    public let reason: String

    public init?(json: TKJSONValue) {
        guard
            let label = json["account_label"]?.stringValue,
            let kickAtText = json["kick_at"]?.stringValue,
            let kickAt = parseUTCISO(kickAtText)
        else { return nil }
        self.accountLabel = label
        self.kickAt = kickAt
        self.reason = json["reason"]?.stringValue ?? "scheduled"
    }

    public static func rows(from snapshot: TKSnapshotPayload?) -> [PendingKickRow] {
        guard let snapshot else { return [] }
        return snapshot.pendingKicks
            .compactMap(PendingKickRow.init(json:))
            .sorted { $0.kickAt < $1.kickAt }
    }
}

func parseUTCISO(_ text: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    if let date = formatter.date(from: text) { return date }
    formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    return formatter.date(from: text)
}

/// One time vocabulary app-wide (UX plan §14): "3 m ago", "in 2 h 14 m".
public enum RelativeTimeText {
    public static func ago(from date: Date, now: Date) -> String {
        let seconds = max(0, now.timeIntervalSince(date))
        if seconds < 10 { return "just now" }
        return "\(duration(seconds)) ago"
    }

    public static func until(_ date: Date, now: Date) -> String {
        let seconds = date.timeIntervalSince(now)
        if seconds <= 0 { return "now" }
        return "in \(duration(seconds))"
    }

    public static func duration(_ seconds: TimeInterval) -> String {
        let total = Int(seconds.rounded())
        if total < 60 { return "\(total) s" }
        let minutes = total / 60
        if minutes < 60 { return "\(minutes) m" }
        let hours = minutes / 60
        let remainder = minutes % 60
        if hours < 24 {
            return remainder == 0 ? "\(hours) h" : "\(hours) h \(remainder) m"
        }
        let days = hours / 24
        let hourRemainder = hours % 24
        return hourRemainder == 0 ? "\(days) d" : "\(days) d \(hourRemainder) h"
    }
}
