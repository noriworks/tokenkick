import Foundation
import Observation
import TokenKickKit

public protocol PlannerServicing: Sendable {
    func accountsPlanning() async throws -> TKEnvelope<TKAccountsPlanningPayload>
    func previewPlan(
        workWindow: String,
        date: String,
        usage: [String: Int]
    ) async throws -> TKPlanPayload
    func applyPlan(
        workWindow: String,
        date: String,
        usage: [String: Int]
    ) async throws -> TKPlanPayload
    func cancelPlan(accountLabels: [String]) async throws -> TKPlanCancelPayload
}

public struct LivePlannerService: PlannerServicing {
    public let timeout: TimeInterval

    public init(timeout: TimeInterval = 60) {
        self.timeout = timeout
    }

    public func accountsPlanning() async throws -> TKEnvelope<TKAccountsPlanningPayload> {
        try await LiveTKClient.make(timeout: timeout).accountsPlanning()
    }

    public func previewPlan(
        workWindow: String,
        date: String,
        usage: [String: Int]
    ) async throws -> TKPlanPayload {
        try await LiveTKClient.make(timeout: timeout).plan(
            workWindow: workWindow,
            date: date,
            usage: usage
        )
    }

    public func applyPlan(
        workWindow: String,
        date: String,
        usage: [String: Int]
    ) async throws -> TKPlanPayload {
        try await LiveTKClient.make(timeout: timeout).plan(
            workWindow: workWindow,
            date: date,
            usage: usage,
            apply: true
        )
    }

    public func cancelPlan(accountLabels: [String] = []) async throws -> TKPlanCancelPayload {
        try await LiveTKClient.make(timeout: timeout).cancelPlan(accountLabels: accountLabels)
    }
}

public enum UsageAssumptionMode: String, CaseIterable, Identifiable, Sendable {
    case defaults
    case custom

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .defaults: return "Default"
        case .custom: return "Custom"
        }
    }
}

public struct PlanSegmentRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let time: String
    public let account: String
    public let source: String
    public let notes: String

    init(segment: TKPlanPayload.Segment, workStart: Date?) {
        self.id = "\(segment.start)-\(segment.accountLabel ?? "gap")-\(segment.source)"
        self.time = PlannerFormatting.range(segment.start, segment.end, reference: workStart)
        self.account = segment.accountLabel ?? "—"
        self.source = PlannerFormatting.source(segment.source)
        var parts: [String] = []
        if let kickAt = segment.kickAt {
            parts.append("kick at \(PlannerFormatting.time(kickAt, reference: workStart))")
        }
        if let note = segment.note, !note.isEmpty {
            parts.append(note)
        }
        self.notes = parts.joined(separator: "; ")
    }
}

public struct PlanKickRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let kickAt: String
    public let account: String
    public let purpose: String
    public let covers: String
    public let usage: String

    init(kick: TKPlanPayload.PlannedKick, workStart: Date?) {
        self.id = "\(kick.accountKey)-\(kick.kickAt)-\(kick.purpose)"
        self.kickAt = PlannerFormatting.time(kick.kickAt, reference: workStart)
        self.account = kick.accountLabel
        self.purpose = PlannerFormatting.purpose(kick.purpose)
        self.covers = PlannerFormatting.range(kick.segmentStart, kick.segmentEnd, reference: workStart)
        self.usage = PlannerFormatting.duration(minutes: kick.usableSessionMinutes)
    }
}

public struct PlanPendingRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let account: String
    public let kickAt: String
    public let reason: String

    init?(json: TKJSONValue) {
        guard
            let account = json["account_label"]?.stringValue,
            let kickAt = json["kick_at"]?.stringValue,
            let reason = json["reason"]?.stringValue
        else { return nil }
        self.id = "\(account)-\(kickAt)-\(reason)"
        self.account = account
        self.kickAt = PlannerFormatting.time(kickAt, reference: nil)
        self.reason = reason
    }

    init(kick: TKPlanPayload.PlannedKick, workStart: Date?) {
        self.id = "\(kick.accountLabel)-\(kick.kickAt)-\(kick.reason)"
        self.account = kick.accountLabel
        self.kickAt = PlannerFormatting.time(kick.kickAt, reference: workStart)
        self.reason = kick.reason
    }
}

public enum PlannerFormatting {
    static func source(_ raw: String) -> String {
        switch raw {
        case "planned_early_anchor": return "Pre-anchor"
        case "expected_reset_reuse": return "Reset-boundary reuse"
        case "planned_fresh_session": return "Fresh session"
        case "active_session": return "Active session"
        case "natural_reset_reuse": return "Natural reset reuse"
        case "no coverage": return "No coverage"
        default:
            return raw
                .replacingOccurrences(of: "_", with: " ")
                .split(separator: " ")
                .map { $0.prefix(1).uppercased() + $0.dropFirst() }
                .joined(separator: " ")
        }
    }

    static func purpose(_ raw: String) -> String {
        switch raw {
        case "coverage": return "coverage"
        case "specialist_readiness": return "specialist readiness"
        default: return raw.replacingOccurrences(of: "_", with: " ")
        }
    }

    static func duration(minutes: Int) -> String {
        if minutes % 60 == 0 { return "\(minutes / 60)h" }
        if minutes > 60 {
            let hours = minutes / 60
            let remainder = minutes % 60
            return "\(hours)h\(String(format: "%02d", remainder))m"
        }
        return "\(minutes)m"
    }

    static func range(_ startText: String, _ endText: String, reference: Date?) -> String {
        guard let start = parseUTCISO(startText), let end = parseUTCISO(endText) else {
            return "\(startText)–\(endText)"
        }
        let suffix = daySuffix(for: end, reference: reference)
        return "\(time(start))–\(time(end))\(suffix)"
    }

    static func time(_ text: String, reference: Date?) -> String {
        guard let date = parseUTCISO(text) else { return text }
        let suffix = daySuffix(for: date, reference: reference)
        return "\(time(date))\(suffix)"
    }

    static func time(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    static func date(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }

    static func workWindow(start: Date, end: Date) -> String {
        "\(time(start))-\(time(end))"
    }

    private static func daySuffix(for date: Date, reference: Date?) -> String {
        guard let reference else { return "" }
        let calendar = Calendar.current
        let days = calendar.dateComponents(
            [.day],
            from: calendar.startOfDay(for: reference),
            to: calendar.startOfDay(for: date)
        ).day ?? 0
        if days == 0 { return "" }
        if days == 1 { return " (+1 day)" }
        if days > 1 { return " (+\(days) days)" }
        return " (\(days) day)"
    }
}

@MainActor
@Observable
public final class PlannerViewModel {
    public enum Phase: Equatable, Sendable {
        case idle
        case loading
        case loaded
        case failed(message: String)
    }

    public enum PendingAction: Equatable, Sendable {
        case apply
        case cancel
    }

    public private(set) var phase: Phase = .idle
    public private(set) var planningAccounts: [TKAccountsPlanningPayload.Account] = []
    public private(set) var preview: TKPlanPayload?
    public private(set) var actionMessage: String?
    public private(set) var isPreviewing = false
    public private(set) var isMutating = false
    public var pendingConfirmation: ConfirmedAction?
    public private(set) var pendingAction: PendingAction?

    public var selectedDate: Date
    public var startTime: Date
    public var endTime: Date
    public var usageMode: UsageAssumptionMode = .defaults
    public var customUsageMinutes: [String: Int] = [:]

    private let service: any PlannerServicing
    private let onMutation: @MainActor () async -> Void

    public init(
        service: any PlannerServicing,
        now: Date = Date(),
        onMutation: @escaping @MainActor () async -> Void
    ) {
        self.service = service
        self.onMutation = onMutation
        self.selectedDate = now
        self.startTime = Calendar.current.date(bySettingHour: 21, minute: 0, second: 0, of: now) ?? now
        self.endTime = Calendar.current.date(bySettingHour: 23, minute: 0, second: 0, of: now) ?? now
    }

    public var workWindow: String {
        PlannerFormatting.workWindow(start: startTime, end: endTime)
    }

    public var dateArgument: String {
        PlannerFormatting.date(selectedDate)
    }

    public var usageOverrides: [String: Int] {
        usageMode == .custom ? customUsageMinutes : [:]
    }

    public var segmentRows: [PlanSegmentRow] {
        let reference = preview.flatMap { parseUTCISO($0.workWindow.start) }
        return preview?.segments.map { PlanSegmentRow(segment: $0, workStart: reference) } ?? []
    }

    public var plannedKickRows: [PlanKickRow] {
        let reference = preview.flatMap { parseUTCISO($0.workWindow.start) }
        return preview?.plannedKicks.map { PlanKickRow(kick: $0, workStart: reference) } ?? []
    }

    public var activeOrchestratedPendingRows: [PlanPendingRow] = []

    public var canApplyPreview: Bool {
        guard let preview else { return false }
        return !isMutating && preview.readOnly && !preview.plannedKicks.isEmpty
            && preview.diff.conflictsUnmanaged.isEmpty
    }

    public var canCancelActivePlan: Bool {
        !isMutating && !activeOrchestratedPendingRows.isEmpty
    }

    public func load(snapshot: TKSnapshotPayload?) async {
        updateActivePlanRows(snapshot: snapshot)
        if phase == .idle { phase = .loading }
        do {
            let envelope = try await service.accountsPlanning()
            guard envelope.ok, let payload = envelope.payload else {
                phase = .failed(message: envelope.message ?? "Could not read planning defaults.")
                return
            }
            planningAccounts = payload.accounts
            for account in planningAccounts where customUsageMinutes[account.label] == nil {
                customUsageMinutes[account.label] = account.usableSessionMinutes
            }
            await previewPlan()
        } catch {
            phase = .failed(message: String(describing: error))
        }
    }

    public func updateActivePlanRows(snapshot: TKSnapshotPayload?) {
        activeOrchestratedPendingRows = (snapshot?.pendingKicks ?? [])
            .compactMap(PlanPendingRow.init(json:))
            .filter { $0.reason == "orchestrated" }
    }

    public func previewPlan() async {
        isPreviewing = true
        defer { isPreviewing = false }
        do {
            preview = try await service.previewPlan(
                workWindow: workWindow,
                date: dateArgument,
                usage: usageOverrides
            )
            actionMessage = nil
            phase = .loaded
        } catch {
            phase = .failed(message: String(describing: error))
        }
    }

    public func requestApply() {
        guard canApplyPreview else { return }
        pendingAction = .apply
        pendingConfirmation = ConfirmedAction(
            id: "planner-apply",
            title: "Apply orchestration plan?",
            explanation: "Writes the planned pending kicks so the daemon follows this orchestration.",
            costLine: nil,
            disclosures: ["Pending kicks created outside the Planner are never replaced."],
            scopeLabel: "Orchestration plan",
            verb: "Apply Plan",
            isDestructive: false,
            tkArguments: ["plan", "--work-window", workWindow, "--date", dateArgument, "--apply", "--yes", "--json-output"]
        )
    }

    public func requestCancelPlan() {
        guard canCancelActivePlan else { return }
        pendingAction = .cancel
        pendingConfirmation = ConfirmedAction(
            id: "planner-cancel",
            title: "Cancel orchestration plan?",
            explanation: "Removes applied orchestration pending kicks. Smart schedules and manual pending kicks stay untouched.",
            costLine: nil,
            disclosures: [],
            scopeLabel: "Orchestration plan",
            verb: "Cancel Plan",
            isDestructive: true,
            tkArguments: ["plan", "cancel", "--yes", "--json-output"]
        )
    }

    public func cancelConfirmation() {
        pendingConfirmation = nil
        pendingAction = nil
    }

    public func confirmPendingAction(snapshot: TKSnapshotPayload?) async {
        guard let action = pendingAction else { return }
        pendingConfirmation = nil
        pendingAction = nil
        isMutating = true
        defer { isMutating = false }
        do {
            switch action {
            case .apply:
                preview = try await service.applyPlan(
                    workWindow: workWindow,
                    date: dateArgument,
                    usage: usageOverrides
                )
                actionMessage = preview?.message
                let reference = preview.flatMap { parseUTCISO($0.workWindow.start) }
                if preview?.applied == true {
                    activeOrchestratedPendingRows = preview?.plannedKicks.map {
                        PlanPendingRow(kick: $0, workStart: reference)
                    } ?? []
                }
            case .cancel:
                let result = try await service.cancelPlan(accountLabels: [])
                actionMessage = result.message
                activeOrchestratedPendingRows = []
            }
            await onMutation()
            if action == .cancel {
                activeOrchestratedPendingRows = []
            } else if activeOrchestratedPendingRows.isEmpty {
                activeOrchestratedPendingRows = (snapshot?.pendingKicks ?? [])
                    .compactMap(PlanPendingRow.init(json:))
                    .filter { $0.reason == "orchestrated" }
            }
            phase = .loaded
        } catch {
            actionMessage = String(describing: error)
        }
    }
}
