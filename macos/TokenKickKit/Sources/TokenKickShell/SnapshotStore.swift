import Foundation
import Observation
import TokenKickKit

/// Source of snapshot envelopes — `TKClient` in the app, stubs in tests.
public protocol SnapshotProviding: Sendable {
    func fetchSnapshot() async throws -> TKEnvelope<TKSnapshotPayload>
}

public enum SnapshotPhase: Equatable, Sendable {
    /// Nothing fetched yet (skeleton state).
    case initial
    /// First fetch in flight.
    case loading
    /// A snapshot is displayed; it may be degraded if the latest refresh failed.
    case loaded
    /// First fetch failed; nothing to show.
    case failed
}

/// One store per app feeds the popover and every window screen
/// (UX plan §4): refresh is global, data is never blanked to reload,
/// and a failed refresh keeps the previous snapshot as degraded data.
@MainActor
@Observable
public final class SnapshotStore {
    public private(set) var phase: SnapshotPhase = .initial
    public private(set) var snapshot: TKSnapshotPayload?
    public private(set) var envelopeOK = true
    public private(set) var envelopeWarnings: [String] = []
    public private(set) var lastUpdated: Date?
    public private(set) var lastError: String?
    public private(set) var isRefreshing = false

    private let provider: any SnapshotProviding
    private let now: @Sendable () -> Date

    /// Owns the auto-refresh task so cancellation happens in this box's
    /// deinit — SnapshotStore's own deinit is nonisolated and cannot touch
    /// main-actor state.
    private final class AutoRefreshBox {
        var task: Task<Void, Never>? {
            willSet { task?.cancel() }
        }

        deinit { task?.cancel() }
    }

    private let autoRefresh = AutoRefreshBox()

    public init(
        provider: any SnapshotProviding,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.provider = provider
        self.now = now
    }

    // MARK: - Derived state

    public var warningItems: [WarningItem] {
        WarningDerivation.items(
            from: WarningInputs(
                snapshot: snapshot,
                envelopeOK: envelopeOK,
                envelopeWarnings: envelopeWarnings,
                fetchError: lastError
            )
        )
    }

    public var menuBarIndicator: MenuBarIndicator {
        WarningDerivation.menuBarIndicator(for: warningItems)
    }

    public var daemonChip: DaemonChipState {
        DaemonChipState.derive(from: snapshot?.daemon)
    }

    public var daemonOwnership: DaemonOwnershipPresentation {
        DaemonOwnershipPresentation.derive(from: snapshot?.daemon)
    }

    /// The latest refresh failed but earlier data is still on screen.
    public var isDegraded: Bool {
        snapshot != nil && lastError != nil
    }

    public func popoverModel(now referenceDate: Date? = nil) -> PopoverModel {
        PopoverModel(
            snapshot: snapshot,
            warnings: warningItems,
            now: referenceDate ?? now()
        )
    }

    // MARK: - Refresh

    /// Coalesces overlapping requests; callers can always `await` safely.
    public func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        if phase == .initial { phase = .loading }
        defer { isRefreshing = false }

        do {
            let envelope = try await provider.fetchSnapshot()
            envelopeOK = envelope.ok
            envelopeWarnings = envelope.warnings
            if envelope.ok, let payload = envelope.payload {
                snapshot = payload
                lastUpdated = now()
                lastError = nil
                phase = .loaded
            } else {
                lastError = envelope.message
                    ?? "TokenKick reported \(envelope.errorCode ?? "an unknown error")."
                phase = snapshot == nil ? .failed : .loaded
            }
        } catch {
            lastError = String(describing: error)
            phase = snapshot == nil ? .failed : .loaded
        }
    }

    // MARK: - Auto refresh

    /// Silent periodic refresh (UX plan §4); pass `nil` to stop.
    public func setAutoRefresh(every interval: TimeInterval?) {
        guard let interval, interval > 0 else {
            autoRefresh.task = nil
            return
        }
        autoRefresh.task = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
                if Task.isCancelled { return }
                await self?.refresh()
            }
        }
    }
}

/// Production provider: locates the bundled runtime on every fetch (so a
/// repaired install recovers without restart) and reads the user's extra
/// PATH entries from defaults (so Settings changes apply immediately).
public struct LiveSnapshotProvider: SnapshotProviding {
    public let timeout: TimeInterval
    private let extraPathEntries: @Sendable () -> [String]

    public init(
        timeout: TimeInterval = 60,
        extraPathEntries: @escaping @Sendable () -> [String] = {
            AppSettingsModel.storedExtraPathEntries()
        }
    ) {
        self.timeout = timeout
        self.extraPathEntries = extraPathEntries
    }

    public func fetchSnapshot() async throws -> TKEnvelope<TKSnapshotPayload> {
        try await LiveTKClient.make(timeout: timeout, extraPathEntries: extraPathEntries())
            .snapshot()
    }
}
