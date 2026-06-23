import Foundation
import Observation
import TokenKickKit

public protocol DiagnosticsProviding: Sendable {
    func appDoctor() async throws -> TKEnvelope<TKJSONValue>
    func resetLog() async throws -> [TKJSONValue]
    func acknowledgeResetEvent(id: String) async throws -> TKEnvelope<TKJSONValue>
}

public struct LiveDiagnosticsProvider: DiagnosticsProviding {
    public let timeout: TimeInterval

    public init(timeout: TimeInterval = 120) {
        self.timeout = timeout
    }

    public func appDoctor() async throws -> TKEnvelope<TKJSONValue> {
        try await LiveTKClient.make(timeout: timeout).appDoctor()
    }

    public func resetLog() async throws -> [TKJSONValue] {
        try await LiveTKClient.make(timeout: timeout).resetLog()
    }

    public func acknowledgeResetEvent(id: String) async throws -> TKEnvelope<TKJSONValue> {
        try await LiveTKClient.make(timeout: timeout)
            .envelope(TKJSONValue.self, arguments: ["reset-log", "ack", id, "--json-output"])
    }
}

/// Core doctor report condensed for the screen header.
public struct DoctorSummaryInfo: Equatable, Sendable {
    public let ok: Int
    public let warn: Int
    public let fail: Int
    public let accounts: Int
    public let cacheStatus: String?
}

public struct DoctorCheckRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let level: String
    public let message: String
    public let fix: String?
}

/// One reset event/provider observation row; full event in `raw`.
public struct ResetEventRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let detectedAt: Date?
    public let provider: String
    public let typeText: String
    public let confidence: String
    public let affectedAccounts: [String]
    public let acknowledged: Bool
    public let summary: String?
    public let raw: TKJSONValue

    public init?(json: TKJSONValue) {
        guard let id = json["id"]?.stringValue else { return nil }
        self.id = id
        self.detectedAt = json["detected_at"]?.stringValue.flatMap(parseUTCISO)
        self.provider = json["provider"]?.stringValue ?? "unknown"
        // Mirrors the core's is_provider_reset_observation trigger set.
        let observationTriggers: Set<String> = [
            "single_account_usage_drop",
            "single_account_weekly_reset",
        ]
        let trigger = json["trigger"]?.stringValue ?? ""
        self.typeText = observationTriggers.contains(trigger)
            ? "Provider observation"
            : "Global reset"
        self.confidence = json["confidence"]?.stringValue ?? "unknown"
        self.affectedAccounts = (json["affected_accounts"]?.arrayValue ?? [])
            .compactMap(\.stringValue)
        self.acknowledged = json["acknowledged_at"]?.stringValue != nil
        self.summary = json["summary"]?.stringValue
        self.raw = json
    }

    public var detailFields: [(key: String, value: String)] {
        guard let object = raw.objectValue else { return [] }
        return object.keys.sorted().compactMap { key in
            guard let value = object[key], value != .null else { return nil }
            switch value {
            case .string(let text): return (key, text)
            case .number(let number): return (key, String(number))
            case .bool(let flag): return (key, flag ? "true" : "false")
            case .array(let items):
                let texts = items.compactMap(\.stringValue)
                return texts.isEmpty ? nil : (key, texts.joined(separator: ", "))
            default: return nil
            }
        }
    }
}

/// Drives the Diagnostics screen: doctor report, reset observations, and
/// environment/runtime info, each section degrading independently.
@MainActor
@Observable
public final class DiagnosticsViewModel {
    public enum Phase: Equatable, Sendable {
        case idle
        case loading
        case loaded
        case failed(message: String)
    }

    public private(set) var phase: Phase = .idle
    public private(set) var doctorPayload: TKJSONValue?
    public private(set) var doctorError: String?
    public private(set) var resetRows: [ResetEventRow] = []
    public private(set) var resetError: String?
    /// The reset event currently being acknowledged, if any.
    public private(set) var ackingResetID: String?
    public var selectedResetID: String?

    private let provider: any DiagnosticsProviding
    private let onMutation: @MainActor () async -> Void

    public init(
        provider: any DiagnosticsProviding,
        onMutation: @escaping @MainActor () async -> Void = {}
    ) {
        self.provider = provider
        self.onMutation = onMutation
    }

    /// Copyable CLI equivalents (UX plan §8): the same data from a terminal.
    public static let cliEquivalents: [(title: String, command: String)] = [
        ("Doctor report", "tk doctor"),
        ("Reset log", "tk reset-log"),
        ("Kick history", "tk history --verbose"),
        ("App snapshot", "TK_APP_MODE=1 tk app snapshot"),
    ]

    public var selectedReset: ResetEventRow? {
        guard let selectedResetID else { return nil }
        return resetRows.first { $0.id == selectedResetID }
    }

    public var doctorSummary: DoctorSummaryInfo? {
        guard let summary = doctorPayload?["doctor"]?["summary"] else { return nil }
        return DoctorSummaryInfo(
            ok: summary["ok"]?.numberValue.map(Int.init) ?? 0,
            warn: summary["warn"]?.numberValue.map(Int.init) ?? 0,
            fail: summary["fail"]?.numberValue.map(Int.init) ?? 0,
            accounts: summary["accounts"]?.numberValue.map(Int.init) ?? 0,
            cacheStatus: summary["cache_status"]?.stringValue
        )
    }

    /// Failing and warning checks — the rows that need attention.
    public var attentionChecks: [DoctorCheckRow] {
        checkRows(levels: ["FAIL", "WARN"])
    }

    /// Informational checks, rendered quietly below the attention rows.
    public var infoChecks: [DoctorCheckRow] {
        checkRows(levels: ["INFO"])
    }

    /// Counts derived from the checks the screen actually shows — the core's
    /// summary counts internal sub-checks the app never renders, so using it
    /// would contradict the visible list.
    public var checkCounts: (ok: Int, warn: Int, fail: Int) {
        let levels = (doctorPayload?["doctor"]?["checks"]?.arrayValue ?? [])
            .compactMap { $0["level"]?.stringValue }
        return (
            ok: levels.filter { $0 == "OK" }.count,
            warn: levels.filter { $0 == "WARN" }.count,
            fail: levels.filter { $0 == "FAIL" }.count
        )
    }

    private func checkRows(levels: Set<String>) -> [DoctorCheckRow] {
        let checks = doctorPayload?["doctor"]?["checks"]?.arrayValue ?? []
        return checks.enumerated().compactMap { index, check in
            guard
                let level = check["level"]?.stringValue,
                levels.contains(level),
                let message = check["message"]?.stringValue
            else { return nil }
            return DoctorCheckRow(
                id: check["code"]?.stringValue ?? "check-\(index)",
                level: level,
                message: message,
                fix: check["fix"]?.stringValue
            )
        }
    }

    public var environmentFields: [(key: String, value: String)] {
        guard let environment = doctorPayload?["environment"]?.objectValue else { return [] }
        let keys = [
            "core_version", "executable", "python_version", "platform", "app_mode",
        ]
        return keys.compactMap { key in
            guard let value = environment[key] else { return nil }
            switch value {
            case .string(let text): return (key, text)
            case .bool(let flag): return (key, flag ? "true" : "false")
            case .number(let number): return (key, String(number))
            default: return nil
            }
        }
    }

    public var providerCLIs: [(name: String, found: Bool, path: String?)] {
        guard let clis = doctorPayload?["provider_clis"]?.objectValue else { return [] }
        return clis.keys.sorted().map { name in
            (
                name: name,
                found: clis[name]?["found"]?.boolValue ?? false,
                path: clis[name]?["path"]?.stringValue
            )
        }
    }

    public var stateFields: [(key: String, value: String)] {
        guard let state = doctorPayload?["state"]?.objectValue else { return [] }
        return state.keys.sorted().compactMap { key in
            guard let value = state[key], value != .null else { return nil }
            switch value {
            case .string(let text): return (key, text)
            case .bool(let flag): return (key, flag ? "true" : "false")
            default: return nil
            }
        }
    }

    public func load() async {
        if phase == .idle { phase = .loading }
        async let doctorTask = loadDoctor()
        async let resetTask = loadResetLog()
        _ = await (doctorTask, resetTask)
        if doctorPayload == nil, let doctorError, resetError != nil {
            phase = .failed(message: doctorError)
        } else {
            phase = .loaded
        }
    }

    private func loadDoctor() async {
        do {
            let envelope = try await provider.appDoctor()
            if envelope.ok, let payload = envelope.payload {
                doctorPayload = payload
                doctorError = nil
            } else {
                doctorError = envelope.message
                    ?? "Doctor failed (\(envelope.errorCode ?? "unknown"))."
            }
        } catch {
            doctorError = String(describing: error)
        }
    }

    private func loadResetLog() async {
        do {
            let events = try await provider.resetLog()
            resetRows = events
                .compactMap(ResetEventRow.init(json:))
                .sorted { ($0.detectedAt ?? .distantPast) > ($1.detectedAt ?? .distantPast) }
            resetError = nil
        } catch {
            resetError = String(describing: error)
        }
    }

    /// Acknowledge one reset event, then reload the log and refresh the
    /// global snapshot so the toolbar advisory clears with it.
    public func acknowledgeReset(id: String) async {
        guard ackingResetID == nil else { return }
        ackingResetID = id
        defer { ackingResetID = nil }
        var ackError: String?
        do {
            let envelope = try await provider.acknowledgeResetEvent(id: id)
            if !envelope.ok {
                ackError = envelope.message ?? "Could not acknowledge the reset event."
            }
        } catch {
            ackError = String(describing: error)
        }
        await loadResetLog()
        if let ackError {
            resetError = ackError
        }
        await onMutation()
    }
}
