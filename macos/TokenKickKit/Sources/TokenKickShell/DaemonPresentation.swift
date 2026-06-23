import Foundation
import TokenKickKit

/// Toolbar daemon chip (UX plan §4): icon + one word, click → Daemon screen.
public struct DaemonChipState: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case running
        case stopped
        case stale
        case unknown
    }

    public let kind: Kind
    public let title: String
    public let symbolName: String
    /// True when the chip should carry the warning tint (mismatch while running).
    public let hasIssue: Bool

    public static func derive(from daemon: TKDaemonStatus?) -> DaemonChipState {
        guard let daemon else {
            return DaemonChipState(
                kind: .unknown,
                title: "Unknown",
                symbolName: "questionmark.circle",
                hasIssue: false
            )
        }
        if daemon.running {
            let mismatch = daemon.versionMatch == false || daemon.executableMatch == false
            return DaemonChipState(
                kind: .running,
                title: "Running",
                symbolName: "gearshape.2",
                hasIssue: mismatch
            )
        }
        if daemon.stalePidfile {
            return DaemonChipState(
                kind: .stale,
                title: "Needs cleanup",
                symbolName: "exclamationmark.circle",
                hasIssue: true
            )
        }
        return DaemonChipState(
            kind: .stopped,
            title: "Stopped",
            symbolName: "pause.circle",
            hasIssue: false
        )
    }
}

/// Ownership chip for the Daemon screen (UX plan §11), derived from the
/// daemon section of the snapshot. The pidfile-backed executable metadata
/// from Phase 3 distinguishes app-managed from terminal-managed daemons.
public struct DaemonOwnershipPresentation: Equatable, Sendable {
    public enum Kind: Equatable, Sendable {
        case appManaged
        case terminalManaged
        case unknownRunning
        case notRunning
        case stale
    }

    public let kind: Kind
    public let title: String
    public let detail: String

    public static func derive(from daemon: TKDaemonStatus?) -> DaemonOwnershipPresentation {
        guard let daemon else {
            return DaemonOwnershipPresentation(
                kind: .notRunning,
                title: "Not running",
                detail: "No background service information is available yet."
            )
        }
        if daemon.running {
            switch daemon.executableMatch {
            case .some(true):
                return DaemonOwnershipPresentation(
                    kind: .appManaged,
                    title: "Managed by TokenKick",
                    detail: "The background service runs the app's bundled runtime"
                        + (daemon.pid.map { " (pid \($0))." } ?? ".")
                )
            case .some(false):
                let origin = daemon.executable ?? "another install"
                return DaemonOwnershipPresentation(
                    kind: .terminalManaged,
                    title: "Managed by terminal",
                    detail: "A TokenKick daemon from \(origin) is running"
                        + (daemon.version.map { " (v\($0))" } ?? "")
                        + ". The app reads its status but won't interfere."
                )
            case .none:
                return DaemonOwnershipPresentation(
                    kind: .unknownRunning,
                    title: "Running (origin unknown)",
                    detail: "The running daemon predates executable tracking; "
                        + "restart it to record which install owns it."
                )
            }
        }
        if daemon.stalePidfile {
            return DaemonOwnershipPresentation(
                kind: .stale,
                title: "Needs cleanup",
                detail: "A previous background service didn't exit cleanly."
            )
        }
        return DaemonOwnershipPresentation(
            kind: .notRunning,
            title: "Not running",
            detail: "Resets are only kicked while the background service runs."
        )
    }
}
