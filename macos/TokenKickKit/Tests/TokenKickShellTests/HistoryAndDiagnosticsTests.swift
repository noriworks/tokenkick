import XCTest
import TokenKickKit
@testable import TokenKickShell

private func jsonValue(_ object: Any) throws -> TKJSONValue {
    let data = try JSONSerialization.data(withJSONObject: object)
    return try JSONDecoder().decode(TKJSONValue.self, from: data)
}

final class StubHistoryProvider: HistoryProviding, @unchecked Sendable {
    var result: Result<[TKJSONValue], Error>

    init(_ result: Result<[TKJSONValue], Error>) {
        self.result = result
    }

    func loadHistory(limit: Int) async throws -> [TKJSONValue] {
        try result.get()
    }
}

@MainActor
final class HistoryViewModelTests: XCTestCase {
    private func event(
        label: String,
        timestamp: Double,
        success: Bool = true,
        confirmed: Bool = true,
        kind: String = "kick",
        error: String? = nil
    ) throws -> TKJSONValue {
        var object: [String: Any] = [
            "label": label,
            "timestamp": timestamp,
            "success": success,
            "confirmed": confirmed,
            "kind": kind,
            "kick_type": "session",
            "post_kick_status": confirmed ? "moved" : "phantom",
        ]
        if let error { object["error"] = error }
        return try jsonValue(object)
    }

    func testLoadSortsReverseChronologically() async throws {
        let provider = StubHistoryProvider(.success([
            try event(label: "codex (a)", timestamp: 100),
            try event(label: "codex (b)", timestamp: 300),
            try event(label: "codex (c)", timestamp: 200),
        ]))
        let model = HistoryViewModel(provider: provider)

        await model.load()

        XCTAssertEqual(model.phase, .loaded)
        XCTAssertEqual(model.rows.map(\.label), ["codex (b)", "codex (c)", "codex (a)"])
    }

    func testEmptyHistory() async {
        let model = HistoryViewModel(provider: StubHistoryProvider(.success([])))
        await model.load()
        XCTAssertEqual(model.phase, .loaded)
        XCTAssertTrue(model.rows.isEmpty)
        XCTAssertTrue(model.accountLabels.isEmpty)
    }

    func testLoadFailure() async {
        let model = HistoryViewModel(
            provider: StubHistoryProvider(.failure(StubError(description: "tk timed out")))
        )
        await model.load()
        guard case .failed(let message) = model.phase else {
            return XCTFail("expected failed, got \(model.phase)")
        }
        XCTAssertTrue(message.contains("timed out"))
    }

    func testAccountFilter() async throws {
        let provider = StubHistoryProvider(.success([
            try event(label: "codex (a)", timestamp: 100),
            try event(label: "claude (b)", timestamp: 200),
            try event(label: "codex (a)", timestamp: 300),
        ]))
        let model = HistoryViewModel(provider: provider)
        await model.load()

        XCTAssertEqual(model.accountLabels, ["claude (b)", "codex (a)"])
        XCTAssertEqual(model.filteredRows.count, 3)

        model.accountFilter = "codex (a)"
        XCTAssertEqual(model.filteredRows.count, 2)
        XCTAssertTrue(model.filteredRows.allSatisfy { $0.label == "codex (a)" })
    }

    func testFilterResetsWhenAccountDisappears() async throws {
        let provider = StubHistoryProvider(.success([
            try event(label: "codex (a)", timestamp: 100)
        ]))
        let model = HistoryViewModel(provider: provider)
        await model.load()
        model.accountFilter = "codex (a)"

        provider.result = .success([try event(label: "claude (b)", timestamp: 200)])
        await model.load()

        XCTAssertNil(model.accountFilter, "stale filter clears on reload")
    }

    func testDetailSelectionAndFields() async throws {
        let provider = StubHistoryProvider(.success([
            try event(
                label: "codex (a)",
                timestamp: 100,
                success: true,
                confirmed: false,
                error: "session status is still ambiguous"
            )
        ]))
        let model = HistoryViewModel(provider: provider)
        await model.load()

        let row = try XCTUnwrap(model.rows.first)
        model.selectedID = row.id
        let selected = try XCTUnwrap(model.selectedRow)
        XCTAssertEqual(selected.resultText, "Attempted — unconfirmed")
        let fields = Dictionary(uniqueKeysWithValues: selected.detailFields.map { ($0.key, $0.value) })
        XCTAssertEqual(fields["error"], "session status is still ambiguous")
        XCTAssertEqual(fields["post_kick_status"], "phantom")
        XCTAssertEqual(fields["timestamp"], "100")

        model.selectedID = "nonexistent"
        XCTAssertNil(model.selectedRow)
    }

    func testResultTextTruthfulness() async throws {
        let provider = StubHistoryProvider(.success([
            try event(label: "a", timestamp: 3, success: true, confirmed: true),
            try event(label: "b", timestamp: 2, success: true, confirmed: false),
            try event(label: "c", timestamp: 1, success: false, confirmed: false),
        ]))
        let model = HistoryViewModel(provider: provider)
        await model.load()
        XCTAssertEqual(
            model.rows.map(\.resultText),
            ["Kicked — confirmed", "Attempted — unconfirmed", "Failed"]
        )
    }
}

final class StubDiagnosticsProvider: DiagnosticsProviding, @unchecked Sendable {
    var doctorResult: Result<TKEnvelope<TKJSONValue>, Error>
    var resetResult: Result<[TKJSONValue], Error>
    var ackResult: Result<TKEnvelope<TKJSONValue>, Error>
    private(set) var ackedIDs: [String] = []

    init(
        doctorResult: Result<TKEnvelope<TKJSONValue>, Error>,
        resetResult: Result<[TKJSONValue], Error> = .success([]),
        ackResult: Result<TKEnvelope<TKJSONValue>, Error> = .success(
            try! StubAccountConfigurator.envelope(
                json: ["acknowledged": [[String: Any]]()],
                message: "Acknowledged 1 reset event(s)."
            )
        )
    ) {
        self.doctorResult = doctorResult
        self.resetResult = resetResult
        self.ackResult = ackResult
    }

    func appDoctor() async throws -> TKEnvelope<TKJSONValue> {
        try doctorResult.get()
    }

    func resetLog() async throws -> [TKJSONValue] {
        try resetResult.get()
    }

    func acknowledgeResetEvent(id: String) async throws -> TKEnvelope<TKJSONValue> {
        ackedIDs.append(id)
        return try ackResult.get()
    }
}

@MainActor
final class DiagnosticsViewModelTests: XCTestCase {
    private func doctorEnvelope(failChecks: Bool = false) throws -> TKEnvelope<TKJSONValue> {
        let checks: [[String: Any]] = failChecks
            ? [
                [
                    "level": "FAIL",
                    "code": "daemon_not_running",
                    "message": "Daemon is not running.",
                    "fix": "Run tk daemon --background.",
                ],
                ["level": "WARN", "code": "cache_stale", "message": "Cache is stale.", "fix": NSNull()],
                ["level": "INFO", "code": "schedule_info", "message": "schedule: disabled", "fix": NSNull()],
                ["level": "OK", "code": "config_ok", "message": "Config is valid.", "fix": NSNull()],
            ]
            : [["level": "OK", "code": "config_ok", "message": "Config is valid.", "fix": NSNull()]]
        let payload: [String: Any] = [
            "environment": [
                "core_version": "1.9.17",
                "executable": "/Users/fixture/tk",
                "python_version": "3.14.5",
                "platform": "darwin",
                "app_mode": true,
                "path_env": "/usr/bin:/bin",
            ],
            "provider_clis": [
                "codex": ["found": true, "path": "/opt/homebrew/bin/codex"],
                "claude": ["found": false, "path": NSNull()],
            ],
            "state": [
                "config_dir": "/Users/fixture/.tokenkick",
                "config_dir_writable": true,
                "config_loadable": true,
            ],
            "daemon": NSNull(),
            "doctor": [
                "summary": [
                    "ok": 7,
                    "warn": failChecks ? 1 : 0,
                    "fail": failChecks ? 1 : 0,
                    "accounts": 2,
                    "cache_status": "fresh",
                ],
                "checks": checks,
            ],
        ]
        let envelope: [String: Any] = [
            "schema_version": 1,
            "ok": true,
            "error_code": NSNull(),
            "message": NSNull(),
            "warnings": [String](),
            "payload": payload,
        ]
        let data = try JSONSerialization.data(withJSONObject: envelope)
        return try TKJSONDecoding.envelope(TKJSONValue.self, from: data)
    }

    private func resetEvent(id: String, acknowledged: Bool = false) throws -> TKJSONValue {
        var object: [String: Any] = [
            "id": id,
            "detected_at": "2026-06-01T06:00:00+00:00",
            "provider": "codex",
            "confidence": "confirmed",
            "trigger": "single_account_weekly_reset",
            "affected_accounts": ["codex (a)"],
            "summary": "Weekly reset observed earlier than predicted.",
        ]
        if acknowledged {
            object["acknowledged_at"] = "2026-06-02T06:00:00+00:00"
        }
        return try jsonValue(object)
    }

    func testDoctorPayloadParsing() async throws {
        let provider = StubDiagnosticsProvider(
            doctorResult: .success(try doctorEnvelope(failChecks: true))
        )
        let model = DiagnosticsViewModel(provider: provider)

        await model.load()

        XCTAssertEqual(model.phase, .loaded)
        let summary = try XCTUnwrap(model.doctorSummary)
        XCTAssertEqual(summary.ok, 7)
        XCTAssertEqual(summary.fail, 1)
        XCTAssertEqual(summary.cacheStatus, "fresh")

        XCTAssertEqual(model.attentionChecks.count, 2, "OK and INFO checks stay out of the attention list")
        XCTAssertEqual(model.attentionChecks.first?.level, "FAIL")
        XCTAssertEqual(model.attentionChecks.first?.fix, "Run tk daemon --background.")
        XCTAssertEqual(model.infoChecks.map(\.message), ["schedule: disabled"])

        // Header counts mirror the rendered checks, not the core summary.
        let counts = model.checkCounts
        XCTAssertEqual(counts.ok, 1)
        XCTAssertEqual(counts.warn, 1)
        XCTAssertEqual(counts.fail, 1)

        let environment = Dictionary(
            uniqueKeysWithValues: model.environmentFields.map { ($0.key, $0.value) }
        )
        XCTAssertEqual(environment["core_version"], "1.9.17")
        XCTAssertEqual(environment["app_mode"], "true")

        let clis = model.providerCLIs
        XCTAssertEqual(clis.map(\.name), ["claude", "codex"])
        XCTAssertEqual(clis.first { $0.name == "codex" }?.found, true)
        XCTAssertEqual(clis.first { $0.name == "claude" }?.found, false)
    }

    func testResetRowsParseAndDetailSelection() async throws {
        let provider = StubDiagnosticsProvider(
            doctorResult: .success(try doctorEnvelope()),
            resetResult: .success([
                try resetEvent(id: "r1"),
                try resetEvent(id: "r2", acknowledged: true),
            ])
        )
        let model = DiagnosticsViewModel(provider: provider)

        await model.load()

        XCTAssertEqual(model.resetRows.count, 2)
        let first = try XCTUnwrap(model.resetRows.first)
        XCTAssertEqual(first.typeText, "Provider observation")
        XCTAssertEqual(first.provider, "codex")

        model.selectedResetID = "r2"
        let selected = try XCTUnwrap(model.selectedReset)
        XCTAssertTrue(selected.acknowledged)
        let fields = Dictionary(uniqueKeysWithValues: selected.detailFields.map { ($0.key, $0.value) })
        XCTAssertEqual(fields["affected_accounts"], "codex (a)")
    }

    func testAcknowledgeResetReloadsRefreshesAndSurfacesFailure() async throws {
        let provider = StubDiagnosticsProvider(
            doctorResult: .success(try doctorEnvelope()),
            resetResult: .success([try resetEvent(id: "r1")])
        )
        var refreshes = 0
        let model = DiagnosticsViewModel(provider: provider) { refreshes += 1 }
        await model.load()
        XCTAssertEqual(model.resetRows.first?.acknowledged, false)

        provider.resetResult = .success([try resetEvent(id: "r1", acknowledged: true)])
        await model.acknowledgeReset(id: "r1")

        XCTAssertEqual(provider.ackedIDs, ["r1"])
        XCTAssertEqual(model.resetRows.first?.acknowledged, true)
        XCTAssertNil(model.resetError)
        XCTAssertNil(model.ackingResetID)
        XCTAssertEqual(refreshes, 1, "acking refreshes the snapshot so the advisory clears")

        provider.ackResult = .success(
            try StubAccountConfigurator.envelope(
                json: NSNull(),
                ok: false,
                errorCode: "reset_log_ack_invalid",
                message: "Could not acknowledge."
            )
        )
        await model.acknowledgeReset(id: "r1")
        XCTAssertEqual(model.resetError, "Could not acknowledge.")
    }

    func testDoctorErrorKeepsResetSection() async throws {
        let provider = StubDiagnosticsProvider(
            doctorResult: .failure(StubError(description: "doctor exploded")),
            resetResult: .success([try resetEvent(id: "r1")])
        )
        let model = DiagnosticsViewModel(provider: provider)

        await model.load()

        XCTAssertEqual(model.phase, .loaded, "sections degrade independently")
        XCTAssertTrue(model.doctorError?.contains("doctor exploded") ?? false)
        XCTAssertEqual(model.resetRows.count, 1)
    }

    func testBothSectionsFailingFailsScreen() async {
        let provider = StubDiagnosticsProvider(
            doctorResult: .failure(StubError(description: "doctor exploded")),
            resetResult: .failure(StubError(description: "reset log unreadable"))
        )
        let model = DiagnosticsViewModel(provider: provider)

        await model.load()

        guard case .failed(let message) = model.phase else {
            return XCTFail("expected failed, got \(model.phase)")
        }
        XCTAssertTrue(message.contains("doctor exploded"))
    }

    func testCLIEquivalentsAreCopyableCommands() {
        let commands = DiagnosticsViewModel.cliEquivalents.map(\.command)
        XCTAssertTrue(commands.contains("tk doctor"))
        XCTAssertTrue(commands.contains("tk reset-log"))
        XCTAssertTrue(commands.allSatisfy { $0.contains("tk ") })
    }
}
