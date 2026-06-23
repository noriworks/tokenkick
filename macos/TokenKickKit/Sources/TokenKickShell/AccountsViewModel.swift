import Foundation
import Observation
import TokenKickKit

/// Read + mutate account configuration through bundled-tk JSON commands.
public protocol AccountConfiguring: Sendable {
    func accountsList() async throws -> TKEnvelope<TKAccountsListPayload>
    func accountsPlanning() async throws -> TKEnvelope<TKAccountsPlanningPayload>
    func accountsNotifications() async throws -> TKEnvelope<TKAccountNotificationsPayload>
    func runMutation(arguments: [String]) async throws -> TKEnvelope<TKJSONValue>
}

public struct LiveAccountConfigurator: AccountConfiguring {
    public let timeout: TimeInterval

    public init(timeout: TimeInterval = 60) {
        self.timeout = timeout
    }

    public func accountsList() async throws -> TKEnvelope<TKAccountsListPayload> {
        try await LiveTKClient.make(timeout: timeout).accountsList()
    }

    public func accountsPlanning() async throws -> TKEnvelope<TKAccountsPlanningPayload> {
        try await LiveTKClient.make(timeout: timeout).accountsPlanning()
    }

    public func accountsNotifications() async throws -> TKEnvelope<TKAccountNotificationsPayload> {
        try await LiveTKClient.make(timeout: timeout).accountsNotifications()
    }

    public func runMutation(arguments: [String]) async throws -> TKEnvelope<TKJSONValue> {
        try await LiveTKClient.make(timeout: timeout)
            .envelope(TKJSONValue.self, arguments: arguments)
    }
}

/// Account settings mutations, each mapping to one bundled-tk command.
/// Most settings are plain controls; enabling provider automation has a
/// separate, typed risk-consent flow enforced by the bundled core.
public enum AccountMutation: Equatable, Sendable {
    case setVisible(Bool)
    case setAutoKick(Bool)
    case setSessionAutoKick(Bool)
    case setWeeklyAutoKick(Bool)
    case setUsableSessionMinutes(Int)
    case setOrchestrationRole(String)
    case setWeeklyReserveThreshold(Int?)
    case setNotificationRoute(NotificationRoute)

    public var enablesAutoKick: Bool {
        switch self {
        case .setAutoKick(true), .setSessionAutoKick(true), .setWeeklyAutoKick(true):
            return true
        default:
            return false
        }
    }

    public func arguments(label: String, acceptingRisk: Bool = false) -> [String] {
        var arguments: [String]
        switch self {
        case .setVisible(let visible):
            arguments = ["accounts", visible ? "show" : "hide", label, "--json-output"]
        case .setAutoKick(let enabled):
            arguments = ["auto", enabled ? "enable" : "disable", label, "--json-output"]
        case .setSessionAutoKick(let enabled):
            arguments = ["auto", "session", enabled ? "enable" : "disable", label, "--json-output"]
        case .setWeeklyAutoKick(let enabled):
            arguments = ["auto", "weekly", enabled ? "enable" : "disable", label, "--json-output"]
        case .setUsableSessionMinutes(let minutes):
            arguments = ["accounts", "set-usable", label, String(minutes), "--json-output"]
        case .setOrchestrationRole(let role):
            arguments = ["accounts", "set-role", label, role, "--json-output"]
        case .setWeeklyReserveThreshold(let threshold):
            if let threshold {
                arguments = ["accounts", "set-weekly-reserve", label, String(threshold), "--json-output"]
            } else {
                arguments = ["accounts", "clear-weekly-reserve", label, "--json-output"]
            }
        case .setNotificationRoute(let route):
            arguments = ["accounts", "set-notifications", label] + route.flags + ["--json-output"]
        }
        if acceptingRisk && enablesAutoKick {
            arguments.insert(contentsOf: ["--accept-risk", "ENABLE"], at: arguments.count - 1)
        }
        return arguments
    }
}

public struct AutoKickConsentRequest: Identifiable, Equatable, Sendable {
    public let label: String
    public let provider: String
    public let text: String
    public let confirmation: String
    public let mutation: AccountMutation

    public var id: String { "\(provider):\(label)" }
}

/// Global notification configuration through `tk notify --json-output`.
public enum GlobalNotificationMutation: Equatable, Sendable {
    case enableNtfy(topic: String)
    case enableTelegram(token: String, chatID: String)
    case sendTest

    public var arguments: [String] {
        switch self {
        case .enableNtfy(let topic):
            return ["notify", "--ntfy", topic, "--json-output"]
        case .enableTelegram(let token, let chatID):
            return ["notify", "--telegram", token, chatID, "--json-output"]
        case .sendTest:
            return ["notify", "test", "--json-output"]
        }
    }

    /// Test delivery reads config but changes nothing.
    public var changesConfiguration: Bool {
        switch self {
        case .sendTest: return false
        case .enableNtfy, .enableTelegram: return true
        }
    }
}

/// Per-account notification routing (UX plan: Configure > Notifications).
public enum NotificationRoute: String, CaseIterable, Identifiable, Equatable, Sendable {
    case globalDefault
    case ntfy
    case telegram
    case both
    case none

    public var id: String { rawValue }

    public var flags: [String] {
        switch self {
        case .globalDefault: return ["--global-default"]
        case .ntfy: return ["--ntfy"]
        case .telegram: return ["--telegram"]
        case .both: return ["--ntfy", "--telegram"]
        case .none: return ["--none"]
        }
    }

    public var label: String {
        switch self {
        case .globalDefault: return "Global default"
        case .ntfy: return "ntfy"
        case .telegram: return "Telegram"
        case .both: return "ntfy + Telegram"
        case .none: return "Off"
        }
    }

    /// Best-effort mapping from the core's route display strings.
    public static func from(routeDisplay: String, enabled: Bool) -> NotificationRoute {
        if !enabled { return .none }
        let lowered = routeDisplay.lowercased()
        let hasNtfy = lowered.contains("ntfy")
        let hasTelegram = lowered.contains("telegram")
        if hasNtfy && hasTelegram { return .both }
        if hasNtfy { return .ntfy }
        if hasTelegram { return .telegram }
        if lowered.contains("disabled") || lowered.contains("off") { return .none }
        return .globalDefault
    }
}

/// One row joining list + planning payloads for the Accounts screen.
public struct AccountConfigRow: Identifiable, Equatable, Sendable {
    public let list: TKAccountsListPayload.Account
    public let planning: TKAccountsPlanningPayload.Account?

    public var id: String { list.label }
    public var label: String { list.label }
}

/// Drives Accounts and Notifications management: load, mutate, reload, and
/// refresh the global snapshot after every mutation. Errors from the core
/// (e.g. auto-kick on a monitor-only account) surface as inline messages —
/// the core's refusal wording is shown verbatim.
@MainActor
@Observable
public final class AccountsViewModel {
    public enum LoadPhase: Equatable, Sendable {
        case idle
        case loading
        case loaded
        case failed(message: String)
    }

    public private(set) var loadPhase: LoadPhase = .idle
    public private(set) var rows: [AccountConfigRow] = []
    public private(set) var notifications: TKAccountNotificationsPayload?
    public private(set) var busyLabels: Set<String> = []
    /// Most recent mutation refusal/failure per account, core wording.
    public private(set) var mutationErrors: [String: String] = [:]
    public private(set) var pendingAutoKickConsent: AutoKickConsentRequest?
    public var selectedLabel: String?

    public private(set) var globalBusy = false
    public private(set) var globalMutationError: String?
    public private(set) var globalMutationMessage: String?

    private let service: any AccountConfiguring
    private let onMutation: @MainActor () async -> Void

    public init(
        service: any AccountConfiguring,
        onMutation: @escaping @MainActor () async -> Void
    ) {
        self.service = service
        self.onMutation = onMutation
    }

    public var selectedRow: AccountConfigRow? {
        guard let selectedLabel else { return nil }
        return rows.first { $0.label == selectedLabel }
    }

    public func isBusy(_ label: String) -> Bool {
        busyLabels.contains(label)
    }

    // MARK: - Loading

    public func load() async {
        if loadPhase == .idle { loadPhase = .loading }
        do {
            async let listEnvelope = service.accountsList()
            async let planningEnvelope = service.accountsPlanning()
            async let notificationsEnvelope = service.accountsNotifications()
            let (list, planning, routes) = try await (
                listEnvelope, planningEnvelope, notificationsEnvelope
            )
            guard list.ok, let listPayload = list.payload else {
                loadPhase = .failed(message: list.message ?? "Could not read accounts.")
                return
            }
            let planningByLabel = Dictionary(
                uniqueKeysWithValues: (planning.payload?.accounts ?? []).map { ($0.label, $0) }
            )
            rows = listPayload.accounts.map { account in
                AccountConfigRow(list: account, planning: planningByLabel[account.label])
            }
            notifications = routes.payload
            if let selectedLabel, !rows.contains(where: { $0.label == selectedLabel }) {
                self.selectedLabel = rows.first?.label
            }
            loadPhase = .loaded
        } catch {
            loadPhase = .failed(message: String(describing: error))
        }
    }

    // MARK: - Mutations

    /// Run one mutation: busy state, envelope result, reload + snapshot
    /// refresh afterwards regardless of outcome.
    public func apply(_ mutation: AccountMutation, to label: String) async {
        await apply(mutation, to: label, acceptingRisk: false)
    }

    private func apply(
        _ mutation: AccountMutation,
        to label: String,
        acceptingRisk: Bool
    ) async {
        guard !isBusy(label) else { return }
        busyLabels.insert(label)
        mutationErrors[label] = nil
        defer { busyLabels.remove(label) }
        do {
            let envelope = try await service.runMutation(
                arguments: mutation.arguments(label: label, acceptingRisk: acceptingRisk)
            )
            if !envelope.ok {
                if let request = consentRequest(
                    from: envelope,
                    label: label,
                    mutation: mutation
                ) {
                    pendingAutoKickConsent = request
                } else {
                    mutationErrors[label] = envelope.message
                        ?? "The change was not applied (\(envelope.errorCode ?? "unknown"))."
                }
            }
        } catch {
            mutationErrors[label] = String(describing: error)
        }
        await load()
        await onMutation()
    }

    public func cancelAutoKickConsent() {
        pendingAutoKickConsent = nil
    }

    public func confirmAutoKickConsent() async {
        guard let request = pendingAutoKickConsent else { return }
        pendingAutoKickConsent = nil
        await apply(request.mutation, to: request.label, acceptingRisk: true)
    }

    private func consentRequest(
        from envelope: TKEnvelope<TKJSONValue>,
        label: String,
        mutation: AccountMutation
    ) -> AutoKickConsentRequest? {
        guard
            mutation.enablesAutoKick,
            envelope.errorCode == "auto_kick_consent_required",
            let consent = envelope.payload?["consent"],
            let provider = consent["provider"]?.stringValue,
            let text = consent["text"]?.stringValue,
            let confirmation = consent["confirmation"]?.stringValue
        else { return nil }
        return AutoKickConsentRequest(
            label: label,
            provider: provider,
            text: text,
            confirmation: confirmation,
            mutation: mutation
        )
    }

    public func clearError(for label: String) {
        mutationErrors[label] = nil
    }

    // MARK: - Global notification configuration

    /// Configure the global destination (or send a test) through
    /// `tk notify --json-output`; the envelope message is shown verbatim.
    public func applyGlobal(_ mutation: GlobalNotificationMutation) async {
        guard !globalBusy else { return }
        globalBusy = true
        globalMutationError = nil
        globalMutationMessage = nil
        defer { globalBusy = false }
        do {
            let envelope = try await service.runMutation(arguments: mutation.arguments)
            if envelope.ok {
                globalMutationMessage = envelope.message
            } else {
                globalMutationError = envelope.message
                    ?? "The change was not applied (\(envelope.errorCode ?? "unknown"))."
            }
        } catch {
            globalMutationError = String(describing: error)
        }
        if mutation.changesConfiguration {
            await load()
            await onMutation()
        }
    }

    public func clearGlobalResult() {
        globalMutationError = nil
        globalMutationMessage = nil
    }
}
