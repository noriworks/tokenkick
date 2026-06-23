import Foundation

/// Bare JSON payload of `tk plan --json-output`.
public struct TKPlanPayload: Decodable, Equatable, Sendable {
    public struct WorkWindow: Decodable, Equatable, Sendable {
        public let start: String
        public let end: String
        public let timezone: String?
    }

    public struct Segment: Decodable, Equatable, Sendable {
        public let accountKey: String?
        public let accountLabel: String?
        public let provider: String?
        public let start: String
        public let end: String
        public let source: String
        public let usableSessionMinutes: Int?
        public let kickAt: String?
        public let note: String?

        enum CodingKeys: String, CodingKey {
            case accountKey = "account_key"
            case accountLabel = "account_label"
            case provider
            case start
            case end
            case source
            case usableSessionMinutes = "usable_session_minutes"
            case kickAt = "kick_at"
            case note
        }
    }

    public struct PlannedKick: Decodable, Equatable, Sendable {
        public let accountKey: String
        public let accountLabel: String
        public let provider: String
        public let kickAt: String
        public let workStart: String
        public let workEnd: String
        public let segmentStart: String
        public let segmentEnd: String
        public let usableSessionMinutes: Int
        public let reason: String
        public let windowBasis: String
        public let purpose: String

        enum CodingKeys: String, CodingKey {
            case accountKey = "account_key"
            case accountLabel = "account_label"
            case provider
            case kickAt = "kick_at"
            case workStart = "work_start"
            case workEnd = "work_end"
            case segmentStart = "segment_start"
            case segmentEnd = "segment_end"
            case usableSessionMinutes = "usable_session_minutes"
            case reason
            case windowBasis = "window_basis"
            case purpose
        }
    }

    public struct CoverageGap: Decodable, Equatable, Sendable {
        public let start: String
        public let end: String
        public let reason: String
    }

    public struct Diff: Decodable, Equatable, Sendable {
        public let adds: [TKJSONValue]
        public let replacesOrchestrated: [TKJSONValue]
        public let unchangedOrchestrated: [TKJSONValue]
        public let conflictsUnmanaged: [TKJSONValue]
        public let skipped: [TKJSONValue]
        public let removesOrchestrated: [TKJSONValue]

        enum CodingKeys: String, CodingKey {
            case adds
            case replacesOrchestrated = "replaces_orchestrated"
            case unchangedOrchestrated = "unchanged_orchestrated"
            case conflictsUnmanaged = "conflicts_unmanaged"
            case skipped
            case removesOrchestrated = "removes_orchestrated"
        }
    }

    public struct SkippedAccount: Decodable, Equatable, Sendable {
        public let accountKey: String
        public let accountLabel: String
        public let provider: String
        public let reason: String

        enum CodingKeys: String, CodingKey {
            case accountKey = "account_key"
            case accountLabel = "account_label"
            case provider
            case reason
        }
    }

    public let schemaVersion: Int
    public let readOnly: Bool
    public let applied: Bool
    public let builtAt: String?
    public let workWindow: WorkWindow
    public let cacheAgeSeconds: Int?
    public let accountsConsidered: [TKJSONValue]
    public let segments: [Segment]
    public let plannedKicks: [PlannedKick]
    public let coverageGaps: [CoverageGap]
    public let diff: Diff
    public let skippedAccounts: [SkippedAccount]
    public let limitations: [String]
    public let message: String?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case readOnly = "read_only"
        case applied
        case builtAt = "built_at"
        case workWindow = "work_window"
        case cacheAgeSeconds = "cache_age_seconds"
        case accountsConsidered = "accounts_considered"
        case segments
        case plannedKicks = "planned_kicks"
        case coverageGaps = "coverage_gaps"
        case diff
        case skippedAccounts = "skipped_accounts"
        case limitations
        case message
    }
}

/// Bare JSON payload of `tk plan cancel --json-output`.
public struct TKPlanCancelPayload: Decodable, Equatable, Sendable {
    public let readOnly: Bool
    public let applied: Bool
    public let message: String
    public let result: TKJSONValue
    public let matching: [TKJSONValue]?

    enum CodingKeys: String, CodingKey {
        case readOnly = "read_only"
        case applied
        case message
        case result
        case matching
    }
}

public struct TKWorkSchedulePayload: Decodable, Equatable, Sendable {
    public let enabled: Bool
    public let weekdays: String?
    public let weekends: String?

    public init(enabled: Bool = false, weekdays: String? = nil, weekends: String? = nil) {
        self.enabled = enabled
        self.weekdays = weekdays
        self.weekends = weekends
    }

    enum CodingKeys: String, CodingKey {
        case enabled
        case weekdays
        case weekends
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.enabled = try container.decodeIfPresent(Bool.self, forKey: .enabled) ?? false
        self.weekdays = try container.decodeIfPresent(String.self, forKey: .weekdays)
        self.weekends = try container.decodeIfPresent(String.self, forKey: .weekends)
    }
}

/// Envelope payload of `tk schedule show --json-output`.
public struct TKScheduleShowPayload: Decodable, Equatable, Sendable {
    public let enabled: Bool
    public let timezone: String?
    public let schedulingTarget: String
    public let `default`: TKWorkSchedulePayload
    public let accounts: [String: TKWorkSchedulePayload]
    public let pendingKicks: [TKJSONValue]

    enum CodingKeys: String, CodingKey {
        case enabled
        case timezone
        case schedulingTarget = "scheduling_target"
        case `default`
        case accounts
        case pendingKicks = "pending_kicks"
    }
}

public struct TKScheduleMutationPayload: Decodable, Equatable, Sendable {
    public let action: String
    public let scope: String
    public let removedPendingKicks: [TKJSONValue]
    public let schedule: TKScheduleShowPayload

    enum CodingKeys: String, CodingKey {
        case action
        case scope
        case removedPendingKicks = "removed_pending_kicks"
        case schedule
    }
}

extension TKClient {
    public func plan(
        workWindow: String,
        date: String,
        timezone: String? = nil,
        usage: [String: Int] = [:],
        apply: Bool = false
    ) async throws -> TKPlanPayload {
        var arguments = ["plan", "--work-window", workWindow, "--date", date, "--json-output"]
        if let timezone, !timezone.isEmpty {
            arguments += ["--timezone", timezone]
        }
        for label in usage.keys.sorted() {
            if let minutes = usage[label] {
                arguments += ["--usage", "\(label)=\(minutes)m"]
            }
        }
        if apply {
            arguments += ["--apply", "--yes"]
        }
        let result = try await runner.run(
            executable: runtime,
            arguments: arguments,
            environment: environment
        )
        return try JSONDecoder().decode(TKPlanPayload.self, from: result.stdout)
    }

    public func cancelPlan(accountLabels: [String] = []) async throws -> TKPlanCancelPayload {
        var arguments = ["plan", "cancel", "--json-output", "--yes"]
        for label in accountLabels.sorted() {
            arguments += ["--account", label]
        }
        let result = try await runner.run(
            executable: runtime,
            arguments: arguments,
            environment: environment
        )
        return try JSONDecoder().decode(TKPlanCancelPayload.self, from: result.stdout)
    }

    public func scheduleShow() async throws -> TKEnvelope<TKScheduleShowPayload> {
        try await envelope(TKScheduleShowPayload.self, arguments: ["schedule", "show", "--json-output"])
    }

    public func scheduleSet(
        scope: String,
        weekdays: String?,
        weekends: String?,
        timezone: String?
    ) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        var arguments = ["schedule", "set"]
        if scope == "default" {
            arguments.append("--default")
        } else {
            arguments += ["--account", scope]
        }
        if let weekdays, !weekdays.isEmpty {
            arguments += ["--weekdays", weekdays]
        }
        if let weekends, !weekends.isEmpty {
            arguments += ["--weekends", weekends]
        }
        if let timezone, !timezone.isEmpty {
            arguments += ["--timezone", timezone]
        }
        arguments.append("--json-output")
        return try await envelope(TKScheduleMutationPayload.self, arguments: arguments)
    }

    public func scheduleClear(scope: String) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        var arguments = ["schedule", "clear"]
        if scope == "default" {
            arguments.append("--default")
        } else {
            arguments += ["--account", scope]
        }
        arguments.append("--json-output")
        return try await envelope(TKScheduleMutationPayload.self, arguments: arguments)
    }

    public func scheduleDisable(scope: String) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        var arguments = ["schedule", "disable"]
        if scope == "default" {
            arguments.append("--default")
        } else {
            arguments += ["--account", scope]
        }
        arguments.append("--json-output")
        return try await envelope(TKScheduleMutationPayload.self, arguments: arguments)
    }
}
