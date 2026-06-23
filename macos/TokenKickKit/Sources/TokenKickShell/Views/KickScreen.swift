import SwiftUI
import TokenKickKit

/// Kick screen (Phase 5A): snapshot-backed eligibility, confirmed kicks
/// through the bundled runtime, truthful per-account results.
public struct KickScreen: View {
    @Environment(SnapshotStore.self) private var store
    @Environment(KickViewModel.self) private var kick
    @Environment(NavigationModel.self) private var navigation

    public init() {}

    public var body: some View {
        @Bindable var kick = kick
        Group {
            switch store.phase {
            case .initial, .loading:
                ProgressView("Loading accounts…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed:
                ContentUnavailableView {
                    Label("TokenKick core isn't responding", systemImage: "xmark.octagon")
                } description: {
                    Text(store.lastError ?? "The bundled runtime did not answer.")
                } actions: {
                    Button("Retry") { Task { await store.refresh() } }
                }
            case .loaded:
                content
            }
        }
        .sheet(item: $kick.pendingConfirmation) { action in
            ConfirmationSheetView(
                action: action,
                onCancel: { kick.cancelConfirmation() },
                onConfirm: { Task { await kick.confirmPendingAction() } }
            )
        }
        .navigationTitle("Kick")
    }

    private var eligible: [SnapshotAccountRow] {
        KickViewModel.eligibleRows(in: store.snapshot)
    }

    private var ineligible: [SnapshotAccountRow] {
        KickViewModel.ineligibleRows(in: store.snapshot)
    }

    @ViewBuilder
    private var content: some View {
        if eligible.isEmpty && ineligible.isEmpty {
            ContentUnavailableView {
                Label("No accounts yet", systemImage: "person.crop.circle.badge.questionmark")
            } description: {
                Text("TokenKick finds the provider CLIs you're already logged into.")
            } actions: {
                Button("Discover Accounts…") { navigation.open(.accounts) }
                    .buttonStyle(.borderedProminent)
            }
        } else {
            List {
                if !eligible.isEmpty {
                    Section("Ready to kick") {
                        ForEach(eligible) { row in
                            kickableRow(row)
                        }
                    }
                }
                if !ineligible.isEmpty {
                    Section("Not kickable right now") {
                        ForEach(ineligible) { row in
                            ineligibleRow(row)
                        }
                    }
                }
                Section {
                } footer: {
                    Text("Kicks always ask for confirmation and use a small amount of quota. Automatic kicking stays with the background service.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private func kickableRow(_ row: SnapshotAccountRow) -> some View {
        HStack(spacing: 10) {
            accountCell(row)
            Spacer()
            resultCell(row)
            kickButton(row)
        }
        .padding(.vertical, 2)
    }

    private func ineligibleRow(_ row: SnapshotAccountRow) -> some View {
        HStack(spacing: 10) {
            accountCell(row)
            Spacer()
            resultCell(row)
            Text(KickViewModel.ineligibilityText(for: row))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
    }

    private func accountCell(_ row: SnapshotAccountRow) -> some View {
        HStack(spacing: 8) {
            Text(row.providerBadge)
                .font(.caption2.weight(.bold))
                .padding(.horizontal, 4)
                .padding(.vertical, 2)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
            VStack(alignment: .leading, spacing: 1) {
                Text(row.label).truncationMode(.middle).lineLimit(1)
                HStack(spacing: 6) {
                    Label(row.stateDisplay, systemImage: row.stateSymbolName)
                    if let phrase = row.resetsPhrase {
                        Text(phrase).monospacedDigit()
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func resultCell(_ row: SnapshotAccountRow) -> some View {
        if case .finished(let outcome) = kick.state(for: row.label) {
            Label {
                Text(outcome.message)
                    .font(.caption)
                    .lineLimit(2)
            } icon: {
                Image(systemName: outcome.symbolName)
                    .foregroundStyle(outcomeColor(outcome))
            }
            .help(outcome.message)
            .frame(maxWidth: 320, alignment: .trailing)
        }
    }

    @ViewBuilder
    private func kickButton(_ row: SnapshotAccountRow) -> some View {
        switch kick.state(for: row.label) {
        case .running:
            ProgressView()
                .controlSize(.small)
                .frame(width: 64)
        default:
            Button {
                kick.requestKick(for: row, snapshot: store.snapshot)
            } label: {
                Label("Kick", systemImage: "bolt.fill")
            }
            .controlSize(.small)
            .disabled(kick.isAnyKickRunning)
        }
    }

    private func outcomeColor(_ outcome: KickOutcome) -> Color {
        switch outcome {
        case .confirmed: return .green
        case .unconfirmed: return .orange
        case .failed: return .red
        case .skipped: return .secondary
        }
    }
}
