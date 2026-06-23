import SwiftUI

/// Menu bar popover shell (UX plan §3). Read-mostly: in the shell phase the
/// only mutation is a safe refresh; Quick Kick routes to the Kick screen.
public struct MenuBarPopoverView: View {
    @Environment(SnapshotStore.self) private var store
    @Environment(NavigationModel.self) private var navigation
    @Environment(KickViewModel.self) private var kick
    @Environment(PlannerViewModel.self) private var planner
    @Environment(MainWindowPresenter.self) private var windowPresenter

    public init() {}

    public var body: some View {
        @Bindable var kick = kick
        let model = store.popoverModel()
        VStack(alignment: .leading, spacing: 0) {
            header(model)
            Divider().padding(.vertical, 6)
            if let warning = model.topWarning {
                warningStrip(warning, additional: model.additionalWarningCount)
                Divider().padding(.vertical, 6)
            }
            accountSection(model)
            if let nextAction = model.nextActionLine {
                Divider().padding(.vertical, 6)
                Label(nextAction, systemImage: "bolt")
                    .font(.callout)
                    .padding(.horizontal, 12)
            }
            if kick.isAnyKickRunning || kick.lastOutcome != nil {
                Divider().padding(.vertical, 6)
                lastKickResult
            }
            Divider().padding(.vertical, 6)
            footer(model)
        }
        .padding(.vertical, 10)
        .frame(width: 360)
        .sheet(item: $kick.pendingConfirmation) { action in
            ConfirmationSheetView(
                action: action,
                onCancel: { kick.cancelConfirmation() },
                onConfirm: { Task { await kick.confirmPendingAction() } }
            )
        }
        .task { await store.refresh() }
    }

    @ViewBuilder
    private var lastKickResult: some View {
        if kick.isAnyKickRunning {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Kicking…")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
        } else if let last = kick.lastOutcome {
            HStack(spacing: 6) {
                Image(systemName: last.outcome.symbolName)
                    .foregroundStyle(lastOutcomeColor(last.outcome))
                Text("\(last.label): \(last.outcome.message)")
                    .font(.caption)
                    .lineLimit(2)
                Spacer(minLength: 0)
                Button {
                    kick.clearResult(for: last.label)
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(.tertiary)
                }
                .buttonStyle(.plain)
                .help("Dismiss")
            }
            .padding(.horizontal, 12)
        }
    }

    private func lastOutcomeColor(_ outcome: KickOutcome) -> Color {
        switch outcome {
        case .confirmed: return .green
        case .unconfirmed: return .orange
        case .failed: return .red
        case .skipped: return .secondary
        }
    }

    private func header(_ model: PopoverModel) -> some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("TokenKick").font(.headline)
                Text(model.headerStateLine)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                Button {
                    Task { await store.refresh() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .disabled(store.isRefreshing)
                if let lastUpdated = store.lastUpdated {
                    TimelineView(.periodic(from: .now, by: 10)) { context in
                        Text("Checked \(RelativeTimeText.ago(from: lastUpdated, now: context.date))")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .padding(.horizontal, 12)
    }

    private func warningStrip(_ warning: WarningItem, additional: Int) -> some View {
        Button {
            openMainWindow(at: warning.destination ?? .status)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: warning.tier.symbolName)
                    .foregroundStyle(warning.tier.color)
                Text(additional > 0 ? "\(warning.title) · \(additional) more…" : warning.title)
                    .font(.callout)
                    .lineLimit(1)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
            .contentShape(Rectangle())
            .padding(.horizontal, 12)
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private func accountSection(_ model: PopoverModel) -> some View {
        if model.accountRows.isEmpty {
            Text(store.phase == .initial || store.phase == .loading
                ? "Loading accounts…"
                : "No accounts yet — open TokenKick to discover them.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 12)
                .padding(.vertical, 4)
        } else {
            VStack(spacing: 2) {
                ForEach(model.accountRows) { row in
                    Button {
                        openMainWindow(at: .status)
                    } label: {
                        accountRow(row)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                if model.overflowAccountCount > 0 {
                    Button {
                        openMainWindow(at: .status)
                    } label: {
                        Text("\(model.overflowAccountCount) more accounts…")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .contentShape(Rectangle())
                            .padding(.horizontal, 12)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private func accountRow(_ row: SnapshotAccountRow) -> some View {
        HStack(spacing: 8) {
            Text(row.providerBadge)
                .font(.caption2.weight(.bold))
                .padding(.horizontal, 4)
                .padding(.vertical, 2)
                .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
            Text(row.label)
                .font(.callout)
                .truncationMode(.middle)
                .lineLimit(1)
            Spacer(minLength: 8)
            Label(row.stateDisplay, systemImage: row.stateSymbolName)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(row.resetsInText)
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
            UsageBarView(percent: row.usedPercent)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 3)
    }

    private func footer(_ model: PopoverModel) -> some View {
        @Bindable var planner = planner
        return HStack {
            quickKickControl(model)
            if model.cancelPlanVisible {
                // Ellipsis: the standard cancel confirmation follows.
                Button("Cancel Plan…") {
                    planner.updateActivePlanRows(snapshot: store.snapshot)
                    planner.requestCancelPlan()
                }
                .controlSize(.small)
                .disabled(planner.isMutating)
            }
            Spacer()
            Button("Open TokenKick") {
                openMainWindow(at: navigation.selection)
            }
            .controlSize(.small)
            Menu {
                Button("Refresh") { Task { await store.refresh() } }
                SettingsLink { Text("Settings…") }
                Divider()
                Button("Quit TokenKick") { NSApplication.shared.terminate(nil) }
            } label: {
                Image(systemName: "gearshape")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
        }
        .padding(.horizontal, 12)
        // Anchored to the footer itself so an auto-refresh that hides the
        // Cancel Plan button can't tear down a presented sheet.
        .sheet(item: $planner.pendingConfirmation) { action in
            ConfirmationSheetView(
                action: action,
                onCancel: { planner.cancelConfirmation() },
                onConfirm: {
                    Task { await planner.confirmPendingAction(snapshot: store.snapshot) }
                }
            )
        }
    }

    @ViewBuilder
    private func quickKickControl(_ model: PopoverModel) -> some View {
        switch model.quickKick {
        case .available(let rows):
            // Selecting an account opens the same confirmation sheet as the
            // Kick screen — quota is never one click away.
            Menu("Quick Kick") {
                ForEach(rows) { row in
                    Button {
                        kick.requestKick(for: row, snapshot: store.snapshot)
                    } label: {
                        Label(row.label, systemImage: "bolt.fill")
                    }
                }
            }
            .controlSize(.small)
            .fixedSize()
            .disabled(kick.isAnyKickRunning)
        case .disabled(let reason):
            Menu("Quick Kick") {
                Text(reason)
                Button("Open Kick Screen…") {
                    openMainWindow(at: .kick)
                }
            }
            .controlSize(.small)
            .fixedSize()
            .help(reason)
        }
    }

    private func openMainWindow(at destination: SidebarDestination) {
        windowPresenter.open(destination)
    }
}
