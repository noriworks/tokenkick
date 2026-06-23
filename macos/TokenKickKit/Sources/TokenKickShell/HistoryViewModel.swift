import Foundation
import Observation
import TokenKickKit

public protocol HistoryProviding: Sendable {
    func loadHistory(limit: Int) async throws -> [TKJSONValue]
}

public struct LiveHistoryProvider: HistoryProviding {
    public let timeout: TimeInterval

    public init(timeout: TimeInterval = 60) {
        self.timeout = timeout
    }

    public func loadHistory(limit: Int) async throws -> [TKJSONValue] {
        try await LiveTKClient.make(timeout: timeout).history(limit: limit)
    }
}

/// One kick-history event projected for the list; the full event stays
/// available as `raw` for the detail inspector (UX plan §13: verbose
/// verification fields live in the inspector, not the table).
public struct HistoryEventRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let date: Date?
    public let label: String
    public let kind: String
    public let kickType: String?
    public let success: Bool
    public let confirmed: Bool
    public let postKickStatus: String?
    public let errorText: String?
    public let raw: TKJSONValue

    public init?(json: TKJSONValue, index: Int) {
        guard let label = json["label"]?.stringValue else { return nil }
        self.label = label
        let timestamp = json["timestamp"]?.numberValue
        self.date = timestamp.map { Date(timeIntervalSince1970: $0) }
        self.id = "\(timestamp ?? 0)-\(label)-\(index)"
        self.kind = json["kind"]?.stringValue ?? "kick"
        self.kickType = json["kick_type"]?.stringValue
        self.success = json["success"]?.boolValue ?? false
        self.confirmed = json["confirmed"]?.boolValue ?? false
        self.postKickStatus = json["post_kick_status"]?.stringValue
        self.errorText = json["error"]?.stringValue
        self.raw = json
    }

    /// Truthful outcome wording, consistent with the Kick screen.
    public var resultText: String {
        if kind == "probe" || kind == "status_probe" {
            return "Probe"
        }
        if success && confirmed {
            return "Kicked — confirmed"
        }
        if success {
            return "Attempted — unconfirmed"
        }
        return "Failed"
    }

    public var symbolName: String {
        if kind == "probe" || kind == "status_probe" {
            return "stethoscope"
        }
        if success && confirmed { return "checkmark.circle.fill" }
        if success { return "clock.badge.questionmark" }
        return "xmark.circle.fill"
    }

    /// Inspector content: every non-null event field, stable order.
    public var detailFields: [(key: String, value: String)] {
        guard let object = raw.objectValue else { return [] }
        return object.keys.sorted().compactMap { key in
            guard let value = object[key], value != .null else { return nil }
            switch value {
            case .string(let text): return (key, text)
            case .number(let number):
                if number == number.rounded() && abs(number) < 1e12 {
                    return (key, String(Int(number)))
                }
                return (key, String(number))
            case .bool(let flag): return (key, flag ? "true" : "false")
            default: return (key, String(describing: value))
            }
        }
    }
}

/// Drives the History screen: one load, reverse-chronological, account
/// filter only in v1 (UX plan §13).
@MainActor
@Observable
public final class HistoryViewModel {
    public enum Phase: Equatable, Sendable {
        case idle
        case loading
        case loaded
        case failed(message: String)
    }

    public static let loadLimit = 200

    public private(set) var phase: Phase = .idle
    public private(set) var rows: [HistoryEventRow] = []
    public var accountFilter: String?
    public var selectedID: String?

    private let provider: any HistoryProviding

    public init(provider: any HistoryProviding) {
        self.provider = provider
    }

    public var filteredRows: [HistoryEventRow] {
        guard let accountFilter else { return rows }
        return rows.filter { $0.label == accountFilter }
    }

    public var accountLabels: [String] {
        var seen = Set<String>()
        return rows.compactMap { row in
            seen.insert(row.label).inserted ? row.label : nil
        }.sorted()
    }

    public var selectedRow: HistoryEventRow? {
        guard let selectedID else { return nil }
        return rows.first { $0.id == selectedID }
    }

    public func load() async {
        if phase == .idle { phase = .loading }
        do {
            let events = try await provider.loadHistory(limit: Self.loadLimit)
            rows = events.enumerated()
                .compactMap { HistoryEventRow(json: $0.element, index: $0.offset) }
                .sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
            if let accountFilter, !rows.contains(where: { $0.label == accountFilter }) {
                self.accountFilter = nil
            }
            if let selectedID, !rows.contains(where: { $0.id == selectedID }) {
                self.selectedID = nil
            }
            phase = .loaded
        } catch {
            phase = .failed(message: String(describing: error))
        }
    }
}
