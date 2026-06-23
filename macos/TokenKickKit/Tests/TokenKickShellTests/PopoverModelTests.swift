import XCTest
import TokenKickKit
@testable import TokenKickShell

final class PopoverModelTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_750_000_000)

    func testWaitingHeaderWithoutSnapshot() {
        let model = PopoverModel(snapshot: nil, warnings: [], now: now)
        XCTAssertEqual(model.headerStateLine, "Waiting for first status…")
        XCTAssertEqual(model.quickKick, .disabled(reason: "No accounts yet"))
        XCTAssertNil(model.nextActionLine)
        XCTAssertFalse(model.cancelPlanVisible)
    }

    func testAllQuietHeader() throws {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [ShellFixtures.accountRow(label: "codex (a)", kickable: false)]
        )
        let model = PopoverModel(
            snapshot: snapshot,
            warnings: [],
            now: now
        )
        XCTAssertEqual(model.headerStateLine, "All quiet")
    }

    func testWarningsHeaderCountsActionableOnly() throws {
        let snapshot = try ShellFixtures.snapshot()
        let warnings = [
            WarningItem(id: "a", tier: .warning, title: "Warning A"),
            WarningItem(id: "b", tier: .warning, title: "Warning B"),
            WarningItem(id: "info", tier: .advisory, title: "Info"),
            WarningItem(id: "c", tier: .footnote, title: "Footnote"),
        ]
        let model = PopoverModel(snapshot: snapshot, warnings: warnings, now: now)
        XCTAssertEqual(model.headerStateLine, "2 warnings")
        XCTAssertEqual(model.topWarning?.id, "a")
        XCTAssertEqual(model.additionalWarningCount, 1)
    }

    func testAdvisoryOnlyHeaderUsesNoticeLanguage() throws {
        let snapshot = try ShellFixtures.snapshot()
        let warnings = [
            WarningItem(id: "reset-observation", tier: .advisory, title: "Reset observed on Codex"),
            WarningItem(id: "c", tier: .footnote, title: "Footnote"),
        ]
        let model = PopoverModel(snapshot: snapshot, warnings: warnings, now: now)
        XCTAssertEqual(model.headerStateLine, "1 notice")
        XCTAssertEqual(model.topWarning?.id, "reset-observation")
        XCTAssertEqual(model.additionalWarningCount, 0)
    }

    func testQuickKickAvailableListsKickableVisibleAccounts() throws {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [
                ShellFixtures.accountRow(label: "codex (a)", kickable: true),
                ShellFixtures.accountRow(label: "codex (hidden)", visible: false, kickable: true),
                ShellFixtures.accountRow(label: "gemini (m)", provider: "gemini", kickable: false),
            ]
        )
        let model = PopoverModel(snapshot: snapshot, warnings: [], now: now)
        guard case .available(let rows) = model.quickKick else {
            return XCTFail("expected available quick kick")
        }
        XCTAssertEqual(rows.map(\.label), ["codex (a)"])
    }

    func testQuickKickDisabledWithResetReason() throws {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [
                ShellFixtures.accountRow(
                    label: "codex (a)",
                    state: "active",
                    kickable: false,
                    resetsIn: "1h 02m"
                )
            ]
        )
        let model = PopoverModel(snapshot: snapshot, warnings: [], now: now)
        guard case .disabled(let reason) = model.quickKick else {
            return XCTFail("expected disabled quick kick")
        }
        XCTAssertTrue(reason.contains("No fresh windows"))
        XCTAssertTrue(reason.contains("1h 02m"))
    }

    func testAccountRowOverflowCapsAtSix() throws {
        let rows = (1...8).map { ShellFixtures.accountRow(label: "codex (\($0))") }
        let snapshot = try ShellFixtures.snapshot(accounts: rows)
        let model = PopoverModel(snapshot: snapshot, warnings: [], now: now)
        XCTAssertEqual(model.accountRows.count, 6)
        XCTAssertEqual(model.overflowAccountCount, 2)
    }

    func testNextActionLineAndCancelPlanForOrchestratedKick() throws {
        let kickAt = ISO8601DateFormatter().string(
            from: now.addingTimeInterval(2 * 3600)
        )
        let snapshot = try ShellFixtures.snapshot(
            pendingKicks: [
                ShellFixtures.pendingKick(
                    accountLabel: "codex (a)",
                    kickAt: kickAt,
                    reason: "orchestrated"
                )
            ]
        )
        let model = PopoverModel(snapshot: snapshot, warnings: [], now: now)
        let line = try XCTUnwrap(model.nextActionLine)
        XCTAssertTrue(line.contains("codex (a)"))
        XCTAssertTrue(line.contains("(orchestrated)"))
        XCTAssertTrue(model.cancelPlanVisible)
        XCTAssertTrue(model.headerStateLine.hasPrefix("All quiet — next kick"))
    }

    func testRowProjectionReadsCoreKeys() throws {
        let snapshot = try ShellFixtures.snapshot(
            accounts: [
                ShellFixtures.accountRow(
                    label: "codex (a)",
                    state: "active",
                    usedPercent: 42.5,
                    resetsIn: "3h 10m",
                    stale: true
                )
            ]
        )
        let row = try XCTUnwrap(SnapshotAccountRow.rows(from: snapshot).first)
        XCTAssertEqual(row.label, "codex (a)")
        XCTAssertEqual(row.providerBadge, "CX")
        XCTAssertEqual(row.state, "active")
        XCTAssertEqual(row.usedPercent, 42.5)
        XCTAssertEqual(row.resetsInText, "3h 10m")
        XCTAssertTrue(row.stale)
    }
}
