import XCTest
@testable import TokenKickKit

/// End-to-end proof across the real process boundary: the bundled `tk`
/// (PyInstaller output of scripts/build-bundled-tk.sh) answers
/// `tk app snapshot`, and an external pipx tk is never the one executing.
///
/// Gated: set TK_BUNDLED_RUNTIME=/path/to/dist/tokenkick-runtime/tk.
final class BundledRuntimeIntegrationTests: XCTestCase {
    private func bundledRuntime() throws -> URL {
        guard
            let path = ProcessInfo.processInfo.environment["TK_BUNDLED_RUNTIME"],
            !path.isEmpty
        else {
            throw XCTSkip(
                "TK_BUNDLED_RUNTIME not set — run scripts/build-bundled-tk.sh, then "
                    + "TK_BUNDLED_RUNTIME=$PWD/dist/tokenkick-runtime/tk swift test"
            )
        }
        return try TKRuntimeLocator.bundledTkURL(
            environment: [TKRuntimeLocator.environmentOverrideKey: path]
        )
    }

    private func isolatedHome() throws -> URL {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("tk-integration-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: home)
        }
        return home
    }

    func testSnapshotCrossesProcessBoundaryFromBundledRuntime() async throws {
        let runtime = try bundledRuntime()
        let home = try isolatedHome()
        let client = TKClient(
            runtime: runtime,
            environment: TKEnvironment.subprocessEnvironment(home: home.path),
            timeout: 120
        )

        let envelope = try await client.snapshot()
        XCTAssertTrue(envelope.ok)
        XCTAssertEqual(envelope.schemaVersion, 1)
        let payload = try XCTUnwrap(envelope.payload)

        // The answering runtime is the bundled one.
        if let expected = TKRuntimeLocator.runtimeVersion(forRuntimeAt: runtime) {
            XCTAssertEqual(
                payload.core.version,
                expected,
                "bundled runtime answered with a different core version"
            )
        }
        let executable = try XCTUnwrap(payload.core.executable)
        XCTAssertEqual(
            URL(fileURLWithPath: executable).resolvingSymlinksInPath().path,
            runtime.resolvingSymlinksInPath().path,
            "snapshot was answered by a tk other than the bundled runtime"
        )

        // External pipx tk may exist on PATH but must never be the runtime.
        if let externalTk = payload.runtime.externalTk {
            XCTAssertFalse(
                externalTk.isCurrentRuntime,
                "external tk (\(externalTk.path)) answered the snapshot"
            )
        }

        // Snapshot state is the isolated HOME, not the user's real state.
        let configDir = try XCTUnwrap(payload.paths["config_dir"])
        XCTAssertTrue(
            configDir.hasPrefix(home.path) || configDir.hasPrefix("/private" + home.path),
            "snapshot read state from \(configDir) instead of the isolated HOME"
        )
    }

    func testDaemonStatusThroughBundledRuntime() async throws {
        let runtime = try bundledRuntime()
        let home = try isolatedHome()
        let client = TKClient(
            runtime: runtime,
            environment: TKEnvironment.subprocessEnvironment(home: home.path),
            timeout: 120
        )
        let envelope = try await client.daemonStatus()
        XCTAssertTrue(envelope.ok)
        let daemon = try XCTUnwrap(envelope.payload).daemon
        XCTAssertFalse(daemon.running)
    }

    func testErrorEnvelopeThroughBundledRuntime() async throws {
        let runtime = try bundledRuntime()
        let home = try isolatedHome()
        let client = TKClient(
            runtime: runtime,
            environment: TKEnvironment.subprocessEnvironment(home: home.path),
            timeout: 120
        )
        let envelope = try await client.envelope(
            TKJSONValue.self,
            arguments: ["accounts", "hide", "nope", "--json-output"]
        )
        XCTAssertFalse(envelope.ok)
        XCTAssertEqual(envelope.errorCode, "mutation_failed")
    }

    func testKickEnvelopeThroughBundledRuntime() async throws {
        let runtime = try bundledRuntime()
        let home = try isolatedHome()
        let client = TKClient(
            runtime: runtime,
            environment: TKEnvironment.subprocessEnvironment(home: home.path),
            timeout: 120
        )
        // Isolated HOME has no accounts: the kick interface answers with a
        // typed error envelope instead of kicking anything.
        let envelope = try await client.kick(label: "nope")
        XCTAssertFalse(envelope.ok)
        XCTAssertEqual(envelope.errorCode, "no_accounts")
    }

    func testSetupStreamThroughBundledRuntime() async throws {
        let runtime = try bundledRuntime()
        let home = try isolatedHome()
        var environment = TKEnvironment.subprocessEnvironment(home: home.path)
        // No provider CLIs on PATH: discovery deterministically finds nothing.
        environment["PATH"] = "/usr/bin:/bin"

        let stream = try TKProcessRunner().streamLines(
            executable: runtime,
            arguments: ["app", "setup", "--json-lines"],
            environment: environment
        )
        var events: [TKSetupEvent] = []
        for try await line in stream.lines {
            events.append(try TKSetupStream.event(fromLine: line))
        }
        let exit = await stream.waitForExit()

        XCTAssertEqual(events.first?.event, "setup_started")
        let terminal = try TKSetupStream.terminalEvent(in: events)
        XCTAssertEqual(terminal.event, "setup_completed")
        XCTAssertEqual(terminal.ok, true)
        XCTAssertEqual(terminal.payload?["config_saved"]?.boolValue, false)
        XCTAssertEqual(exit.exitCode, 0)
    }
}
