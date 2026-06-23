import XCTest
@testable import TokenKickKit

final class MockLaunchctl: TKLaunchctl {
    var calls: [[String]] = []
    var loaded = false
    var failNext: [String: TKProcessResult] = [:]

    func run(_ arguments: [String]) async throws -> TKProcessResult {
        calls.append(arguments)
        if let result = failNext[arguments.first ?? ""] {
            return result
        }
        switch arguments.first {
        case "print":
            return loaded ? .success(stdout: "service loaded\n") : .failure(stderr: "not loaded\n")
        case "bootstrap":
            loaded = true
            return .success()
        case "kickstart":
            return .success()
        case "bootout":
            loaded = false
            return .success()
        default:
            return .success()
        }
    }
}

final class FakeDaemonClient: TKDaemonCommanding {
    var stopCalls = 0
    var stopEnvelope: TKEnvelope<TKDaemonActionPayload>

    init(stopEnvelope: TKEnvelope<TKDaemonActionPayload>) {
        self.stopEnvelope = stopEnvelope
    }

    func stopDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        stopCalls += 1
        return stopEnvelope
    }
}

private extension TKProcessResult {
    static func success(stdout: String = "", stderr: String = "") -> TKProcessResult {
        TKProcessResult(
            exitCode: 0,
            stdout: Data(stdout.utf8),
            stderr: Data(stderr.utf8)
        )
    }

    static func failure(exitCode: Int32 = 1, stdout: String = "", stderr: String = "") -> TKProcessResult {
        TKProcessResult(
            exitCode: exitCode,
            stdout: Data(stdout.utf8),
            stderr: Data(stderr.utf8)
        )
    }
}

final class LaunchAgentTests: XCTestCase {
    private func temporaryHome() throws -> URL {
        let home = FileManager.default.temporaryDirectory
            .appendingPathComponent("tk-launch-agent-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true)
        addTeardownBlock {
            try? FileManager.default.removeItem(at: home)
        }
        return home
    }

    private func makeRuntime(_ home: URL, name: String = "tk") throws -> URL {
        let runtime = home.appendingPathComponent(name, isDirectory: false)
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: runtime)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: runtime.path)
        return runtime
    }

    private func daemon(
        running: Bool,
        executable: String? = nil,
        versionMatch: Bool? = true,
        executableMatch: Bool? = true,
        stalePidfile: Bool = false
    ) -> TKDaemonStatus {
        TKDaemonStatus(
            running: running,
            pid: running ? 4242 : nil,
            version: running ? "1.9.14" : nil,
            executable: executable,
            installedVersion: "1.9.14",
            versionMatch: running ? versionMatch : nil,
            executableMatch: running ? executableMatch : nil,
            pidfileExists: running || stalePidfile,
            stalePidfile: stalePidfile,
            uptimeSeconds: running ? 10 : nil,
            pollIntervalMinutes: 5,
            pidfilePath: "/Users/fixture/.tokenkick/daemon.pid",
            logPath: "/Users/fixture/.tokenkick/daemon.log"
        )
    }

    private func stoppedEnvelope() -> TKEnvelope<TKDaemonActionPayload> {
        let daemon = daemon(running: false)
        let payload = TKDaemonActionPayload(
            action: "stop",
            daemon: daemon,
            started: nil,
            stopped: true,
            restarted: nil,
            alreadyRunning: nil,
            wasRunning: true
        )
        return TKEnvelope(
            schemaVersion: 1,
            ok: true,
            errorCode: nil,
            message: "stopped",
            warnings: [],
            payload: payload
        )
    }

    func testInstallWritesStableHelperRuntimePathAndPlist() throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: MockLaunchctl())

        let status = try manager.install()

        XCTAssertTrue(status.installed)
        XCTAssertTrue(status.runtimePathMatches)
        XCTAssertTrue(status.plistProgramMatchesHelper)
        XCTAssertFalse(status.needsRepair)
        XCTAssertEqual(
            try String(contentsOf: manager.runtimePathURL).trimmingCharacters(in: .whitespacesAndNewlines),
            runtime.path
        )
        XCTAssertTrue(try String(contentsOf: manager.helperURL).contains("exec \"$RUNTIME\" daemon"))

        let plist = try readPlist(manager.plistURL)
        XCTAssertEqual(plist["Label"] as? String, TKLaunchAgentManager.label)
        XCTAssertEqual(plist["ProgramArguments"] as? [String], [manager.helperURL.path])
        XCTAssertEqual(plist["RunAtLoad"] as? Bool, true)
        XCTAssertEqual(plist["KeepAlive"] as? Bool, false)
        XCTAssertNotNil((plist["EnvironmentVariables"] as? [String: String])?["PATH"])
    }

    func testStartBootstrapsAndKickstartsLaunchAgent() async throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let launchctl = MockLaunchctl()
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: launchctl)

        _ = try await manager.start()

        XCTAssertEqual(launchctl.calls, [
            ["bootstrap", manager.launchDomain, manager.plistURL.path],
            ["kickstart", "-k", manager.serviceTarget],
            ["print", manager.serviceTarget],
        ])
    }

    func testStopBootsOutLoadedLaunchAgent() async throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let launchctl = MockLaunchctl()
        launchctl.loaded = true
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: launchctl)

        _ = try await manager.stop()

        XCTAssertEqual(launchctl.calls.first, ["bootout", manager.serviceTarget])
        XCTAssertFalse(launchctl.loaded)
    }

    func testRemoveBootsOutAndDeletesInstalledFiles() async throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let launchctl = MockLaunchctl()
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: launchctl)
        _ = try manager.install()

        let status = try await manager.remove()

        XCTAssertFalse(status.installed)
        XCTAssertFalse(FileManager.default.fileExists(atPath: manager.plistURL.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: manager.helperURL.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: manager.runtimePathURL.path))
        XCTAssertEqual(launchctl.calls.first, ["bootout", manager.serviceTarget])
    }

    func testStatusReportsStalePidfileAndRuntimeMismatches() async throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: MockLaunchctl())

        let status = await manager.status(
            daemon: daemon(
                running: true,
                executable: "/opt/pipx/bin/tk",
                versionMatch: false,
                executableMatch: false,
                stalePidfile: true
            )
        )

        XCTAssertEqual(status.daemonOwnership, .stalePidfile)
        XCTAssertTrue(status.stalePidfile)
        XCTAssertTrue(status.versionMismatch)
        XCTAssertTrue(status.executablePathMismatch)
    }

    func testDuplicateTerminalManagedDaemonRequiresExplicitTakeover() async throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: MockLaunchctl())

        do {
            _ = try await manager.start(
                daemon: daemon(running: true, executable: "/opt/pipx/bin/tk", executableMatch: false),
                takeover: false
            )
            XCTFail("expected takeover requirement")
        } catch {
            guard case TKLaunchAgentError.takeoverRequired = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }

    func testUnknownRunningDaemonRequiresExplicitTakeover() async throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: MockLaunchctl())

        do {
            _ = try await manager.start(
                daemon: daemon(running: true, executable: nil, executableMatch: nil),
                takeover: false
            )
            XCTFail("expected takeover requirement")
        } catch {
            guard case TKLaunchAgentError.takeoverRequired = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }

    func testAlreadyAppManagedDaemonDoesNotStartDuplicate() async throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let launchctl = MockLaunchctl()
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: launchctl)

        let status = try await manager.start(
            daemon: daemon(running: true, executable: runtime.path, executableMatch: true),
            takeover: false
        )

        XCTAssertEqual(status.daemonOwnership, .appManaged)
        XCTAssertFalse(launchctl.calls.contains { $0.first == "bootstrap" || $0.first == "kickstart" })
        XCTAssertTrue(FileManager.default.fileExists(atPath: manager.plistURL.path))
    }

    func testTakeoverStopsTerminalManagedDaemonBeforeBootstrap() async throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let launchctl = MockLaunchctl()
        let daemonClient = FakeDaemonClient(stopEnvelope: stoppedEnvelope())
        let manager = TKLaunchAgentManager(
            runtime: runtime,
            home: home,
            launchctl: launchctl,
            daemonClient: daemonClient
        )

        _ = try await manager.start(
            daemon: daemon(running: true, executable: "/opt/pipx/bin/tk", executableMatch: false),
            takeover: true
        )

        XCTAssertEqual(daemonClient.stopCalls, 1)
        XCTAssertEqual(launchctl.calls.first, ["bootstrap", manager.launchDomain, manager.plistURL.path])
    }

    func testRepairUpdatesRuntimePathAfterRelocation() throws {
        let home = try temporaryHome()
        let oldRuntime = try makeRuntime(home, name: "old-tk")
        let newRuntime = try makeRuntime(home, name: "new-tk")
        let oldManager = TKLaunchAgentManager(runtime: oldRuntime, home: home, launchctl: MockLaunchctl())
        _ = try oldManager.install()

        let newManager = TKLaunchAgentManager(runtime: newRuntime, home: home, launchctl: MockLaunchctl())
        let before = try String(contentsOf: newManager.runtimePathURL)
        XCTAssertTrue(before.contains(oldRuntime.path))

        let repaired = try newManager.repair()

        XCTAssertTrue(repaired.runtimePathMatches)
        XCTAssertEqual(
            try String(contentsOf: newManager.runtimePathURL).trimmingCharacters(in: .whitespacesAndNewlines),
            newRuntime.path
        )
    }

    func testLaunchAgentPlistPointsAtHelperNotMovableAppRuntime() throws {
        let home = try temporaryHome()
        let runtime = try makeRuntime(home)
        let manager = TKLaunchAgentManager(runtime: runtime, home: home, launchctl: MockLaunchctl())

        let plist = manager.plistDictionary()

        XCTAssertEqual(plist["ProgramArguments"] as? [String], [manager.helperURL.path])
        XCTAssertNotEqual(plist["ProgramArguments"] as? [String], [runtime.path])
    }

    private func readPlist(_ url: URL) throws -> [String: Any] {
        let data = try Data(contentsOf: url)
        let plist = try PropertyListSerialization.propertyList(
            from: data,
            options: [],
            format: nil
        )
        return try XCTUnwrap(plist as? [String: Any])
    }
}
