import SwiftUI

/// Minimal Status screen for the shell phase: snapshot-backed account table
/// with the UX plan §13 columns, degraded/stale presentation, and the
/// loading/empty patterns from §6. Selection inspector arrives later.
public struct StatusScreen: View {
    @Environment(SnapshotStore.self) private var store
    @Environment(NavigationModel.self) private var navigation

    public init() {}

    public var body: some View {
        VStack(spacing: 0) {
            if let banner = statusBanner {
                WarningBannerView(item: banner) {
                    Task { await store.refresh() }
                }
            }
            content
            footnotes
        }
        .navigationTitle("Status")
    }

    private var rows: [SnapshotAccountRow] {
        SnapshotAccountRow.rows(from: store.snapshot).filter(\.visible)
    }

    /// The one banner Status owns (UX plan §5): degraded data or stale cache.
    private var statusBanner: WarningItem? {
        store.warningItems.first { item in
            item.tier == .warning && (item.id == "refresh-failed" || item.id == "envelope-status-cache")
        }
    }

    @ViewBuilder
    private var content: some View {
        switch store.phase {
        case .initial, .loading:
            skeleton
        case .failed:
            ContentUnavailableView {
                Label("TokenKick core isn't responding", systemImage: "xmark.octagon")
            } description: {
                Text(store.lastError ?? "The bundled runtime did not answer.")
            } actions: {
                Button("Retry") { Task { await store.refresh() } }
            }
        case .loaded:
            if rows.isEmpty {
                ContentUnavailableView {
                    Label("No accounts yet", systemImage: "person.crop.circle.badge.questionmark")
                } description: {
                    Text("TokenKick finds the provider CLIs you're already logged into.")
                } actions: {
                    Button("Discover Accounts…") { navigation.open(.accounts) }
                        .buttonStyle(.borderedProminent)
                }
            } else {
                accountTable
            }
        }
    }

    private var accountTable: some View {
        Table(rows) {
            TableColumn("Account") { row in
                HStack(spacing: 6) {
                    Text(row.providerBadge)
                        .font(.caption2.weight(.bold))
                        .padding(.horizontal, 4)
                        .padding(.vertical, 2)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
                    Text(row.label).truncationMode(.middle)
                }
            }
            TableColumn("State") { row in
                Label(row.stateDisplay, systemImage: row.stateSymbolName)
                    .foregroundStyle(row.stale ? .secondary : .primary)
            }
            .width(min: 90, ideal: 110)
            TableColumn("Resets") { row in
                Text(row.resetsInText)
                    .font(.body.monospacedDigit())
                    .foregroundStyle(row.stale ? .secondary : .primary)
            }
            .width(min: 80, ideal: 100)
            TableColumn("Used") { row in
                UsageBarView(percent: row.usedPercent)
            }
            .width(min: 90, ideal: 110)
            TableColumn("Notes") { row in
                if row.stale {
                    Label(staleNote(row), systemImage: "clock.badge.exclamationmark")
                        .font(.caption)
                        .foregroundStyle(.orange)
                } else if let error = row.errorText {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .help(error)
                } else {
                    Text("—").foregroundStyle(.tertiary)
                }
            }
        }
    }

    /// "as of 41 m ago" when the row carries its observation time (UX plan
    /// §6); the vague fallback covers rows without one.
    private func staleNote(_ row: SnapshotAccountRow) -> String {
        guard let observedAt = row.observedAt else { return "as of earlier" }
        return "as of \(RelativeTimeText.ago(from: observedAt, now: Date()))"
    }

    private var skeleton: some View {
        List(0..<3, id: \.self) { _ in
            HStack {
                RoundedRectangle(cornerRadius: 4).fill(.quaternary)
                    .frame(width: 220, height: 14)
                Spacer()
                RoundedRectangle(cornerRadius: 4).fill(.quaternary)
                    .frame(width: 90, height: 14)
                RoundedRectangle(cornerRadius: 4).fill(.quaternary)
                    .frame(width: 70, height: 14)
            }
            .padding(.vertical, 6)
        }
        .scrollDisabled(true)
    }

    @ViewBuilder
    private var footnotes: some View {
        let notes = store.warningItems.filter { $0.tier == .footnote }
        if !notes.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(notes) { note in
                    Text(note.title)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal)
            .padding(.vertical, 6)
        }
    }
}
