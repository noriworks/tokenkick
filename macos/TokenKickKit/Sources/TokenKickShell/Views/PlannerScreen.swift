import SwiftUI
import TokenKickKit

/// Native orchestration planner. The core still owns all planning state;
/// the app only sends JSON commands and renders the resulting plan.
public struct PlannerScreen: View {
    @Environment(SnapshotStore.self) private var store
    @Environment(PlannerViewModel.self) private var planner

    public init() {}

    public var body: some View {
        @Bindable var planner = planner
        Group {
            switch planner.phase {
            case .idle, .loading:
                ProgressView("Loading planner…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                ContentUnavailableView {
                    Label("Couldn't build a plan", systemImage: "xmark.octagon")
                } description: {
                    Text(message)
                } actions: {
                    Button("Retry") {
                        Task { await planner.load(snapshot: store.snapshot) }
                    }
                }
            case .loaded:
                plannerContent
            }
        }
        .sheet(item: $planner.pendingConfirmation) { action in
            ConfirmationSheetView(
                action: action,
                onCancel: { planner.cancelConfirmation() },
                onConfirm: { Task { await planner.confirmPendingAction(snapshot: store.snapshot) } }
            )
        }
        .task {
            if store.snapshot == nil {
                await store.refresh()
            }
            await planner.load(snapshot: store.snapshot)
        }
        .onChange(of: store.lastUpdated) {
            planner.updateActivePlanRows(snapshot: store.snapshot)
        }
        .navigationTitle("Planner")
    }

    private var plannerContent: some View {
        HStack(spacing: 0) {
            controls
                .frame(minWidth: 300, idealWidth: 340, maxWidth: 380)
            Divider()
            preview
        }
    }

    private var controls: some View {
        @Bindable var planner = planner
        return Form {
            Section("Work window") {
                DatePicker("Date", selection: $planner.selectedDate, displayedComponents: .date)
                DatePicker("Start", selection: $planner.startTime, displayedComponents: .hourAndMinute)
                DatePicker("End", selection: $planner.endTime, displayedComponents: .hourAndMinute)
                LabeledContent("Window", value: planner.workWindow)
            }

            Section("Usage assumptions") {
                Picker("Mode", selection: $planner.usageMode) {
                    ForEach(UsageAssumptionMode.allCases) { mode in
                        Text(mode.label).tag(mode)
                    }
                }
                .pickerStyle(.segmented)

                if planner.usageMode == .custom {
                    ForEach(planner.planningAccounts, id: \.label) { account in
                        Stepper(
                            value: usageBinding(for: account),
                            in: 1...1440,
                            step: 15
                        ) {
                            LabeledContent(
                                account.label,
                                value: PlannerFormatting.duration(
                                    minutes: planner.customUsageMinutes[account.label]
                                        ?? account.usableSessionMinutes
                                )
                            )
                        }
                    }
                } else {
                    Text(defaultUsageSummary)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Section {
                Button {
                    Task { await planner.previewPlan() }
                } label: {
                    // Swap only the icon for the spinner so the button keeps
                    // its width while previewing.
                    if planner.isPreviewing {
                        Label {
                            Text("Preview Plan")
                        } icon: {
                            ProgressView().controlSize(.small)
                        }
                    } else {
                        Label("Preview Plan", systemImage: "arrow.triangle.2.circlepath")
                    }
                }
                .disabled(planner.isPreviewing || planner.isMutating)

                Button {
                    planner.requestApply()
                } label: {
                    Label("Apply Plan", systemImage: "checkmark.circle")
                }
                .disabled(!planner.canApplyPreview)

                if planner.canCancelActivePlan {
                    Button(role: .destructive) {
                        planner.requestCancelPlan()
                    } label: {
                        Label("Cancel Active Plan", systemImage: "xmark.circle")
                    }
                }
            } footer: {
                Text("Apply schedules the planned kicks for the background service. Cancel removes only kicks created by the Planner.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .padding(.horizontal, 8)
        .padding(.top, 8)
    }

    private var preview: some View {
        List {
            if let message = planner.actionMessage {
                Section {
                    Label(message, systemImage: "info.circle")
                        .foregroundStyle(.secondary)
                }
            }

            if !planner.activeOrchestratedPendingRows.isEmpty {
                Section("Active orchestration") {
                    ForEach(planner.activeOrchestratedPendingRows) { row in
                        HStack(spacing: 8) {
                            Text(row.account).fontWeight(.medium)
                            Spacer()
                            Text(row.kickAt)
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }

            warningsSection
            timelineSection
            plannedKicksSection
            skippedSection
            limitationsSection
        }
    }

    @ViewBuilder
    private var warningsSection: some View {
        if let preview = planner.preview, !preview.diff.conflictsUnmanaged.isEmpty {
            Section("Unmanaged conflicts") {
                ForEach(Array(preview.diff.conflictsUnmanaged.enumerated()), id: \.offset) { _, conflict in
                    Label(conflictSummary(conflict), systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                }
                Text("Pending kicks created outside the Planner overlap this window. Apply is disabled so they're never replaced.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }

        if let preview = planner.preview, !preview.coverageGaps.isEmpty {
            Section("Coverage gaps") {
                ForEach(preview.coverageGaps.indices, id: \.self) { index in
                    let gap = preview.coverageGaps[index]
                    HStack {
                        Text(PlannerFormatting.range(
                            gap.start,
                            gap.end,
                            reference: preview.flatMapWorkStart
                        ))
                        Spacer()
                        Text(gap.reason.replacingOccurrences(of: "_", with: " "))
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }

        if !storeReservationWarnings.isEmpty {
            Section("Reserved account warnings") {
                ForEach(storeReservationWarnings, id: \.self) { message in
                    Label(message, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
            }
        }
    }

    @ViewBuilder
    private var timelineSection: some View {
        if planner.segmentRows.isEmpty {
            Section {
                ContentUnavailableView {
                    Label("No plan preview", systemImage: "calendar.badge.clock")
                } description: {
                    Text("Choose a work window and preview an orchestration plan.")
                }
            }
        } else {
            Section("Timeline") {
                ForEach(planner.segmentRows) { row in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(row.time)
                                .font(.callout.monospacedDigit())
                                .frame(width: 140, alignment: .leading)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(row.account)
                                    .fontWeight(.medium)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Text(row.source)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                        }
                        if !row.notes.isEmpty {
                            Text(row.notes)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    @ViewBuilder
    private var plannedKicksSection: some View {
        if !planner.plannedKickRows.isEmpty {
            Section("Planned kicks") {
                ForEach(planner.plannedKickRows) { row in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(row.kickAt)
                                .font(.callout.monospacedDigit())
                                .frame(width: 86, alignment: .leading)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(row.account)
                                    .fontWeight(.medium)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Text("\(row.purpose) · \(row.covers) · \(row.usage)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    @ViewBuilder
    private var skippedSection: some View {
        if let preview = planner.preview, !preview.skippedAccounts.isEmpty {
            Section("Skipped accounts") {
                ForEach(preview.skippedAccounts, id: \.accountKey) { item in
                    HStack {
                        Text(item.accountLabel)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer()
                        Text(item.reason.replacingOccurrences(of: "_", with: " "))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var limitationsSection: some View {
        if let preview = planner.preview, !preview.limitations.isEmpty {
            Section("Limitations") {
                ForEach(preview.limitations, id: \.self) { limitation in
                    Text(limitation)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var defaultUsageSummary: String {
        let parts = planner.planningAccounts.prefix(4).map {
            "\($0.label): \(PlannerFormatting.duration(minutes: $0.usableSessionMinutes))"
        }
        let more = planner.planningAccounts.count - parts.count
        if more > 0 {
            return (parts + ["+\(more) more"]).joined(separator: ", ")
        }
        return parts.isEmpty ? "No planning defaults found." : parts.joined(separator: ", ")
    }

    private var storeReservationWarnings: [String] {
        (store.snapshot?.advisories ?? []).compactMap { $0["message"]?.stringValue }
    }

    private func usageBinding(for account: TKAccountsPlanningPayload.Account) -> Binding<Int> {
        Binding(
            get: { planner.customUsageMinutes[account.label] ?? account.usableSessionMinutes },
            set: { planner.customUsageMinutes[account.label] = $0 }
        )
    }

    private func conflictSummary(_ value: TKJSONValue) -> String {
        if let account = value["account_label"]?.stringValue {
            return account
        }
        if let key = value["account_key"]?.stringValue {
            return key
        }
        return "Unmanaged pending kick"
    }
}

private extension TKPlanPayload {
    var flatMapWorkStart: Date? {
        parseUTCISO(workWindow.start)
    }
}
