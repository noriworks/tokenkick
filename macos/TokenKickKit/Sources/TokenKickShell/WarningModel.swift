import Foundation
import TokenKickKit

/// Warning tiers from the UX plan §5. Each active condition maps to exactly
/// one tier and one presentation; the menu bar dot mirrors the highest tier
/// at `.warning` or above.
public enum WarningTier: Int, Comparable, Sendable {
    case blocker = 0
    case warning = 1
    case advisory = 2
    case footnote = 3

    public static func < (lhs: WarningTier, rhs: WarningTier) -> Bool {
        lhs.rawValue < rhs.rawValue
    }
}

public struct WarningItem: Identifiable, Equatable, Sendable {
    public let id: String
    public let tier: WarningTier
    public let title: String
    public let detail: String?
    public let destination: SidebarDestination?

    public init(
        id: String,
        tier: WarningTier,
        title: String,
        detail: String? = nil,
        destination: SidebarDestination? = nil
    ) {
        self.id = id
        self.tier = tier
        self.title = title
        self.detail = detail
        self.destination = destination
    }
}

public enum MenuBarIndicator: Equatable, Sendable {
    case normal
    case warning
    case blocker
}

public struct WarningInputs: Sendable {
    public let snapshot: TKSnapshotPayload?
    public let envelopeOK: Bool
    public let envelopeWarnings: [String]
    public let fetchError: String?

    public init(
        snapshot: TKSnapshotPayload?,
        envelopeOK: Bool,
        envelopeWarnings: [String],
        fetchError: String?
    ) {
        self.snapshot = snapshot
        self.envelopeOK = envelopeOK
        self.envelopeWarnings = envelopeWarnings
        self.fetchError = fetchError
    }
}

public enum WarningDerivation {
    /// Pure derivation so tiering is testable without UI. Items come back
    /// sorted blocker → footnote, stable within a tier.
    public static func items(from inputs: WarningInputs) -> [WarningItem] {
        var items: [WarningItem] = []

        if let fetchError = inputs.fetchError, inputs.snapshot == nil {
            items.append(
                WarningItem(
                    id: "core-unreachable",
                    tier: .blocker,
                    title: "TokenKick core isn't responding",
                    detail: fetchError,
                    destination: .diagnostics
                )
            )
            return items
        }
        if !inputs.envelopeOK, inputs.snapshot == nil {
            items.append(
                WarningItem(
                    id: "core-error",
                    tier: .blocker,
                    title: "TokenKick core reported an error",
                    detail: inputs.fetchError,
                    destination: .diagnostics
                )
            )
            return items
        }

        if let fetchError = inputs.fetchError {
            items.append(
                WarningItem(
                    id: "refresh-failed",
                    tier: .warning,
                    title: "Couldn't refresh — showing earlier data",
                    detail: fetchError,
                    destination: .status
                )
            )
        }

        if let snapshot = inputs.snapshot {
            items.append(contentsOf: daemonItems(snapshot))
            items.append(contentsOf: advisoryItems(snapshot))
            items.append(contentsOf: resetObservationItems(snapshot))
        }
        items.append(contentsOf: envelopeWarningItems(inputs.envelopeWarnings))

        return items.sorted { lhs, rhs in
            lhs.tier == rhs.tier ? lhs.id < rhs.id : lhs.tier < rhs.tier
        }
    }

    public static func menuBarIndicator(for items: [WarningItem]) -> MenuBarIndicator {
        guard let highest = items.map(\.tier).min() else { return .normal }
        switch highest {
        case .blocker: return .blocker
        case .warning: return .warning
        case .advisory, .footnote: return .normal
        }
    }

    // MARK: - Sections

    private static func daemonItems(_ snapshot: TKSnapshotPayload) -> [WarningItem] {
        var items: [WarningItem] = []
        let daemon = snapshot.daemon

        if !daemon.running {
            let autoKickConfigured = snapshot.status.accounts.contains {
                $0["auto_kick"]?.boolValue == true
            }
            if autoKickConfigured {
                items.append(
                    WarningItem(
                        id: "daemon-not-running",
                        tier: .warning,
                        title: "Background service isn't running",
                        detail: "Auto-kick is enabled but nothing is watching for resets.",
                        destination: .daemon
                    )
                )
            }
        }
        if daemon.stalePidfile {
            items.append(
                WarningItem(
                    id: "daemon-stale-pidfile",
                    tier: .warning,
                    title: "A previous background service didn't exit cleanly",
                    destination: .daemon
                )
            )
        }
        if daemon.running, daemon.versionMatch == false {
            items.append(
                WarningItem(
                    id: "daemon-version-mismatch",
                    tier: .warning,
                    title: "Restart the background service to finish updating",
                    detail: "It runs v\(daemon.version ?? "?") but v\(daemon.installedVersion) is installed.",
                    destination: .daemon
                )
            )
        }
        if daemon.running, daemon.executableMatch == false {
            items.append(
                WarningItem(
                    id: "daemon-executable-mismatch",
                    tier: .warning,
                    title: "The background service runs from a different install",
                    detail: daemon.executable,
                    destination: .daemon
                )
            )
        }
        return items
    }

    private static func advisoryItems(_ snapshot: TKSnapshotPayload) -> [WarningItem] {
        guard !snapshot.advisories.isEmpty else { return [] }
        let count = snapshot.advisories.count
        let title = count == 1
            ? "1 reserved-account advisory"
            : "\(count) reserved-account advisories"
        return [
            WarningItem(
                id: "reservation-advisories",
                tier: .advisory,
                title: title,
                detail: snapshot.advisories.first?["message"]?.stringValue,
                destination: .status
            )
        ]
    }

    private static func resetObservationItems(_ snapshot: TKSnapshotPayload) -> [WarningItem] {
        let unacknowledged = snapshot.resetObservations.filter {
            $0["acknowledged"]?.boolValue != true
        }
        guard !unacknowledged.isEmpty else { return [] }
        let provider = unacknowledged.last?["provider"]?.stringValue
        return [
            WarningItem(
                id: "reset-observation",
                tier: .advisory,
                title: provider.map { "Reset observed on \($0.capitalized)" }
                    ?? "Provider reset observed",
                detail: "Review what changed and acknowledge it.",
                destination: .diagnostics
            )
        ]
    }

    private static func envelopeWarningItems(_ warnings: [String]) -> [WarningItem] {
        warnings.enumerated().map { index, text in
            let lowered = text.lowercased()
            if lowered.contains("external tk") {
                return WarningItem(
                    id: "envelope-external-tk",
                    tier: .footnote,
                    title: text,
                    destination: .diagnostics
                )
            }
            if lowered.contains("status cache") {
                return WarningItem(
                    id: "envelope-status-cache",
                    tier: .warning,
                    title: "Status data is missing or stale",
                    detail: text,
                    destination: .status
                )
            }
            if lowered.contains("pidfile") || lowered.contains("daemon") {
                // Daemon conditions are derived from snapshot fields above;
                // the envelope's text duplicates them as a footnote.
                return WarningItem(
                    id: "envelope-daemon-\(index)",
                    tier: .footnote,
                    title: text,
                    destination: .daemon
                )
            }
            return WarningItem(
                id: "envelope-\(index)",
                tier: .advisory,
                title: text
            )
        }
    }
}
