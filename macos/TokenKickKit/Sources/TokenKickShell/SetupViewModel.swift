import Foundation
import Observation
import TokenKickKit

/// One run of `tk app setup --json-lines`: an event stream plus a graceful
/// cancel. Live sessions interrupt the process (SIGINT) so the core ends
/// the stream with its own `setup_cancelled` record.
public protocol SetupSessionProtocol: Sendable {
    var events: AsyncThrowingStream<TKSetupEvent, Error> { get }
    func cancel()
}

public protocol SetupSessionStarting: Sendable {
    func startSession() throws -> any SetupSessionProtocol
}

public struct LiveSetupSession: SetupSessionProtocol {
    public let events: AsyncThrowingStream<TKSetupEvent, Error>
    private let stream: TKLineStream

    init(stream: TKLineStream) {
        self.stream = stream
        self.events = AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var index = 0
                    for try await line in stream.lines {
                        continuation.yield(try TKSetupStream.event(fromLine: line, index: index))
                        index += 1
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    public func cancel() {
        stream.interrupt()
    }
}

public struct LiveSetupSessionStarter: SetupSessionStarting {
    public init() {}

    public func startSession() throws -> any SetupSessionProtocol {
        let runtime = try TKRuntimeLocator.bundledTkURL()
        let environment = TKEnvironment.subprocessEnvironment(
            pathAdditions: TKEnvironment.defaultPathAdditions
                + AppSettingsModel.storedExtraPathEntries()
        )
        let stream = try TKProcessRunner().streamLines(
            executable: runtime,
            arguments: ["app", "setup", "--json-lines"],
            environment: environment
        )
        return LiveSetupSession(stream: stream)
    }
}

/// Discovery result summary for the results step.
public struct SetupSummary: Equatable, Sendable {
    public let summaryText: String
    public let accountCount: Int
    public let newAccountLabels: [String]
    public let hiddenDuplicateLabels: [String]
    public let warnings: [String]
}

/// Drives the setup checklist (UX plan §8/§10): JSON-lines progress events
/// render as completed steps, never a log dump; cancel is always available.
@MainActor
@Observable
public final class SetupViewModel {
    public enum Phase: Equatable, Sendable {
        case idle
        case running
        case completed(SetupSummary)
        case noAccounts(message: String)
        case failed(message: String)
        case cancelled
    }

    public struct Step: Identifiable, Equatable, Sendable {
        public let id: Int
        public let title: String
    }

    public private(set) var phase: Phase = .idle
    public private(set) var steps: [Step] = []

    private let starter: any SetupSessionStarting
    private let onMutation: @MainActor () async -> Void
    private var session: (any SetupSessionProtocol)?
    private var runTask: Task<Void, Never>?

    public init(
        starter: any SetupSessionStarting,
        onMutation: @escaping @MainActor () async -> Void
    ) {
        self.starter = starter
        self.onMutation = onMutation
    }

    public var isRunning: Bool { phase == .running }

    public func reset() {
        guard !isRunning else { return }
        phase = .idle
        steps = []
    }

    public func startDiscovery() {
        guard !isRunning else { return }
        phase = .running
        steps = []
        let session: any SetupSessionProtocol
        do {
            session = try starter.startSession()
        } catch {
            phase = .failed(message: String(describing: error))
            return
        }
        self.session = session
        runTask = Task { [weak self] in
            await self?.consume(session)
        }
    }

    /// Graceful cancel: the core finishes the stream with `setup_cancelled`.
    public func cancelDiscovery() {
        session?.cancel()
    }

    private func consume(_ session: any SetupSessionProtocol) async {
        var terminal: TKSetupEvent?
        do {
            for try await event in session.events {
                if event.isTerminal {
                    terminal = event
                } else {
                    appendStep(for: event)
                }
            }
        } catch {
            phase = .failed(message: String(describing: error))
            self.session = nil
            return
        }
        self.session = nil
        finish(with: terminal)
        await onMutation()
    }

    private func appendStep(for event: TKSetupEvent) {
        let title: String?
        switch event.event {
        case "setup_started":
            title = "Starting discovery"
        case "config_loaded":
            title = nil
        case "progress":
            title = event.message
        case "discovery_completed":
            title = event.summary
        case "config_saved":
            title = event.accounts.map { "Saved \($0) account\($0 == 1 ? "" : "s")" }
        default:
            title = event.message ?? event.summary
        }
        guard let title, steps.last?.title != title else { return }
        steps.append(Step(id: steps.count, title: title))
    }

    private func finish(with terminal: TKSetupEvent?) {
        guard let terminal else {
            phase = .failed(message: "Setup ended without a final record.")
            return
        }
        switch terminal.event {
        case "setup_cancelled":
            phase = .cancelled
        case "setup_failed":
            phase = .failed(message: terminal.message ?? "Setup failed.")
        case "setup_completed":
            let payload = terminal.payload
            let configSaved = payload?["config_saved"]?.boolValue ?? false
            let accounts = payload?["accounts"]?.arrayValue ?? []
            if !configSaved || accounts.isEmpty {
                phase = .noAccounts(
                    message: payload?["summary"]?.stringValue
                        ?? "No provider accounts were found. Log in with codex or claude in a terminal, then try again."
                )
                return
            }
            phase = .completed(
                SetupSummary(
                    summaryText: payload?["summary"]?.stringValue ?? "Accounts configured.",
                    accountCount: accounts.count,
                    newAccountLabels: (payload?["new_accounts"]?.arrayValue ?? [])
                        .compactMap(\.stringValue),
                    hiddenDuplicateLabels: (payload?["hidden_duplicate_labels"]?.arrayValue ?? [])
                        .compactMap(\.stringValue),
                    warnings: terminal.warnings ?? []
                )
            )
        default:
            phase = .failed(message: "Unexpected final record: \(terminal.event)")
        }
    }
}

/// First-run flow per UX plan §10: Welcome → Discover → Background, window
/// based, skippable at every point. The background step is optional and
/// only offered after a successful discovery.
@MainActor
@Observable
public final class FirstRunModel {
    public enum Step: Equatable, Sendable {
        case welcome
        case discover
        case background
        case done
    }

    public private(set) var step: Step = .welcome

    public init() {}

    /// Offer first-run only when nothing is configured yet.
    public static func shouldOffer(snapshot: TKSnapshotPayload?, completedBefore: Bool) -> Bool {
        guard !completedBefore, let snapshot else { return false }
        let configured = snapshot.notifications["accounts"]?.arrayValue ?? []
        return configured.isEmpty && snapshot.status.accounts.isEmpty
    }

    public func beginDiscovery() {
        guard step == .welcome else { return }
        step = .discover
    }

    /// After discovery resolves: success offers the background step,
    /// no-accounts/failed/cancelled stay on the discover step for retry.
    public func discoveryResolved(_ phase: SetupViewModel.Phase) {
        guard step == .discover else { return }
        if case .completed = phase {
            step = .background
        }
    }

    /// Skipping is allowed everywhere and is not an error.
    public func skip() {
        step = .done
    }

    public func finish() {
        step = .done
    }
}
