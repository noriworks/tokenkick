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

    func testWeeklyOnlyPauseResumeThroughBundledRuntime() async throws {
        let runtime = try bundledRuntime()
        let home = try isolatedHome()
        let directory = home.appendingPathComponent(".tokenkick")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let config: [String: Any] = ["accounts": [[
            "label": "fixture", "provider": "codex", "source": "manual",
            "auto_kick": false, "weekly_auto_kick": false, "session_auto_kick": false,
        ]]]
        try JSONSerialization.data(withJSONObject: config).write(to: directory.appendingPathComponent("config.json"))
        let client = TKClient(
            runtime: runtime,
            environment: TKEnvironment.subprocessEnvironment(home: home.path),
            timeout: 120
        )
        for command in [
            ["auto", "weekly", "enable"], ["auto", "disable"], ["auto", "enable"],
        ] {
            let enabled = command.last == "enable"
            let result = try await client.envelope(TKJSONValue.self, arguments:
                command + ["fixture", "--json-output"] + (enabled ? ["--accept-risk", "ENABLE"] : []))
            XCTAssertTrue(result.ok, result.message ?? "Mutation failed")
            let envelope = try await client.accountsList()
            let account = try XCTUnwrap(envelope.payload?.accounts.first)
            XCTAssertEqual(account.autoKick, enabled)
            XCTAssertTrue(account.weeklyAutoKick)
            XCTAssertFalse(account.sessionAutoKick)
            XCTAssertEqual(account.weeklyAutomationState, enabled ? "enabled" : "paused")
        }
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
        // PATH alone is insufficient isolation: Antigravity discovery also
        // searches the passwd home and standard install paths. Select a fake
        // executable explicitly so this integration test never invokes one.
        let fakeProvider = home.appendingPathComponent("agy")
        try "#!/bin/sh\nexit 1\n".write(to: fakeProvider, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: fakeProvider.path)
        var environment = TKEnvironment.subprocessEnvironment(
            base: ["PATH": "/usr/bin:/bin"], home: home.path, pathAdditions: [])
        environment["ANTIGRAVITY_CLI_PATH"] = fakeProvider.path

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
        XCTAssertEqual(terminal.payload?["config_saved"]?.boolValue, true)
        let saved = try Data(contentsOf: home.appendingPathComponent(".tokenkick/config.json"))
        let config = try XCTUnwrap(JSONSerialization.jsonObject(with: saved) as? [String: Any])
        let accounts = try XCTUnwrap(config["accounts"] as? [[String: Any]])
        XCTAssertEqual(accounts.count, 2)
        XCTAssertEqual(Set(accounts.compactMap { $0["antigravity_family"] as? String }),
                       Set(["gemini", "claude_gpt"]))
        XCTAssertEqual(Set(accounts.compactMap { $0["label"] as? String }).count, 2)
        for account in accounts {
            XCTAssertEqual(account["provider"] as? String, "antigravity")
            XCTAssertFalse(account["auto_kick"] as? Bool ?? true)
        }
        XCTAssertEqual(exit.exitCode, 0)
    }
}
