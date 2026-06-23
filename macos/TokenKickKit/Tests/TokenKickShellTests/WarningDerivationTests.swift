import XCTest
import TokenKickKit
@testable import TokenKickShell

final class WarningDerivationTests: XCTestCase {
    private func items(
        snapshot: TKSnapshotPayload?,
        envelopeOK: Bool = true,
        envelopeWarnings: [String] = [],
        fetchError: String? = nil
    ) -> [WarningItem] {
        WarningDerivation.items(
            from: WarningInputs(
                snapshot: snapshot,
                envelopeOK: envelopeOK,
                envelopeWarnings: envelopeWarnings,
                fetchError: fetchError
            )
        )
    }

    func testHealthySnapshotHasNoWarnings() throws {
        let snapshot = try ShellFixtures.snapshot()
        XCTAssertEqual(items(snapshot: snapshot), [])
        XCTAssertEqual(WarningDerivation.menuBarIndicator(for: []), .normal)
    }

    func testFetchErrorWithoutSnapshotIsBlockerOnly() {
        let result = items(snapshot: nil, fetchError: "boom")
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first?.tier, .blocker)
        XCTAssertEqual(result.first?.id, "core-unreachable")
    }

    func testDaemonStoppedWarnsOnlyWhenAutoKickConfigured() throws {
        let withAutoKick = try ShellFixtures.snapshot(
            daemonRunning: false,
            daemonVersion: nil,
            versionMatch: nil,
            executable: nil,
            executableMatch: nil,
            accounts: [ShellFixtures.accountRow(label: "codex (a)", autoKick: true)]
        )
        XCTAssertTrue(
            items(snapshot: withAutoKick).contains {
                $0.id == "daemon-not-running" && $0.tier == .warning && $0.destination == .daemon
            }
        )

        let withoutAutoKick = try ShellFixtures.snapshot(
            daemonRunning: false,
            daemonVersion: nil,
            versionMatch: nil,
            executable: nil,
            executableMatch: nil,
            accounts: [ShellFixtures.accountRow(label: "codex (a)", autoKick: false)]
        )
        XCTAssertFalse(
            items(snapshot: withoutAutoKick).contains { $0.id == "daemon-not-running" }
        )
    }

    func testStalePidfileWarns() throws {
        let snapshot = try ShellFixtures.snapshot(
            daemonRunning: false,
            daemonVersion: nil,
            versionMatch: nil,
            executable: nil,
            executableMatch: nil,
            stalePidfile: true
        )
        XCTAssertTrue(
            items(snapshot: snapshot).contains {
                $0.id == "daemon-stale-pidfile" && $0.tier == .warning
            }
        )
    }

    func testVersionAndExecutableMismatchWarn() throws {
        let snapshot = try ShellFixtures.snapshot(
            daemonVersion: "1.9.13",
            versionMatch: false,
            executable: "/Users/fixture/.local/pipx/tk",
            executableMatch: false
        )
        let result = items(snapshot: snapshot)
        XCTAssertTrue(result.contains { $0.id == "daemon-version-mismatch" && $0.tier == .warning })
        XCTAssertTrue(result.contains { $0.id == "daemon-executable-mismatch" && $0.tier == .warning })
        XCTAssertTrue(result.allSatisfy { $0.destination == .daemon })
    }

    func testEnvelopeWarningMapping() throws {
        let snapshot = try ShellFixtures.snapshot()
        let result = items(
            snapshot: snapshot,
            envelopeWarnings: [
                "External tk v1.7.6 on PATH differs from this runtime v1.9.14.",
                "No readable status cache; run setup or a status refresh.",
                "Something new the core wants to say.",
            ]
        )
        XCTAssertEqual(result.first { $0.id == "envelope-external-tk" }?.tier, .footnote)
        XCTAssertEqual(result.first { $0.id == "envelope-status-cache" }?.tier, .warning)
        XCTAssertEqual(result.first { $0.id == "envelope-status-cache" }?.destination, .status)
        XCTAssertEqual(result.first { $0.id == "envelope-2" }?.tier, .advisory)
    }

    func testAdvisoriesAreAdvisoryTier() throws {
        let snapshot = try ShellFixtures.snapshot(
            advisories: [["message": "Quiet period starts soon for codex (a)."]]
        )
        let result = items(snapshot: snapshot)
        XCTAssertEqual(result.count, 1)
        XCTAssertEqual(result.first?.tier, .advisory)
        XCTAssertEqual(result.first?.detail, "Quiet period starts soon for codex (a).")
        XCTAssertEqual(WarningDerivation.menuBarIndicator(for: result), .normal)
    }

    func testUnacknowledgedResetObservationIsAdvisory() throws {
        let unacknowledged = try ShellFixtures.snapshot(
            resetObservations: [["provider": "codex", "acknowledged": false]]
        )
        XCTAssertTrue(
            items(snapshot: unacknowledged).contains {
                $0.id == "reset-observation" && $0.tier == .advisory
            }
        )
        XCTAssertEqual(WarningDerivation.menuBarIndicator(for: items(snapshot: unacknowledged)), .normal)

        let acknowledged = try ShellFixtures.snapshot(
            resetObservations: [["provider": "codex", "acknowledged": true]]
        )
        XCTAssertFalse(
            items(snapshot: acknowledged).contains { $0.id == "reset-observation" }
        )
    }

    func testSortedByTierAndIndicatorMatchesHighest() throws {
        let snapshot = try ShellFixtures.snapshot(
            daemonVersion: "1.9.13",
            versionMatch: false,
            advisories: [["message": "advisory"]]
        )
        let result = items(
            snapshot: snapshot,
            envelopeWarnings: ["External tk v1.7.6 on PATH differs from this runtime v1.9.14."]
        )
        let tiers = result.map(\.tier.rawValue)
        XCTAssertEqual(tiers, tiers.sorted())
        XCTAssertEqual(WarningDerivation.menuBarIndicator(for: result), .warning)
    }
}
