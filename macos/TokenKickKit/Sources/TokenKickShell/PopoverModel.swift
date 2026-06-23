import Foundation
import TokenKickKit

/// Pure presentation model for the menu bar popover (UX plan §3).
/// Built fresh from the store's published state; holds no state itself.
public struct PopoverModel: Sendable {
    public static let maxAccountRows = 6

    public let headerStateLine: String
    public let topWarning: WarningItem?
    public let additionalWarningCount: Int
    public let accountRows: [SnapshotAccountRow]
    public let overflowAccountCount: Int
    public let nextActionLine: String?
    public let quickKick: QuickKickState
    public let cancelPlanVisible: Bool

    public enum QuickKickState: Equatable, Sendable {
        /// Kickable accounts exist; the menu lists them. Selecting one will
        /// always confirm before spending quota (no express path).
        case available([SnapshotAccountRow])
        /// Always shown, disabled, with the reason and a path to the Kick
        /// screen so the popover is never a dead end.
        case disabled(reason: String)
    }

    public init(
        snapshot: TKSnapshotPayload?,
        warnings: [WarningItem],
        now: Date = Date()
    ) {
        let allRows = SnapshotAccountRow.rows(from: snapshot)
        let visibleRows = allRows.filter(\.visible)
        let pending = PendingKickRow.rows(from: snapshot)

        let actionable = warnings.filter { $0.tier <= .warning }
        let notices = warnings.filter { $0.tier <= .advisory }
        let highlighted = actionable.isEmpty ? notices : actionable
        self.topWarning = highlighted.first
        self.additionalWarningCount = max(0, highlighted.count - 1)

        self.accountRows = Array(visibleRows.prefix(Self.maxAccountRows))
        self.overflowAccountCount = max(0, visibleRows.count - Self.maxAccountRows)

        if let next = pending.first {
            let timeText: String
            if Calendar.current.isDate(next.kickAt, inSameDayAs: now) {
                timeText = RelativeTimeText.until(next.kickAt, now: now)
            } else {
                let formatter = DateFormatter()
                formatter.dateFormat = "EEE HH:mm"
                timeText = "at \(formatter.string(from: next.kickAt))"
            }
            let reasonText = next.reason == "orchestrated" ? " (orchestrated)" : ""
            self.nextActionLine = "\(next.accountLabel) — kick \(timeText)\(reasonText)"
        } else {
            self.nextActionLine = nil
        }

        self.cancelPlanVisible = pending.contains { $0.reason == "orchestrated" }

        let kickableRows = visibleRows.filter(\.kickable)
        if !kickableRows.isEmpty {
            self.quickKick = .available(kickableRows)
        } else if visibleRows.isEmpty {
            self.quickKick = .disabled(reason: "No accounts yet")
        } else {
            let soonest = visibleRows
                .filter { $0.resetsInText != "—" }
                .map(\.resetsInText)
                .first
            self.quickKick = .disabled(
                reason: soonest.map { text in
                    text.first?.isNumber == true
                        ? "No fresh windows — next reset in \(text)"
                        : "No fresh windows — \(text)"
                } ?? "No fresh windows right now"
            )
        }

        if !highlighted.isEmpty {
            let count = highlighted.count
            let noun = actionable.isEmpty ? "notice" : "warning"
            self.headerStateLine = count == 1 ? "1 \(noun)" : "\(count) \(noun)s"
        } else if snapshot == nil {
            self.headerStateLine = "Waiting for first status…"
        } else if let next = pending.first {
            let formatter = DateFormatter()
            formatter.dateFormat = "EEE HH:mm"
            self.headerStateLine = "All quiet — next kick \(formatter.string(from: next.kickAt))"
        } else {
            self.headerStateLine = "All quiet"
        }
    }
}
