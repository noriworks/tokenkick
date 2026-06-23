import Foundation
import Observation

/// Sidebar structure from the UX plan §4. Destinations exist for every
/// screen so navigation, warnings, and deep links are stable now; screens
/// not in the shell phase render as placeholders.
public enum SidebarDestination: String, CaseIterable, Identifiable, Codable, Sendable {
    case status
    case history
    case kick
    case planner
    case schedule
    case accounts
    case notifications
    case daemon
    case diagnostics
    case advanced

    public var id: String { rawValue }

    public var title: String {
        switch self {
        case .status: return "Status"
        case .history: return "History"
        case .kick: return "Kick"
        case .planner: return "Planner"
        case .schedule: return "Schedule"
        case .accounts: return "Accounts"
        case .notifications: return "Notifications"
        case .daemon: return "Daemon"
        case .diagnostics: return "Diagnostics"
        case .advanced: return "Advanced"
        }
    }

    public var symbolName: String {
        switch self {
        case .status: return "gauge.with.needle"
        case .history: return "clock.arrow.circlepath"
        case .kick: return "bolt.fill"
        case .planner: return "calendar.badge.clock"
        case .schedule: return "calendar"
        case .accounts: return "person.2"
        case .notifications: return "bell"
        case .daemon: return "gearshape.2"
        case .diagnostics: return "stethoscope"
        case .advanced: return "wrench.and.screwdriver"
        }
    }

    /// Screens with real content in the native workflow shell.
    public var isImplemented: Bool {
        true
    }
}

public struct SidebarSection: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let destinations: [SidebarDestination]
}

@MainActor
@Observable
public final class NavigationModel {
    public var selection: SidebarDestination = .status

    public init() {}

    public static let sections: [SidebarSection] = [
        SidebarSection(id: "monitor", title: "Monitor", destinations: [.status, .history]),
        SidebarSection(id: "act", title: "Act", destinations: [.kick, .planner, .schedule]),
        SidebarSection(
            id: "configure",
            title: "Configure",
            destinations: [.accounts, .notifications, .daemon]
        ),
        SidebarSection(id: "maintain", title: "Maintain", destinations: [.diagnostics, .advanced]),
    ]

    public func open(_ destination: SidebarDestination) {
        selection = destination
    }
}
