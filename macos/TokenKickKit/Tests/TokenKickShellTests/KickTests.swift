import XCTest
import TokenKickKit
@testable import TokenKickShell

// MARK: - Confirmation model

final class ConfirmedActionTests: XCTestCase {
    private func row(
        label: String = "codex (a)",
        provider: String = "codex",
        stale: Bool = false
    ) throws -> SnapshotAccountRow {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [
                ShellFixtures.accountRow(label: label, provider: provider, stale: stale)
            ]
        )
        return try XCTUnwrap(SnapshotAccountRow.rows(from: snapshot).first)
    }

    func testKickActionShape() throws {
        let action = ConfirmedAction.kick(row: try row())
        XCTAssertEqual(action.title, "Kick \"codex (a)\"?")
        XCTAssertEqual(action.verb, "Kick Now")
        XCTAssertEqual(action.scopeLabel, "codex (a)")
        XCTAssertFalse(action.isDestructive)
        XCTAssertEqual(action.tkArguments, ["kick", "codex (a)", "--json-output", "--yes"])
    }

    func testCostLineNamesProviderAndQuota() throws {
        let codex = ConfirmedAction.kick(row: try row(provider: "codex"))
        XCTAssertEqual(
            codex.costLine,
            "Uses a small amount of Codex quota to start the fresh window."
        )
        let claude = ConfirmedAction.kick(row: try row(label: "claude (b)", provider: "claude"))
        XCTAssertTrue(try XCTUnwrap(claude.costLine).contains("Claude quota"))
    }

    func testNoDisclosuresForCleanRow() throws {
        XCTAssertEqual(ConfirmedAction.kick(row: try row()).disclosures, [])
    }

    func testStaleRowAddsDisclosure() throws {
        let action = ConfirmedAction.kick(row: try row(stale: true))
        XCTAssertEqual(action.disclosures.count, 1)
        XCTAssertTrue(action.disclosures[0].lowercased().contains("stale"))
    }

    func testPendingKickAddsClearingDisclosure() throws {
        let now = Date(timeIntervalSince1970: 1_750_000_000)
        let kickAt = ISO8601DateFormatter().string(from: now.addingTimeInterval(3600))
        let snapshot = try ShellFixtures.snapshot(
            accounts: [ShellFixtures.accountRow(label: "codex (a)")],
            pendingKicks: [ShellFixtures.pendingKick(accountLabel: "codex (a)", kickAt: kickAt)]
        )
        let row = try XCTUnwrap(SnapshotAccountRow.rows(from: snapshot).first)
        let action = ConfirmedAction.kick(
            row: row,
            pendingKicks: PendingKickRow.rows(from: snapshot),
            now: now
        )
        XCTAssertEqual(action.disclosures.count, 1)
        XCTAssertTrue(action.disclosures[0].contains("clears the planned kick"))
        XCTAssertTrue(action.disclosures[0].contains("in 1 h"))
    }

    func testOtherAccountsPendingKickDoesNotLeakIntoDisclosures() throws {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [ShellFixtures.accountRow(label: "codex (a)")],
            pendingKicks: [
                ShellFixtures.pendingKick(
                    accountLabel: "codex (other)",
                    kickAt: "2026-06-12T06:00:00Z"
                )
            ]
        )
        let row = try XCTUnwrap(SnapshotAccountRow.rows(from: snapshot).first)
        let action = ConfirmedAction.kick(
            row: row,
            pendingKicks: PendingKickRow.rows(from: snapshot)
        )
        XCTAssertEqual(action.disclosures, [])
    }
}

// MARK: - Outcomes

final class KickOutcomeTests: XCTestCase {
    func testConfirmedOutcome() throws {
        let envelope = try ShellFixtures.kickEnvelope(decision: "attempted", result: "confirmed")
        XCTAssertEqual(
            KickOutcome.from(envelope: envelope),
            .confirmed(message: "Kicked — provider confirmed the new window.")
        )
    }

    func testUnconfirmedOutcomeCarriesCoreMessage() throws {
        let coreMessage = "Codex accepted usage, but session status is still ambiguous"
        let envelope = try ShellFixtures.kickEnvelope(
            message: coreMessage,
            decision: "attempted",
            result: "unconfirmed"
        )
        let outcome = KickOutcome.from(envelope: envelope)
        guard case .unconfirmed(let message) = outcome else {
            return XCTFail("expected unconfirmed, got \(outcome)")
        }
        XCTAssertEqual(message, "Attempted — \(coreMessage)")
    }

    func testFailedOutcome() throws {
        let envelope = try ShellFixtures.kickEnvelope(
            ok: false,
            errorCode: "kick_failed",
            message: "codex exec failed: rate limited",
            decision: "attempted",
            result: "failed",
            kicked: false
        )
        XCTAssertEqual(
            KickOutcome.from(envelope: envelope),
            .failed(message: "codex exec failed: rate limited")
        )
    }

    func testSkippedOutcome() throws {
        let envelope = try ShellFixtures.kickEnvelope(
            message: "Skipping \"codex (a)\": already kicked in this window.",
            decision: "skipped",
            result: nil,
            reasonCode: "already_kicked_window",
            kicked: false
        )
        XCTAssertEqual(
            KickOutcome.from(envelope: envelope),
            .skipped(message: "Skipping \"codex (a)\": already kicked in this window.")
        )
    }
}

// MARK: - View model

final class StubKickPerformer: KickPerforming, @unchecked Sendable {
    private let lock = NSLock()
    private var results: [Result<TKEnvelope<TKKickResultPayload>, Error>]
    private(set) var performedLabels: [String] = []

    init(_ results: [Result<TKEnvelope<TKKickResultPayload>, Error>]) {
        self.results = results
    }

    func performKick(label: String) async throws -> TKEnvelope<TKKickResultPayload> {
        lock.lock()
        performedLabels.append(label)
        let next = results.isEmpty ? nil : results.removeFirst()
        lock.unlock()
        guard let next else {
            throw NSError(domain: "StubKickPerformer", code: 1)
        }
        return try next.get()
    }
}

@MainActor
final class KickViewModelTests: XCTestCase {
    private var refreshCount = 0

    private func makeViewModel(_ performer: StubKickPerformer) -> KickViewModel {
        refreshCount = 0
        return KickViewModel(performer: performer) { [weak self] in
            self?.refreshCount += 1
        }
    }

    private func kickableSnapshot() throws -> TKSnapshotPayload {
        try ShellFixtures.snapshot(
            accounts: [
                ShellFixtures.accountRow(label: "codex (a)", kickable: true),
                ShellFixtures.accountRow(label: "claude (b)", provider: "claude", kickable: true),
                ShellFixtures.accountRow(
                    label: "gemini (m)",
                    provider: "gemini",
                    state: "active",
                    kickable: false
                ),
                ShellFixtures.accountRow(label: "codex (hidden)", visible: false, kickable: true),
            ]
        )
    }

    func testEligibilitySplitsVisibleRows() throws {
        let snapshot = try kickableSnapshot()
        XCTAssertEqual(
            KickViewModel.eligibleRows(in: snapshot).map(\.label),
            ["codex (a)", "claude (b)"]
        )
        XCTAssertEqual(
            KickViewModel.ineligibleRows(in: snapshot).map(\.label),
            ["gemini (m)"]
        )
    }

    func testIneligibilityText() throws {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [
                {
                    var row = ShellFixtures.accountRow(
                        label: "gemini (m)",
                        provider: "gemini",
                        kickable: false
                    )
                    row["kick_blocked_reason"] = "provider_not_kickable"
                    return row
                }()
            ]
        )
        let row = try XCTUnwrap(SnapshotAccountRow.rows(from: snapshot).first)
        XCTAssertEqual(KickViewModel.ineligibilityText(for: row), "Monitor-only")
    }

    func testRowDisplayProjections() throws {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [
                {
                    var row = ShellFixtures.accountRow(label: "codex (a)", state: "active")
                    row["observed_at"] = "2026-06-12T08:00:00Z"
                    return row
                }(),
                ShellFixtures.accountRow(label: "codex (b)", resetsIn: "reset ready"),
                ShellFixtures.accountRow(label: "codex (c)", resetsIn: "—"),
            ]
        )
        let rows = SnapshotAccountRow.rows(from: snapshot)
        XCTAssertEqual(rows[0].stateDisplay, "Active")
        XCTAssertEqual(rows[0].resetsPhrase, "resets 2h 14m")
        XCTAssertEqual(rows[0].observedAt, parseUTCISO("2026-06-12T08:00:00Z"))
        // Non-duration core phrases stand on their own — never "resets reset ready".
        XCTAssertEqual(rows[1].resetsPhrase, "reset ready")
        XCTAssertNil(rows[2].resetsPhrase)
        XCTAssertNil(rows[2].observedAt)
    }

    func testRequestKickCreatesConfirmationWithoutPerforming() throws {
        let performer = StubKickPerformer([])
        let viewModel = makeViewModel(performer)
        let snapshot = try kickableSnapshot()
        let row = try XCTUnwrap(KickViewModel.eligibleRows(in: snapshot).first)

        viewModel.requestKick(for: row, snapshot: snapshot)

        let action = try XCTUnwrap(viewModel.pendingConfirmation)
        XCTAssertEqual(action.scopeLabel, "codex (a)")
        XCTAssertEqual(performer.performedLabels, [], "request must not kick")
        XCTAssertEqual(refreshCount, 0)
    }

    func testCancelConfirmationPerformsNothing() throws {
        let performer = StubKickPerformer([])
        let viewModel = makeViewModel(performer)
        let snapshot = try kickableSnapshot()
        let row = try XCTUnwrap(KickViewModel.eligibleRows(in: snapshot).first)

        viewModel.requestKick(for: row, snapshot: snapshot)
        viewModel.cancelConfirmation()

        XCTAssertNil(viewModel.pendingConfirmation)
        XCTAssertEqual(performer.performedLabels, [])
        XCTAssertEqual(refreshCount, 0)
        XCTAssertEqual(viewModel.state(for: "codex (a)"), .idle)
    }

    func testConfirmedKickSuccessPath() async throws {
        let performer = StubKickPerformer([
            .success(try ShellFixtures.kickEnvelope(decision: "attempted", result: "confirmed"))
        ])
        let viewModel = makeViewModel(performer)
        let snapshot = try kickableSnapshot()
        let row = try XCTUnwrap(KickViewModel.eligibleRows(in: snapshot).first)

        viewModel.requestKick(for: row, snapshot: snapshot)
        await viewModel.confirmPendingAction()

        XCTAssertEqual(performer.performedLabels, ["codex (a)"])
        XCTAssertNil(viewModel.pendingConfirmation)
        guard case .finished(.confirmed(let message)) = viewModel.state(for: "codex (a)") else {
            return XCTFail("expected confirmed outcome")
        }
        XCTAssertTrue(message.contains("confirmed"))
        XCTAssertEqual(refreshCount, 1, "snapshot refreshes after the mutation")
        XCTAssertEqual(viewModel.lastOutcome?.label, "codex (a)")
    }

    func testUnconfirmedKickKeepsTruthfulWording() async throws {
        let coreMessage = "Codex accepted usage, but session status is still ambiguous"
        let performer = StubKickPerformer([
            .success(
                try ShellFixtures.kickEnvelope(
                    message: coreMessage,
                    decision: "attempted",
                    result: "unconfirmed"
                )
            )
        ])
        let viewModel = makeViewModel(performer)
        let snapshot = try kickableSnapshot()
        let row = try XCTUnwrap(KickViewModel.eligibleRows(in: snapshot).first)

        viewModel.requestKick(for: row, snapshot: snapshot)
        await viewModel.confirmPendingAction()

        guard case .finished(.unconfirmed(let message)) = viewModel.state(for: "codex (a)") else {
            return XCTFail("expected unconfirmed outcome")
        }
        XCTAssertTrue(message.hasPrefix("Attempted —"))
        XCTAssertTrue(message.contains("ambiguous"))
        XCTAssertFalse(message.lowercased().contains("kicked —"), "must not claim confirmation")
        XCTAssertEqual(refreshCount, 1)
    }

    func testFailedKickOutcomeAndRefresh() async throws {
        let performer = StubKickPerformer([
            .success(
                try ShellFixtures.kickEnvelope(
                    ok: false,
                    errorCode: "kick_failed",
                    message: "codex exec failed",
                    decision: "attempted",
                    result: "failed",
                    kicked: false
                )
            )
        ])
        let viewModel = makeViewModel(performer)
        let snapshot = try kickableSnapshot()
        let row = try XCTUnwrap(KickViewModel.eligibleRows(in: snapshot).first)

        viewModel.requestKick(for: row, snapshot: snapshot)
        await viewModel.confirmPendingAction()

        XCTAssertEqual(
            viewModel.state(for: "codex (a)"),
            .finished(.failed(message: "codex exec failed"))
        )
        XCTAssertEqual(refreshCount, 1, "refresh even after failure — state may have changed")
    }

    func testThrownErrorBecomesFailedOutcome() async throws {
        let performer = StubKickPerformer([
            .failure(StubError(description: "tk did not finish within 300s"))
        ])
        let viewModel = makeViewModel(performer)
        let snapshot = try kickableSnapshot()
        let row = try XCTUnwrap(KickViewModel.eligibleRows(in: snapshot).first)

        viewModel.requestKick(for: row, snapshot: snapshot)
        await viewModel.confirmPendingAction()

        guard case .finished(.failed(let message)) = viewModel.state(for: "codex (a)") else {
            return XCTFail("expected failed outcome")
        }
        XCTAssertTrue(message.contains("300s"))
        XCTAssertEqual(refreshCount, 1)
    }

    func testClearResultResetsRowAndLastOutcome() async throws {
        let performer = StubKickPerformer([
            .success(try ShellFixtures.kickEnvelope(decision: "attempted", result: "confirmed"))
        ])
        let viewModel = makeViewModel(performer)
        let snapshot = try kickableSnapshot()
        let row = try XCTUnwrap(KickViewModel.eligibleRows(in: snapshot).first)

        viewModel.requestKick(for: row, snapshot: snapshot)
        await viewModel.confirmPendingAction()
        viewModel.clearResult(for: "codex (a)")

        XCTAssertEqual(viewModel.state(for: "codex (a)"), .idle)
        XCTAssertNil(viewModel.lastOutcome)
    }

    // MARK: Quick Kick

    func testQuickKickConfirmedPathSharesTheKickFlow() async throws {
        let performer = StubKickPerformer([
            .success(try ShellFixtures.kickEnvelope(decision: "attempted", result: "confirmed"))
        ])
        let viewModel = makeViewModel(performer)
        let snapshot = try kickableSnapshot()
        let popover = PopoverModel(snapshot: snapshot, warnings: [])
        guard case .available(let rows) = popover.quickKick else {
            return XCTFail("expected quick kick to be available")
        }

        viewModel.requestKick(for: try XCTUnwrap(rows.first), snapshot: snapshot)
        let action = try XCTUnwrap(viewModel.pendingConfirmation)
        XCTAssertEqual(action.tkArguments, ["kick", "codex (a)", "--json-output", "--yes"])
        XCTAssertNotNil(action.costLine, "quick kick still discloses quota cost")

        await viewModel.confirmPendingAction()
        XCTAssertEqual(performer.performedLabels, ["codex (a)"])
        XCTAssertEqual(refreshCount, 1)
    }

    func testQuickKickDisabledStateRetainsReasonAndNoActions() throws {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [
                ShellFixtures.accountRow(
                    label: "codex (a)",
                    state: "active",
                    kickable: false,
                    resetsIn: "2h 14m"
                )
            ]
        )
        let popover = PopoverModel(snapshot: snapshot, warnings: [])
        guard case .disabled(let reason) = popover.quickKick else {
            return XCTFail("expected disabled quick kick")
        }
        XCTAssertTrue(reason.contains("No fresh windows"))
    }
}
