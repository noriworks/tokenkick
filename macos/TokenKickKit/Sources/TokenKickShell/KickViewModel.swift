import Foundation
import Observation
import TokenKickKit

/// Executes one confirmed kick — `TKClient` against the bundled runtime in
/// the app, stubs in tests.
public protocol KickPerforming: Sendable {
    func performKick(label: String) async throws -> TKEnvelope<TKKickResultPayload>
}

/// Shared bundled-runtime client construction for live providers: locate on
/// every call (recovers after repair) and honor the Settings PATH entries.
public enum LiveTKClient {
    public static func make(
        timeout: TimeInterval,
        extraPathEntries: [String] = AppSettingsModel.storedExtraPathEntries()
    ) throws -> TKClient {
        let runtime = try TKRuntimeLocator.bundledTkURL()
        let environment = TKEnvironment.subprocessEnvironment(
            pathAdditions: TKEnvironment.defaultPathAdditions + extraPathEntries
        )
        return TKClient(runtime: runtime, environment: environment, timeout: timeout)
    }
}

public struct LiveKickPerformer: KickPerforming {
    /// Kicks spawn a provider CLI and verify the anchor afterwards; allow
    /// well beyond the snapshot timeout.
    public let timeout: TimeInterval

    public init(timeout: TimeInterval = 300) {
        self.timeout = timeout
    }

    public func performKick(label: String) async throws -> TKEnvelope<TKKickResultPayload> {
        try await LiveTKClient.make(timeout: timeout).kick(label: label)
    }
}

/// Drives the Kick screen and Quick Kick: same confirmation, execution, and
/// result flow everywhere. One mutation runs at a time per account.
@MainActor
@Observable
public final class KickViewModel {
    public enum RowActionState: Equatable, Sendable {
        case idle
        case running
        case finished(KickOutcome)
    }

    /// The action awaiting user confirmation; the sheet binds to this.
    /// Setting it to nil (Cancel, Esc) abandons the action silently —
    /// declining is normal flow, not an error.
    public var pendingConfirmation: ConfirmedAction?

    public private(set) var actionStates: [String: RowActionState] = [:]
    /// Most recent finished kick, for compact surfaces like the popover.
    public private(set) var lastOutcome: (label: String, outcome: KickOutcome)?

    private let performer: any KickPerforming
    private let onMutation: @MainActor () async -> Void

    public init(
        performer: any KickPerforming,
        onMutation: @escaping @MainActor () async -> Void
    ) {
        self.performer = performer
        self.onMutation = onMutation
    }

    // MARK: - Row derivation

    public static func eligibleRows(in snapshot: TKSnapshotPayload?) -> [SnapshotAccountRow] {
        SnapshotAccountRow.rows(from: snapshot).filter { $0.visible && $0.kickable }
    }

    public static func ineligibleRows(in snapshot: TKSnapshotPayload?) -> [SnapshotAccountRow] {
        SnapshotAccountRow.rows(from: snapshot).filter { $0.visible && !$0.kickable }
    }

    /// Why an account can't be kicked right now, in UI words.
    public static func ineligibilityText(for row: SnapshotAccountRow) -> String {
        switch row.kickBlockedReason {
        case "provider_not_kickable":
            return row.provider == "gemini" ? "Monitor-only" : "Provider can't be kicked"
        case "unknown":
            return "Status unknown — refresh or check Diagnostics"
        case .some(let reason):
            let text = reason.replacingOccurrences(of: "_", with: " ")
            return text.prefix(1).uppercased() + text.dropFirst()
        case nil:
            if let phrase = row.resetsPhrase {
                return "Window active — \(phrase)"
            }
            return "Not kickable right now"
        }
    }

    // MARK: - Confirmation flow

    public func state(for label: String) -> RowActionState {
        actionStates[label] ?? .idle
    }

    public var isAnyKickRunning: Bool {
        actionStates.values.contains(.running)
    }

    /// Step 1: build the confirmation from the same snapshot the user sees.
    public func requestKick(for row: SnapshotAccountRow, snapshot: TKSnapshotPayload?) {
        guard state(for: row.label) != .running else { return }
        pendingConfirmation = ConfirmedAction.kick(
            row: row,
            pendingKicks: PendingKickRow.rows(from: snapshot)
        )
    }

    /// Safe default: dismissing performs nothing and touches nothing.
    public func cancelConfirmation() {
        pendingConfirmation = nil
    }

    /// Step 2: the user confirmed in the sheet; run the kick and always
    /// refresh afterwards — even a failed attempt can change provider state.
    public func confirmPendingAction() async {
        guard let action = pendingConfirmation else { return }
        pendingConfirmation = nil
        let label = action.scopeLabel
        actionStates[label] = .running
        let outcome: KickOutcome
        do {
            let envelope = try await performer.performKick(label: label)
            outcome = KickOutcome.from(envelope: envelope)
        } catch {
            outcome = .failed(message: String(describing: error))
        }
        actionStates[label] = .finished(outcome)
        lastOutcome = (label: label, outcome: outcome)
        await onMutation()
    }

    public func clearResult(for label: String) {
        if case .finished = state(for: label) {
            actionStates[label] = nil
        }
        if lastOutcome?.label == label {
            lastOutcome = nil
        }
    }
}
