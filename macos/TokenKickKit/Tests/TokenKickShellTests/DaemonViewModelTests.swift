import XCTest
import TokenKickKit
@testable import TokenKickShell

final class StubDaemonController: DaemonControlling, @unchecked Sendable {
    private let lock = NSLock()
    var startResult: Result<TKEnvelope<TKDaemonActionPayload>, Error>?
    var stopResult: Result<TKEnvelope<TKDaemonActionPayload>, Error>?
    var restartResult: Result<TKEnvelope<TKDaemonActionPayload>, Error>?
    private(set) var calls: [String] = []

    private func record(_ call: String) {
        lock.lock()
        calls.append(call)
        lock.unlock()
    }

    func startDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        record("start")
        return try startResult!.get()
    }

    func stopDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        record("stop")
        return try stopResult!.get()
    }

    func restartDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        record("restart")
        return try restartResult!.get()
    }
}

final class StubLaunchAgent: LaunchAgentManaging, @unchecked Sendable {
    private let lock = NSLock()
    private(set) var calls: [String] = []
    var startError: Error?

    private func record(_ call: String) {
        lock.lock()
        calls.append(call)
        lock.unlock()
    }

    private func status(installed: Bool) -> TKLaunchAgentStatus {
        TKLaunchAgentStatus(
            label: "com.tokenkick.daemon",
            installed: installed,
            loaded: installed,
            helperURL: URL(fileURLWithPath: "/Users/fixture/helper"),
            plistURL: URL(fileURLWithPath: "/Users/fixture/plist"),
            runtimePathURL: URL(fileURLWithPath: "/Users/fixture/runtime-path"),
            configuredRuntime: "/Users/fixture/tk",
            runtimePathMatches: installed,
            plistProgramMatchesHelper: installed,
            needsRepair: !installed,
            daemonOwnership: .notRunning,
            stalePidfile: false,
            versionMismatch: false,
            executablePathMismatch: false
        )
    }

    func agentStatus(daemon: TKDaemonStatus?) async -> TKLaunchAgentStatus? {
        record("status")
        return status(installed: true)
    }

    func installAgent() throws -> TKLaunchAgentStatus {
        record("install")
        return status(installed: true)
    }

    func repairAgent() throws -> TKLaunchAgentStatus {
        record("repair")
        return status(installed: true)
    }

    func startAgent(daemon: TKDaemonStatus?, takeover: Bool) async throws -> TKLaunchAgentStatus {
        record("startAgent(takeover:\(takeover))")
        if let startError { throw startError }
        return status(installed: true)
    }

    func removeAgent() async throws -> TKLaunchAgentStatus {
        record("remove")
        return status(installed: false)
    }
}

@MainActor
final class DaemonViewModelTests: XCTestCase {
    private var refreshCount = 0

    private func makeModel(
        controller: StubDaemonController = StubDaemonController(),
        agent: StubLaunchAgent = StubLaunchAgent()
    ) -> (DaemonViewModel, StubDaemonController, StubLaunchAgent) {
        refreshCount = 0
        let model = DaemonViewModel(controller: controller, agent: agent) { [weak self] in
            self?.refreshCount += 1
        }
        return (model, controller, agent)
    }

    private func actionEnvelope(
        ok: Bool = true,
        message: String? = nil,
        running: Bool = false
    ) throws -> TKEnvelope<TKDaemonActionPayload> {
        let payload: [String: Any] = [
            "action": "start",
            "started": true,
            "daemon": [
                "running": running,
                "pid": running ? 99 : NSNull(),
                "version": NSNull(),
                "executable": NSNull(),
                "installed_version": "1.9.16",
                "version_match": NSNull(),
                "executable_match": NSNull(),
                "pidfile_exists": running,
                "stale_pidfile": false,
                "uptime_seconds": NSNull(),
                "poll_interval_minutes": 5,
                "pidfile_path": "/Users/fixture/.tokenkick/daemon.pid",
                "log_path": "/Users/fixture/.tokenkick/daemon.log",
            ],
        ]
        let envelope: [String: Any] = [
            "schema_version": 1,
            "ok": ok,
            "error_code": ok ? NSNull() : "daemon_start_failed",
            "message": message ?? NSNull(),
            "warnings": [String](),
            "payload": payload,
        ]
        let data = try JSONSerialization.data(withJSONObject: envelope)
        return try TKJSONDecoding.envelope(TKDaemonActionPayload.self, from: data)
    }

    // MARK: - Direct actions

    func testStartSuccessRefreshesSnapshot() async throws {
        let controller = StubDaemonController()
        controller.startResult = .success(
            try actionEnvelope(message: "TokenKick daemon started in background (pid 99).", running: true)
        )
        let (model, _, agent) = makeModel(controller: controller)

        await model.performDirect(.start, daemon: nil)

        guard case .finished(let action, let success, let message) = model.phase else {
            return XCTFail("expected finished phase")
        }
        XCTAssertEqual(action, .start)
        XCTAssertTrue(success)
        XCTAssertTrue(message.contains("pid 99"))
        XCTAssertEqual(refreshCount, 1)
        XCTAssertTrue(agent.calls.contains("status"), "agent status reloads after actions")
    }

    func testStartFailureEnvelopeSurfacesMessage() async throws {
        let controller = StubDaemonController()
        controller.startResult = .success(
            try actionEnvelope(ok: false, message: "TokenKick daemon could not be started.")
        )
        let (model, _, _) = makeModel(controller: controller)

        await model.performDirect(.start, daemon: nil)

        guard case .finished(_, let success, let message) = model.phase else {
            return XCTFail("expected finished phase")
        }
        XCTAssertFalse(success)
        XCTAssertTrue(message.contains("could not be started"))
        XCTAssertEqual(refreshCount, 1, "refresh even after failure")
    }

    func testEnableBackgroundInstallsThenStartsWithoutTakeover() async throws {
        let (model, _, agent) = makeModel()

        await model.performDirect(.enableBackground, daemon: nil)

        XCTAssertEqual(
            agent.calls.filter { $0 != "status" },
            ["install", "startAgent(takeover:false)"]
        )
        guard case .finished(_, let success, _) = model.phase else {
            return XCTFail("expected finished phase")
        }
        XCTAssertTrue(success)
        XCTAssertEqual(refreshCount, 1)
    }

    // MARK: - Confirmation-gated actions

    func testStopRequiresConfirmationAndCancelIsSafe() async throws {
        let controller = StubDaemonController()
        controller.stopResult = .success(try actionEnvelope())
        let (model, _, _) = makeModel(controller: controller)

        model.requestStop()
        let action = try XCTUnwrap(model.pendingConfirmation)
        XCTAssertEqual(action.verb, "Stop Daemon")
        XCTAssertTrue(action.isDestructive)
        XCTAssertNil(action.costLine, "stopping consumes no quota")

        model.cancelConfirmation()
        XCTAssertNil(model.pendingConfirmation)
        await model.confirmPendingAction(daemon: nil)

        XCTAssertEqual(controller.calls, [], "declined confirmation performs nothing")
        XCTAssertEqual(refreshCount, 0)
        XCTAssertEqual(model.phase, .idle)
    }

    func testConfirmedStopRuns() async throws {
        let controller = StubDaemonController()
        controller.stopResult = .success(
            try actionEnvelope(message: "TokenKick daemon stopped (pid 99).")
        )
        let (model, _, _) = makeModel(controller: controller)

        model.requestStop()
        await model.confirmPendingAction(daemon: nil)

        XCTAssertEqual(controller.calls, ["stop"])
        guard case .finished(let action, let success, _) = model.phase else {
            return XCTFail("expected finished phase")
        }
        XCTAssertEqual(action, .stop)
        XCTAssertTrue(success)
        XCTAssertEqual(refreshCount, 1)
    }

    func testTakeoverSheetDefaultsToSafeCancel() async throws {
        let (model, _, agent) = makeModel()
        let daemon = try ShellFixtures.snapshot(
            daemonRunning: true,
            daemonVersion: "1.7.6",
            versionMatch: false,
            executable: "/Users/fixture/.local/pipx/venvs/tokenkick/bin/tk",
            executableMatch: false
        ).daemon

        model.requestTakeover(daemon: daemon)
        let action = try XCTUnwrap(model.pendingConfirmation)
        XCTAssertEqual(action.verb, "Take Over")
        XCTAssertTrue(action.isDestructive, "takeover is never the default")
        XCTAssertTrue(action.explanation.contains("pipx"))
        XCTAssertTrue(action.disclosures.contains { $0.contains("~/.tokenkick") })

        model.cancelConfirmation()
        XCTAssertEqual(agent.calls, [], "keeping the terminal setup performs nothing")
        XCTAssertEqual(refreshCount, 0)
    }

    func testConfirmedTakeoverPassesExplicitTakeoverFlag() async throws {
        let (model, _, agent) = makeModel()

        model.requestTakeover(daemon: nil)
        await model.confirmPendingAction(daemon: nil)

        XCTAssertEqual(
            agent.calls.filter { $0 != "status" },
            ["install", "startAgent(takeover:true)"]
        )
        guard case .finished(let action, let success, _) = model.phase else {
            return XCTFail("expected finished phase")
        }
        XCTAssertEqual(action, .takeover)
        XCTAssertTrue(success)
        XCTAssertEqual(refreshCount, 1)
    }

    func testTakeoverRequiredErrorSurfaces() async throws {
        let agent = StubLaunchAgent()
        agent.startError = TKLaunchAgentError.takeoverRequired(
            pid: 4821,
            executable: "/Users/fixture/.local/pipx/venvs/tokenkick/bin/tk"
        )
        let (model, _, _) = makeModel(agent: agent)

        model.requestTakeover(daemon: nil)
        await model.confirmPendingAction(daemon: nil)

        guard case .finished(_, let success, let message) = model.phase else {
            return XCTFail("expected finished phase")
        }
        XCTAssertFalse(success)
        XCTAssertTrue(message.contains("4821"))
        XCTAssertEqual(refreshCount, 1)
    }

    func testRemoveAgentConfirmed() async throws {
        let (model, _, agent) = makeModel()

        model.requestRemoveAgent()
        let action = try XCTUnwrap(model.pendingConfirmation)
        XCTAssertTrue(action.isDestructive)
        XCTAssertTrue(action.explanation.contains("~/.tokenkick"))

        await model.confirmPendingAction(daemon: nil)
        XCTAssertEqual(agent.calls.filter { $0 != "status" }, ["remove"])
        XCTAssertEqual(refreshCount, 1)
    }
}
