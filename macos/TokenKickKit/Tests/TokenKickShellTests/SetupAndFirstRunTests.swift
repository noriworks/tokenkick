import XCTest
import TokenKickKit
@testable import TokenKickShell

final class ScriptedSetupSession: SetupSessionProtocol, @unchecked Sendable {
    let events: AsyncThrowingStream<TKSetupEvent, Error>
    private let lock = NSLock()
    private var _cancelCalled = false
    var cancelCalled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return _cancelCalled
    }

    init(lines: [String], streamError: Error? = nil) {
        events = AsyncThrowingStream { continuation in
            for line in lines {
                continuation.yield(try! TKSetupStream.event(fromLine: line))
            }
            if let streamError {
                continuation.finish(throwing: streamError)
            } else {
                continuation.finish()
            }
        }
    }

    func cancel() {
        lock.lock()
        _cancelCalled = true
        lock.unlock()
    }
}

struct ScriptedStarter: SetupSessionStarting {
    let session: ScriptedSetupSession

    func startSession() throws -> any SetupSessionProtocol { session }
}

struct FailingStarter: SetupSessionStarting {
    func startSession() throws -> any SetupSessionProtocol {
        throw StubError(description: "bundled runtime missing")
    }
}

@MainActor
final class SetupViewModelTests: XCTestCase {
    private var refreshCount = 0

    private func makeModel(_ session: ScriptedSetupSession) -> SetupViewModel {
        refreshCount = 0
        return SetupViewModel(starter: ScriptedStarter(session: session)) { [weak self] in
            self?.refreshCount += 1
        }
    }

    private func waitForTerminalPhase(_ model: SetupViewModel) async {
        for _ in 0..<200 {
            if model.phase != .running { return }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    private static let progressLines = [
        #"{"schema_version": 1, "event": "setup_started", "version": "1.9.16"}"#,
        #"{"schema_version": 1, "event": "config_loaded", "accounts": 0}"#,
        #"{"schema_version": 1, "event": "progress", "message": "Discovering accounts and reading status"}"#,
        #"{"schema_version": 1, "event": "discovery_completed", "summary": "Found 2 accounts via auto-discovery: codex.", "accounts": 2}"#,
        #"{"schema_version": 1, "event": "config_saved", "path": "/Users/fixture/.tokenkick/config.json", "accounts": 2}"#,
    ]

    private static let completedLine = #"{"event": "setup_completed", "schema_version": 1, "ok": true, "error_code": null, "message": null, "warnings": ["Multiple Codex homes found for dev@example.test; only usable homes should auto-kick."], "payload": {"summary": "Found 2 accounts via auto-discovery: codex.", "config_saved": true, "config_path": "/Users/fixture/.tokenkick/config.json", "accounts": [{"label": "codex (a)"}, {"label": "codex (b)"}], "new_accounts": ["codex (b)"], "hidden_duplicate_labels": [], "status": null}}"#

    func testSuccessfulDiscoveryBuildsChecklistAndSummary() async throws {
        let session = ScriptedSetupSession(lines: Self.progressLines + [Self.completedLine])
        let model = makeModel(session)

        model.startDiscovery()
        await waitForTerminalPhase(model)

        guard case .completed(let summary) = model.phase else {
            return XCTFail("expected completed, got \(model.phase)")
        }
        XCTAssertEqual(summary.accountCount, 2)
        XCTAssertEqual(summary.newAccountLabels, ["codex (b)"])
        XCTAssertEqual(summary.warnings.count, 1)
        XCTAssertTrue(summary.summaryText.contains("Found 2 accounts"))
        XCTAssertTrue(model.steps.contains { $0.title.contains("Discovering accounts") })
        XCTAssertTrue(model.steps.contains { $0.title == "Saved 2 accounts" })
        XCTAssertEqual(refreshCount, 1, "snapshot refreshes after setup")
    }

    func testNoAccountsOutcome() async {
        let terminal = #"{"event": "setup_completed", "schema_version": 1, "ok": true, "error_code": null, "message": null, "warnings": ["No accounts found.", "Log in with Codex/CodexBar, then run setup again."], "payload": {"summary": "No accounts found.", "config_saved": false, "config_path": "/Users/fixture/.tokenkick/config.json", "accounts": [], "new_accounts": [], "hidden_duplicate_labels": [], "status": null}}"#
        let session = ScriptedSetupSession(
            lines: [Self.progressLines[0], terminal]
        )
        let model = makeModel(session)

        model.startDiscovery()
        await waitForTerminalPhase(model)

        guard case .noAccounts(let message) = model.phase else {
            return XCTFail("expected noAccounts, got \(model.phase)")
        }
        XCTAssertTrue(message.contains("No accounts found"))
        XCTAssertEqual(refreshCount, 1)
    }

    func testFailureOutcome() async {
        let terminal = #"{"event": "setup_failed", "schema_version": 1, "ok": false, "error_code": "setup_failed", "message": "RuntimeError: discovery exploded", "warnings": [], "payload": null}"#
        let session = ScriptedSetupSession(lines: [Self.progressLines[0], terminal])
        let model = makeModel(session)

        model.startDiscovery()
        await waitForTerminalPhase(model)

        guard case .failed(let message) = model.phase else {
            return XCTFail("expected failed, got \(model.phase)")
        }
        XCTAssertTrue(message.contains("discovery exploded"))
    }

    func testCancellationOutcome() async {
        let terminal = #"{"event": "setup_cancelled", "schema_version": 1, "ok": false, "error_code": "cancelled", "message": "Setup was cancelled before completion.", "warnings": [], "payload": null}"#
        let session = ScriptedSetupSession(lines: [Self.progressLines[0], terminal])
        let model = makeModel(session)

        model.startDiscovery()
        model.cancelDiscovery()
        await waitForTerminalPhase(model)

        XCTAssertTrue(session.cancelCalled, "cancel reaches the session (SIGINT)")
        XCTAssertEqual(model.phase, .cancelled)
    }

    func testStreamErrorWithoutTerminalRecordFails() async {
        let session = ScriptedSetupSession(
            lines: [Self.progressLines[0]],
            streamError: StubError(description: "pipe broke")
        )
        let model = makeModel(session)

        model.startDiscovery()
        await waitForTerminalPhase(model)

        guard case .failed(let message) = model.phase else {
            return XCTFail("expected failed, got \(model.phase)")
        }
        XCTAssertTrue(message.contains("pipe broke"))
        XCTAssertEqual(refreshCount, 0, "no refresh when the stream broke")
    }

    func testMissingTerminalRecordFails() async {
        let session = ScriptedSetupSession(lines: Self.progressLines)
        let model = makeModel(session)

        model.startDiscovery()
        await waitForTerminalPhase(model)

        guard case .failed(let message) = model.phase else {
            return XCTFail("expected failed, got \(model.phase)")
        }
        XCTAssertTrue(message.contains("without a final record"))
    }

    func testStarterFailureFailsImmediately() {
        let model = SetupViewModel(starter: FailingStarter()) { [weak self] in
            self?.refreshCount += 1
        }
        model.startDiscovery()
        guard case .failed(let message) = model.phase else {
            return XCTFail("expected failed, got \(model.phase)")
        }
        XCTAssertTrue(message.contains("runtime missing"))
    }
}

@MainActor
final class FirstRunModelTests: XCTestCase {
    func testShouldOfferOnlyForUnconfiguredSnapshot() throws {
        let empty = try ShellFixtures.envelope().payload
        XCTAssertTrue(FirstRunModel.shouldOffer(snapshot: empty, completedBefore: false))
        XCTAssertFalse(FirstRunModel.shouldOffer(snapshot: empty, completedBefore: true))
        XCTAssertFalse(FirstRunModel.shouldOffer(snapshot: nil, completedBefore: false))

        let configured = try ShellFixtures.snapshot(
            accounts: [ShellFixtures.accountRow(label: "codex (a)")]
        )
        XCTAssertFalse(FirstRunModel.shouldOffer(snapshot: configured, completedBefore: false))
    }

    func testHappyPathTransitions() {
        let model = FirstRunModel()
        XCTAssertEqual(model.step, .welcome)
        model.beginDiscovery()
        XCTAssertEqual(model.step, .discover)
        model.discoveryResolved(
            .completed(
                SetupSummary(
                    summaryText: "ok",
                    accountCount: 1,
                    newAccountLabels: [],
                    hiddenDuplicateLabels: [],
                    warnings: []
                )
            )
        )
        XCTAssertEqual(model.step, .background)
        model.finish()
        XCTAssertEqual(model.step, .done)
    }

    func testNoAccountsKeepsDiscoverStepForRetry() {
        let model = FirstRunModel()
        model.beginDiscovery()
        model.discoveryResolved(.noAccounts(message: "none"))
        XCTAssertEqual(model.step, .discover)
        model.discoveryResolved(.failed(message: "x"))
        XCTAssertEqual(model.step, .discover)
        model.discoveryResolved(.cancelled)
        XCTAssertEqual(model.step, .discover)
    }

    func testSkipIsAllowedEverywhere() {
        let model = FirstRunModel()
        model.skip()
        XCTAssertEqual(model.step, .done)
    }
}
