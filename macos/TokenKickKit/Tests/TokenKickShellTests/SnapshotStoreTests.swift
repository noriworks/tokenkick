import XCTest
import TokenKickKit
@testable import TokenKickShell

@MainActor
final class SnapshotStoreTests: XCTestCase {
    func testRefreshSuccessLoadsSnapshot() async throws {
        let envelope = try ShellFixtures.envelope(
            warnings: ["No readable status cache; run setup or a status refresh."],
            daemonRunning: true,
            daemonVersion: "1.9.14",
            versionMatch: true,
            executableMatch: true
        )
        let store = SnapshotStore(
            provider: SequenceProvider([.success(envelope)]),
            now: { Date(timeIntervalSince1970: 1_000) }
        )

        XCTAssertEqual(store.phase, .initial)
        await store.refresh()

        XCTAssertEqual(store.phase, .loaded)
        XCTAssertNotNil(store.snapshot)
        XCTAssertEqual(store.lastUpdated, Date(timeIntervalSince1970: 1_000))
        XCTAssertNil(store.lastError)
        XCTAssertFalse(store.isDegraded)
        XCTAssertEqual(store.envelopeWarnings.count, 1)
    }

    func testFirstRefreshFailureIsBlocker() async {
        let store = SnapshotStore(
            provider: SequenceProvider([.failure(StubError(description: "runtime missing"))])
        )

        await store.refresh()

        XCTAssertEqual(store.phase, .failed)
        XCTAssertNil(store.snapshot)
        XCTAssertNotNil(store.lastError)
        let items = store.warningItems
        XCTAssertEqual(items.first?.tier, .blocker)
        XCTAssertEqual(items.first?.id, "core-unreachable")
        XCTAssertEqual(store.menuBarIndicator, .blocker)
    }

    func testFailureAfterSuccessKeepsSnapshotAsDegraded() async throws {
        let envelope = try ShellFixtures.envelope(
            daemonRunning: true,
            daemonVersion: "1.9.14",
            versionMatch: true,
            executableMatch: true
        )
        let store = SnapshotStore(
            provider: SequenceProvider([
                .success(envelope),
                .failure(StubError(description: "timeout")),
            ])
        )

        await store.refresh()
        XCTAssertEqual(store.phase, .loaded)
        await store.refresh()

        XCTAssertEqual(store.phase, .loaded, "degraded data stays on screen")
        XCTAssertNotNil(store.snapshot)
        XCTAssertTrue(store.isDegraded)
        XCTAssertTrue(store.warningItems.contains { $0.id == "refresh-failed" && $0.tier == .warning })
        XCTAssertEqual(store.menuBarIndicator, .warning)
    }

    func testSuccessAfterDegradedClearsError() async throws {
        let envelope = try ShellFixtures.envelope(
            daemonRunning: true,
            daemonVersion: "1.9.14",
            versionMatch: true,
            executableMatch: true
        )
        let store = SnapshotStore(
            provider: SequenceProvider([
                .success(envelope),
                .failure(StubError(description: "timeout")),
                .success(envelope),
            ])
        )

        await store.refresh()
        await store.refresh()
        XCTAssertTrue(store.isDegraded)
        await store.refresh()

        XCTAssertFalse(store.isDegraded)
        XCTAssertNil(store.lastError)
        XCTAssertFalse(store.warningItems.contains { $0.id == "refresh-failed" })
    }

    func testEnvelopeNotOKWithoutSnapshotFails() async throws {
        let errorEnvelope = try ShellFixtures.envelope(
            ok: false,
            errorCode: "state_file_error",
            message: "TokenKick config is not valid JSON."
        )
        let store = SnapshotStore(provider: SequenceProvider([.success(errorEnvelope)]))

        await store.refresh()

        XCTAssertEqual(store.phase, .failed)
        XCTAssertEqual(store.lastError, "TokenKick config is not valid JSON.")
        XCTAssertEqual(store.warningItems.first?.tier, .blocker)
    }

    func testAutoRefreshCanBeStartedAndStopped() async throws {
        let envelope = try ShellFixtures.envelope(daemonRunning: true, versionMatch: true)
        let store = SnapshotStore(provider: SequenceProvider([.success(envelope)]))
        store.setAutoRefresh(every: 3600)
        store.setAutoRefresh(every: nil)
        // No assertion beyond "does not crash/leak": the timer task is
        // cancelled; refresh behavior itself is covered above.
        XCTAssertEqual(store.phase, .initial)
    }
}
