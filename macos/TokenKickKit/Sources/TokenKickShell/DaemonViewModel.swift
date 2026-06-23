import Foundation
import Observation
import TokenKickKit

/// Daemon process control through bundled `tk` JSON commands.
public protocol DaemonControlling: Sendable {
    func startDaemon() async throws -> TKEnvelope<TKDaemonActionPayload>
    func stopDaemon() async throws -> TKEnvelope<TKDaemonActionPayload>
    func restartDaemon() async throws -> TKEnvelope<TKDaemonActionPayload>
}

/// LaunchAgent lifecycle through the Phase 3 manager. Swift never touches
/// TokenKick core state — the agent helper resolves and runs the bundled tk.
/// `agentStatus` is nil only when the bundled runtime itself is missing;
/// the blocker banner owns that condition.
public protocol LaunchAgentManaging: Sendable {
    func agentStatus(daemon: TKDaemonStatus?) async -> TKLaunchAgentStatus?
    func installAgent() throws -> TKLaunchAgentStatus
    func repairAgent() throws -> TKLaunchAgentStatus
    func startAgent(daemon: TKDaemonStatus?, takeover: Bool) async throws -> TKLaunchAgentStatus
    func removeAgent() async throws -> TKLaunchAgentStatus
}

public struct LiveDaemonController: DaemonControlling {
    public let timeout: TimeInterval

    public init(timeout: TimeInterval = 60) {
        self.timeout = timeout
    }

    public func startDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        try await LiveTKClient.make(timeout: timeout).startDaemon()
    }

    public func stopDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        try await LiveTKClient.make(timeout: timeout).stopDaemon()
    }

    public func restartDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        try await LiveTKClient.make(timeout: timeout).restartDaemon()
    }
}

public struct LiveLaunchAgentManager: LaunchAgentManaging {
    private func manager() throws -> TKLaunchAgentManager {
        let runtime = try TKRuntimeLocator.bundledTkURL()
        return TKLaunchAgentManager(
            runtime: runtime,
            daemonClient: try LiveTKClient.make(timeout: 60)
        )
    }

    public init() {}

    public func agentStatus(daemon: TKDaemonStatus?) async -> TKLaunchAgentStatus? {
        guard let manager = try? manager() else { return nil }
        return await manager.status(daemon: daemon)
    }

    public func installAgent() throws -> TKLaunchAgentStatus {
        try manager().install()
    }

    public func repairAgent() throws -> TKLaunchAgentStatus {
        try manager().repair()
    }

    public func startAgent(daemon: TKDaemonStatus?, takeover: Bool) async throws -> TKLaunchAgentStatus {
        try await manager().start(daemon: daemon, takeover: takeover)
    }

    public func removeAgent() async throws -> TKLaunchAgentStatus {
        try await manager().remove()
    }
}

/// Drives the Daemon screen: process control, LaunchAgent lifecycle, and
/// the takeover flow. Confirmation-gated actions (stop, takeover, remove)
/// go through the shared ConfirmedAction sheet; declining is normal flow.
@MainActor
@Observable
public final class DaemonViewModel {
    public enum DaemonAction: String, Equatable, Sendable, CaseIterable {
        case start
        case stop
        case restart
        case enableBackground   // install LaunchAgent + start it
        case repairAgent
        case removeAgent
        case takeover
    }

    public enum Phase: Equatable, Sendable {
        case idle
        case running(DaemonAction)
        case finished(action: DaemonAction, success: Bool, message: String)
    }

    public private(set) var phase: Phase = .idle
    public private(set) var agentStatus: TKLaunchAgentStatus?
    /// Set when a confirmation-gated action awaits the sheet; the paired
    /// action runs only via confirmPendingAction().
    public var pendingConfirmation: ConfirmedAction?
    private var pendingAction: DaemonAction?

    private let controller: any DaemonControlling
    private let agent: any LaunchAgentManaging
    private let onMutation: @MainActor () async -> Void

    public init(
        controller: any DaemonControlling,
        agent: any LaunchAgentManaging,
        onMutation: @escaping @MainActor () async -> Void
    ) {
        self.controller = controller
        self.agent = agent
        self.onMutation = onMutation
    }

    public var isBusy: Bool {
        if case .running = phase { return true }
        return false
    }

    // MARK: - Agent status

    public func reloadAgentStatus(daemon: TKDaemonStatus?) async {
        agentStatus = await agent.agentStatus(daemon: daemon)
    }

    // MARK: - Direct (no-confirmation) actions

    /// Start, restart, repair, and enable-background are safe/reversible:
    /// they run directly per the UX plan; only consequential stops confirm.
    public func performDirect(_ action: DaemonAction, daemon: TKDaemonStatus?) async {
        guard !isBusy else { return }
        switch action {
        case .start, .restart, .enableBackground, .repairAgent:
            await run(action, daemon: daemon)
        case .stop, .removeAgent, .takeover:
            assertionFailure("\(action) requires confirmation")
        }
    }

    // MARK: - Confirmation-gated actions

    public func requestStop() {
        guard !isBusy else { return }
        pendingAction = .stop
        pendingConfirmation = ConfirmedAction(
            id: "daemon-stop",
            title: "Stop background kicking?",
            explanation: "TokenKick stops watching for resets until the service is started again. No state is lost.",
            costLine: nil,
            disclosures: [],
            scopeLabel: "Background service",
            verb: "Stop Daemon",
            isDestructive: true,
            tkArguments: ["daemon", "--stop", "--json-output"]
        )
    }

    public func requestRemoveAgent() {
        guard !isBusy else { return }
        pendingAction = .removeAgent
        pendingConfirmation = ConfirmedAction(
            id: "daemon-remove-agent",
            title: "Remove the background service?",
            explanation: "Stops the daemon and removes the login item. Accounts, history, and schedules stay untouched in ~/.tokenkick.",
            costLine: nil,
            disclosures: [],
            scopeLabel: "Background service",
            verb: "Remove Service",
            isDestructive: true,
            tkArguments: []
        )
    }

    /// Takeover sheet per UX plan §11. Cancel ("keep terminal setup") is the
    /// default; the manager refuses takeover unless explicitly confirmed.
    public func requestTakeover(daemon: TKDaemonStatus?) {
        guard !isBusy else { return }
        pendingAction = .takeover
        let origin = daemon?.executable ?? "a terminal install"
        let version = daemon?.version.map { " (v\($0))" } ?? ""
        pendingConfirmation = ConfirmedAction(
            id: "daemon-takeover",
            title: "Manage the daemon with TokenKick?",
            explanation: "Stops the daemon from \(origin)\(version), installs the login item, and runs the app's bundled runtime instead.",
            costLine: nil,
            disclosures: [
                "Accounts, history, and schedules don't change — both use ~/.tokenkick.",
                "Your terminal install keeps working for CLI use.",
            ],
            scopeLabel: "Background service",
            verb: "Take Over",
            isDestructive: true,
            tkArguments: []
        )
    }

    /// Safe default: dismissing the sheet performs nothing.
    public func cancelConfirmation() {
        pendingConfirmation = nil
        pendingAction = nil
    }

    public func confirmPendingAction(daemon: TKDaemonStatus?) async {
        guard let action = pendingAction else { return }
        pendingConfirmation = nil
        pendingAction = nil
        await run(action, daemon: daemon)
    }

    public func dismissResult() {
        if case .finished = phase {
            phase = .idle
        }
    }

    // MARK: - Execution

    private func run(_ action: DaemonAction, daemon: TKDaemonStatus?) async {
        phase = .running(action)
        do {
            let message = try await execute(action, daemon: daemon)
            phase = .finished(action: action, success: true, message: message)
        } catch let error as TKLaunchAgentError {
            phase = .finished(action: action, success: false, message: error.description)
        } catch {
            phase = .finished(action: action, success: false, message: String(describing: error))
        }
        await onMutation()
        agentStatus = await agent.agentStatus(daemon: nil)
    }

    private func execute(_ action: DaemonAction, daemon: TKDaemonStatus?) async throws -> String {
        switch action {
        case .start:
            let envelope = try await controller.startDaemon()
            return try resultMessage(envelope, fallback: "Background service started.")
        case .stop:
            let envelope = try await controller.stopDaemon()
            return try resultMessage(envelope, fallback: "Background service stopped.")
        case .restart:
            let envelope = try await controller.restartDaemon()
            return try resultMessage(envelope, fallback: "Background service restarted.")
        case .enableBackground:
            _ = try agent.installAgent()
            _ = try await agent.startAgent(daemon: daemon, takeover: false)
            return "Background service installed and started. It now starts at login."
        case .repairAgent:
            _ = try agent.repairAgent()
            return "Background service files repaired."
        case .removeAgent:
            _ = try await agent.removeAgent()
            return "Background service removed. Start it manually any time."
        case .takeover:
            _ = try agent.installAgent()
            _ = try await agent.startAgent(daemon: daemon, takeover: true)
            return "TokenKick now manages the background service."
        }
    }

    private func resultMessage(
        _ envelope: TKEnvelope<TKDaemonActionPayload>,
        fallback: String
    ) throws -> String {
        if envelope.ok {
            return envelope.message ?? fallback
        }
        throw DaemonActionError(
            message: envelope.message
                ?? "The daemon command failed (\(envelope.errorCode ?? "unknown"))."
        )
    }
}

struct DaemonActionError: Error, CustomStringConvertible {
    let message: String
    var description: String { message }
}
