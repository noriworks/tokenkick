import XCTest
import TokenKickKit
@testable import TokenKickShell

final class DaemonPresentationTests: XCTestCase {
    private func daemon(
        running: Bool,
        versionMatch: Bool? = nil,
        executable: String? = nil,
        executableMatch: Bool? = nil,
        stalePidfile: Bool = false
    ) throws -> TKDaemonStatus {
        try ShellFixtures.snapshot(
            daemonRunning: running,
            daemonVersion: running ? "1.9.14" : nil,
            versionMatch: versionMatch,
            executable: executable,
            executableMatch: executableMatch,
            stalePidfile: stalePidfile
        ).daemon
    }

    // MARK: - Toolbar chip

    func testChipUnknownWithoutSnapshot() {
        let chip = DaemonChipState.derive(from: nil)
        XCTAssertEqual(chip.kind, .unknown)
        XCTAssertFalse(chip.hasIssue)
    }

    func testChipRunningClean() throws {
        let chip = DaemonChipState.derive(
            from: try daemon(running: true, versionMatch: true, executableMatch: true)
        )
        XCTAssertEqual(chip.kind, .running)
        XCTAssertEqual(chip.title, "Running")
        XCTAssertFalse(chip.hasIssue)
    }

    func testChipRunningWithMismatchHasIssue() throws {
        let versionMismatch = DaemonChipState.derive(
            from: try daemon(running: true, versionMatch: false, executableMatch: true)
        )
        XCTAssertEqual(versionMismatch.kind, .running)
        XCTAssertTrue(versionMismatch.hasIssue)

        let executableMismatch = DaemonChipState.derive(
            from: try daemon(running: true, versionMatch: true, executableMatch: false)
        )
        XCTAssertTrue(executableMismatch.hasIssue)
    }

    func testChipStoppedAndStale() throws {
        XCTAssertEqual(DaemonChipState.derive(from: try daemon(running: false)).kind, .stopped)
        let stale = DaemonChipState.derive(from: try daemon(running: false, stalePidfile: true))
        XCTAssertEqual(stale.kind, .stale)
        XCTAssertTrue(stale.hasIssue)
    }

    // MARK: - Ownership chip (Daemon screen)

    func testOwnershipAppManaged() throws {
        let ownership = DaemonOwnershipPresentation.derive(
            from: try daemon(running: true, executable: "/Users/fixture/tk", executableMatch: true)
        )
        XCTAssertEqual(ownership.kind, .appManaged)
        XCTAssertEqual(ownership.title, "Managed by TokenKick")
    }

    func testOwnershipTerminalManaged() throws {
        let ownership = DaemonOwnershipPresentation.derive(
            from: try daemon(
                running: true,
                executable: "/Users/fixture/.local/pipx/venvs/tokenkick/bin/tk",
                executableMatch: false
            )
        )
        XCTAssertEqual(ownership.kind, .terminalManaged)
        XCTAssertEqual(ownership.title, "Managed by terminal")
        XCTAssertTrue(ownership.detail.contains("pipx"))
        XCTAssertTrue(ownership.detail.contains("won't interfere"))
    }

    func testOwnershipUnknownWhenExecutableUntracked() throws {
        let ownership = DaemonOwnershipPresentation.derive(
            from: try daemon(running: true, executable: nil, executableMatch: nil)
        )
        XCTAssertEqual(ownership.kind, .unknownRunning)
    }

    func testOwnershipStaleAndNotRunning() throws {
        XCTAssertEqual(
            DaemonOwnershipPresentation.derive(
                from: try daemon(running: false, stalePidfile: true)
            ).kind,
            .stale
        )
        XCTAssertEqual(
            DaemonOwnershipPresentation.derive(from: try daemon(running: false)).kind,
            .notRunning
        )
        XCTAssertEqual(DaemonOwnershipPresentation.derive(from: nil).kind, .notRunning)
    }
}
