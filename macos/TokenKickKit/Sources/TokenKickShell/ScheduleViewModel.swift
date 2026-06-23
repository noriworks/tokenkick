import Foundation
import Observation
import TokenKickKit

public protocol ScheduleServicing: Sendable {
    func scheduleShow() async throws -> TKEnvelope<TKScheduleShowPayload>
    func accountsList() async throws -> TKEnvelope<TKAccountsListPayload>
    func setSchedule(
        scope: String,
        weekdays: String?,
        weekends: String?,
        timezone: String?
    ) async throws -> TKEnvelope<TKScheduleMutationPayload>
    func clearSchedule(scope: String) async throws -> TKEnvelope<TKScheduleMutationPayload>
    func disableSchedule(scope: String) async throws -> TKEnvelope<TKScheduleMutationPayload>
}

public struct LiveScheduleService: ScheduleServicing {
    public let timeout: TimeInterval

    public init(timeout: TimeInterval = 60) {
        self.timeout = timeout
    }

    public func scheduleShow() async throws -> TKEnvelope<TKScheduleShowPayload> {
        try await LiveTKClient.make(timeout: timeout).scheduleShow()
    }

    public func accountsList() async throws -> TKEnvelope<TKAccountsListPayload> {
        try await LiveTKClient.make(timeout: timeout).accountsList()
    }

    public func setSchedule(
        scope: String,
        weekdays: String?,
        weekends: String?,
        timezone: String?
    ) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        try await LiveTKClient.make(timeout: timeout).scheduleSet(
            scope: scope,
            weekdays: weekdays,
            weekends: weekends,
            timezone: timezone
        )
    }

    public func clearSchedule(scope: String) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        try await LiveTKClient.make(timeout: timeout).scheduleClear(scope: scope)
    }

    public func disableSchedule(scope: String) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        try await LiveTKClient.make(timeout: timeout).scheduleDisable(scope: scope)
    }
}

public struct ScheduleScopeRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let enabled: Bool
    public let weekdays: String
    public let weekends: String
    public let pendingCount: Int

    public var isDefault: Bool { id == "default" }
}

public struct SchedulePendingKickRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let account: String
    public let reason: String
    public let purpose: String
    public let kickAt: String
    public let status: String

    public init?(json: TKJSONValue) {
        guard
            let key = json["key"]?.stringValue,
            let account = json["account_label"]?.stringValue,
            let kickAtText = json["kick_at"]?.stringValue
        else { return nil }
        self.id = key
        self.account = account
        self.reason = (json["reason"]?.stringValue ?? "scheduled")
            .replacingOccurrences(of: "_", with: " ")
        self.purpose = PlannerFormatting.purpose(json["purpose"]?.stringValue ?? "coverage")
        self.kickAt = PlannerFormatting.time(kickAtText, reference: nil)
        if json["gave_up_at"]?.stringValue != nil {
            self.status = "gave up"
        } else if json["next_retry_at"]?.stringValue != nil {
            self.status = "retry scheduled"
        } else {
            self.status = "scheduled"
        }
    }
}

@MainActor
@Observable
public final class ScheduleViewModel {
    public enum Phase: Equatable, Sendable {
        case idle
        case loading
        case loaded
        case failed(message: String)
    }

    public enum PendingAction: Equatable, Sendable {
        case clear
        case disable
    }

    public private(set) var phase: Phase = .idle
    public private(set) var payload: TKScheduleShowPayload?
    public private(set) var accountLabels: [String] = []
    public private(set) var pendingKicks: [SchedulePendingKickRow] = []
    public private(set) var isMutating = false
    public private(set) var resultMessage: String?
    public var selectedScope: String = "default" {
        didSet { syncEditorFromSelection() }
    }
    public var weekdays: String = ""
    public var weekends: String = ""
    public var timezone: String = ""
    public var pendingConfirmation: ConfirmedAction?
    public private(set) var pendingAction: PendingAction?

    private let service: any ScheduleServicing
    private let onMutation: @MainActor () async -> Void

    public init(
        service: any ScheduleServicing,
        onMutation: @escaping @MainActor () async -> Void
    ) {
        self.service = service
        self.onMutation = onMutation
    }

    public var rows: [ScheduleScopeRow] {
        guard let payload else { return [] }
        var result = [
            row(
                id: "default",
                title: "Default",
                schedule: payload.default,
                pending: payload.pendingKicks
            )
        ]
        for label in accountLabels {
            let schedule = payload.accounts[label] ?? TKWorkSchedulePayload()
            result.append(row(id: label, title: label, schedule: schedule, pending: payload.pendingKicks))
        }
        return result
    }

    public var selectedSchedule: TKWorkSchedulePayload {
        guard let payload else { return TKWorkSchedulePayload() }
        if selectedScope == "default" { return payload.default }
        return payload.accounts[selectedScope] ?? TKWorkSchedulePayload()
    }

    public var canSave: Bool {
        !isMutating && (!weekdays.trimmingCharacters(in: .whitespaces).isEmpty
            || !weekends.trimmingCharacters(in: .whitespaces).isEmpty)
    }

    public func load() async {
        if phase == .idle { phase = .loading }
        do {
            async let scheduleEnvelope = service.scheduleShow()
            async let accountsEnvelope = service.accountsList()
            let (schedule, accounts) = try await (scheduleEnvelope, accountsEnvelope)
            guard schedule.ok, let schedulePayload = schedule.payload else {
                phase = .failed(message: schedule.message ?? "Could not read schedule.")
                return
            }
            payload = schedulePayload
            accountLabels = (accounts.payload?.accounts ?? [])
                .filter { $0.visible && $0.kickable }
                .map(\.label)
                .sorted()
            pendingKicks = schedulePayload.pendingKicks.compactMap(SchedulePendingKickRow.init(json:))
            if selectedScope != "default" && !accountLabels.contains(selectedScope) {
                selectedScope = "default"
            }
            syncEditorFromSelection()
            phase = .loaded
        } catch {
            phase = .failed(message: String(describing: error))
        }
    }

    public func save() async {
        guard canSave else { return }
        await runMutation {
            try await self.service.setSchedule(
                scope: self.selectedScope,
                weekdays: emptyToNil(self.weekdays),
                weekends: emptyToNil(self.weekends),
                timezone: emptyToNil(self.timezone)
            )
        }
    }

    /// Whether the selected scope is effectively on (global and per-scope).
    public var selectedScopeEnabled: Bool {
        guard let payload else { return false }
        return payload.enabled && selectedSchedule.enabled
    }

    /// Re-enable a disabled scope: `tk schedule set` turns scheduling back
    /// on, and sending no windows keeps the configured ones.
    public func enable() async {
        guard !isMutating else { return }
        await runMutation {
            try await self.service.setSchedule(
                scope: self.selectedScope,
                weekdays: nil,
                weekends: nil,
                timezone: nil
            )
        }
    }

    public func requestClear() {
        guard !isMutating else { return }
        pendingAction = .clear
        pendingConfirmation = ConfirmedAction(
            id: "schedule-clear:\(selectedScope)",
            title: selectedScope == "default" ? "Clear default schedule?" : "Clear schedule for \"\(selectedScope)\"?",
            explanation: "Removes the configured smart-schedule windows for this scope.",
            costLine: nil,
            disclosures: ["Smart-schedule pending kicks for this scope may be removed. Orchestration plans stay untouched."],
            scopeLabel: selectedScope,
            verb: "Clear Schedule",
            isDestructive: true,
            tkArguments: scheduleArguments(action: "clear", scope: selectedScope)
        )
    }

    public func requestDisable() {
        guard !isMutating else { return }
        pendingAction = .disable
        pendingConfirmation = ConfirmedAction(
            id: "schedule-disable:\(selectedScope)",
            title: selectedScope == "default" ? "Disable default schedule?" : "Disable schedule for \"\(selectedScope)\"?",
            explanation: "Keeps the configured windows but stops smart scheduling for this scope.",
            costLine: nil,
            disclosures: ["Smart-schedule pending kicks for this scope may be removed. Orchestration plans stay untouched."],
            scopeLabel: selectedScope,
            verb: "Disable Schedule",
            isDestructive: false,
            tkArguments: scheduleArguments(action: "disable", scope: selectedScope)
        )
    }

    public func cancelConfirmation() {
        pendingConfirmation = nil
        pendingAction = nil
    }

    public func confirmPendingAction() async {
        guard let action = pendingAction else { return }
        pendingConfirmation = nil
        pendingAction = nil
        switch action {
        case .clear:
            await runMutation { try await self.service.clearSchedule(scope: self.selectedScope) }
        case .disable:
            await runMutation { try await self.service.disableSchedule(scope: self.selectedScope) }
        }
    }

    private func runMutation(
        _ operation: @escaping () async throws -> TKEnvelope<TKScheduleMutationPayload>
    ) async {
        guard !isMutating else { return }
        isMutating = true
        defer { isMutating = false }
        do {
            let envelope = try await operation()
            if envelope.ok, let schedule = envelope.payload?.schedule {
                payload = schedule
                pendingKicks = schedule.pendingKicks.compactMap(SchedulePendingKickRow.init(json:))
                resultMessage = envelope.message
            } else {
                resultMessage = envelope.message
                    ?? "The schedule change was not applied (\(envelope.errorCode ?? "unknown"))."
            }
        } catch {
            resultMessage = String(describing: error)
        }
        await load()
        await onMutation()
    }

    private func syncEditorFromSelection() {
        let selected = selectedSchedule
        weekdays = selected.weekdays ?? ""
        weekends = selected.weekends ?? ""
        timezone = payload?.timezone ?? ""
    }

    private func row(
        id: String,
        title: String,
        schedule: TKWorkSchedulePayload,
        pending: [TKJSONValue]
    ) -> ScheduleScopeRow {
        // The default scope's editor lists every pending kick, so its badge
        // counts them all; account scopes count their own.
        let pendingCount = id == "default"
            ? pending.count
            : pending.filter { $0["account_label"]?.stringValue == id }.count
        return ScheduleScopeRow(
            id: id,
            title: title,
            enabled: (payload?.enabled ?? false) && schedule.enabled,
            weekdays: schedule.weekdays ?? "—",
            weekends: schedule.weekends ?? "—",
            pendingCount: pendingCount
        )
    }
}

private func emptyToNil(_ value: String) -> String? {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? nil : trimmed
}

private func scheduleArguments(action: String, scope: String) -> [String] {
    if scope == "default" {
        return ["schedule", action, "--default", "--json-output"]
    }
    return ["schedule", action, "--account", scope, "--json-output"]
}
