import Foundation
import Observation
import TokenKickKit

/// App-level preferences only (UX plan §15). Account and provider
/// configuration lives in the Configure screens, never here.
@MainActor
@Observable
public final class AppSettingsModel {
    public enum Keys {
        public static let extraPathEntries = "extraPathEntries"
        public static let refreshInterval = "refreshInterval"
        public static let updateChecksVisible = "updateChecksVisible"
        public static let firstRunCompleted = "firstRunCompleted"
        public static let mainWindowAutoOpened = "mainWindowAutoOpened"
    }

    public enum RefreshInterval: String, CaseIterable, Identifiable, Sendable {
        case thirtySeconds
        case oneMinute
        case fiveMinutes

        public var id: String { rawValue }

        public var seconds: TimeInterval {
            switch self {
            case .thirtySeconds: return 30
            case .oneMinute: return 60
            case .fiveMinutes: return 300
            }
        }

        public var label: String {
            switch self {
            case .thirtySeconds: return "30 seconds"
            case .oneMinute: return "1 minute"
            case .fiveMinutes: return "5 minutes"
            }
        }
    }

    private let defaults: UserDefaults

    public var extraPathEntries: [String] {
        didSet { defaults.set(extraPathEntries, forKey: Keys.extraPathEntries) }
    }

    public var refreshInterval: RefreshInterval {
        didSet { defaults.set(refreshInterval.rawValue, forKey: Keys.refreshInterval) }
    }

    public var updateChecksVisible: Bool {
        didSet { defaults.set(updateChecksVisible, forKey: Keys.updateChecksVisible) }
    }

    /// First-run was completed or explicitly skipped; never offered again.
    public var firstRunCompleted: Bool {
        didSet { defaults.set(firstRunCompleted, forKey: Keys.firstRunCompleted) }
    }

    public var mainWindowAutoOpened: Bool {
        didSet { defaults.set(mainWindowAutoOpened, forKey: Keys.mainWindowAutoOpened) }
    }

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.extraPathEntries =
            defaults.stringArray(forKey: Keys.extraPathEntries) ?? []
        self.refreshInterval =
            defaults.string(forKey: Keys.refreshInterval)
                .flatMap(RefreshInterval.init(rawValue:)) ?? .oneMinute
        self.updateChecksVisible =
            defaults.object(forKey: Keys.updateChecksVisible) as? Bool ?? true
        self.firstRunCompleted =
            defaults.object(forKey: Keys.firstRunCompleted) as? Bool ?? false
        self.mainWindowAutoOpened =
            defaults.object(forKey: Keys.mainWindowAutoOpened) as? Bool ?? false
    }

    public var shouldAutoOpenMainWindowOnLaunch: Bool {
        !firstRunCompleted && !mainWindowAutoOpened
    }

    public func markMainWindowAutoOpened() {
        mainWindowAutoOpened = true
    }

    /// Built-in provider CLI locations plus the user's extra entries —
    /// the additions handed to `TKEnvironment.subprocessEnvironment`.
    public var pathAdditions: [String] {
        TKEnvironment.defaultPathAdditions + sanitizedExtraEntries
    }

    public var sanitizedExtraEntries: [String] {
        extraPathEntries
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
    }

    public func addPathEntry(_ entry: String) {
        let trimmed = entry.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, !extraPathEntries.contains(trimmed) else { return }
        extraPathEntries.append(trimmed)
    }

    public func removePathEntries(at offsets: IndexSet) {
        extraPathEntries.remove(atOffsets: offsets)
    }

    /// Self-verifying indicator for the Settings list (UX plan §15): which
    /// provider CLIs resolve inside one PATH entry.
    public nonisolated static func providerTools(
        foundIn entry: String,
        home: String = NSHomeDirectory(),
        isExecutable: (String) -> Bool = { FileManager.default.isExecutableFile(atPath: $0) }
    ) -> [String] {
        var directory = entry
        if directory == "~" { directory = home }
        if directory.hasPrefix("~/") { directory = home + directory.dropFirst(1) }
        return ["codex", "claude"].filter { isExecutable(directory + "/" + $0) }
    }

    /// Snapshot of the extra entries readable off the main actor — the live
    /// snapshot provider builds subprocess environments from worker tasks.
    public nonisolated static func storedExtraPathEntries(
        defaults: UserDefaults = .standard
    ) -> [String] {
        defaults.stringArray(forKey: Keys.extraPathEntries) ?? []
    }
}
