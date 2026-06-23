import SwiftUI

/// Native smart schedule editor. Mutations always go through `tk schedule`
/// JSON commands so CLI/TUI state ownership stays in the core.
public struct ScheduleScreen: View {
    @Environment(ScheduleViewModel.self) private var schedule

    public init() {}

    public var body: some View {
        @Bindable var schedule = schedule
        Group {
            switch schedule.phase {
            case .idle, .loading:
                ProgressView("Loading schedule…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                ContentUnavailableView {
                    Label("Couldn't read schedule", systemImage: "xmark.octagon")
                } description: {
                    Text(message)
                } actions: {
                    Button("Retry") { Task { await schedule.load() } }
                }
            case .loaded:
                scheduleContent
            }
        }
        .sheet(item: $schedule.pendingConfirmation) { action in
            ConfirmationSheetView(
                action: action,
                onCancel: { schedule.cancelConfirmation() },
                onConfirm: { Task { await schedule.confirmPendingAction() } }
            )
        }
        .task { await schedule.load() }
        .navigationTitle("Schedule")
    }

    private var scheduleContent: some View {
        HStack(spacing: 0) {
            scopeList
                .frame(minWidth: 260, idealWidth: 300, maxWidth: 340)
            Divider()
            editor
        }
    }

    private var scopeList: some View {
        @Bindable var schedule = schedule
        let selection = Binding<String?>(
            get: { schedule.selectedScope },
            set: { newValue in
                if let newValue {
                    schedule.selectedScope = newValue
                }
            }
        )
        return List(selection: selection) {
            Section("Scopes") {
                ForEach(schedule.rows) { row in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Label(row.title, systemImage: row.isDefault ? "calendar" : "person")
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer()
                            Image(systemName: row.enabled ? "checkmark.circle.fill" : "circle")
                                .foregroundStyle(row.enabled ? Color.green : Color.secondary)
                        }
                        HStack(spacing: 6) {
                            Text("Weekdays \(row.weekdays)")
                            Text("Weekends \(row.weekends)")
                            if row.pendingCount > 0 {
                                Text("\(row.pendingCount) queued")
                            }
                        }
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    }
                    .padding(.vertical, 2)
                    .tag(String?.some(row.id))
                }
            }
        }
    }

    private var editor: some View {
        @Bindable var schedule = schedule
        return Form {
            if let message = schedule.resultMessage {
                Section {
                    Label(message, systemImage: "info.circle")
                        .foregroundStyle(.secondary)
                }
            }

            Section {
                TextField("Weekdays", text: $schedule.weekdays, prompt: Text("09:00-17:00"))
                    .textFieldStyle(.roundedBorder)
                    .help("HH:MM-HH:MM, for example 09:00-17:00")
                TextField("Weekends", text: $schedule.weekends, prompt: Text("10:00-15:00"))
                    .textFieldStyle(.roundedBorder)
                    .help("HH:MM-HH:MM, for example 10:00-15:00")
                TextField("Timezone", text: $schedule.timezone, prompt: Text("Europe/Berlin"))
                    .textFieldStyle(.roundedBorder)
                    .help("IANA timezone, for example Europe/Berlin")
                HStack {
                    Button {
                        Task { await schedule.save() }
                    } label: {
                        // Swap only the icon for the spinner so the button
                        // keeps its width while saving.
                        if schedule.isMutating {
                            Label {
                                Text("Save Schedule")
                            } icon: {
                                ProgressView().controlSize(.small)
                            }
                        } else {
                            Label("Save Schedule", systemImage: "checkmark.circle")
                        }
                    }
                    .disabled(!schedule.canSave)

                    if schedule.selectedScopeEnabled {
                        Button {
                            schedule.requestDisable()
                        } label: {
                            Label("Disable", systemImage: "pause.circle")
                        }
                        .disabled(schedule.isMutating)
                    } else {
                        Button {
                            Task { await schedule.enable() }
                        } label: {
                            Label("Enable", systemImage: "play.circle")
                        }
                        .disabled(schedule.isMutating)
                    }

                    Button(role: .destructive) {
                        schedule.requestClear()
                    } label: {
                        Label("Clear", systemImage: "trash")
                    }
                    .disabled(schedule.isMutating)
                }
            } header: {
                Text(selectedTitle)
            } footer: {
                Text("Use HH:MM-HH:MM. Empty fields stay unchanged on save. Save and Enable both re-enable a disabled scope; Clear removes the configured windows.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            pendingSection
        }
        .formStyle(.grouped)
        .padding(.horizontal, 8)
        .padding(.top, 8)
    }

    @ViewBuilder
    private var pendingSection: some View {
        let rows = visiblePendingRows
        if rows.isEmpty {
            Section("Pending kicks") {
                Text("No pending kicks for this scope.")
                    .foregroundStyle(.secondary)
            }
        } else {
            Section("Pending kicks") {
                ForEach(rows) { row in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(row.account)
                                .fontWeight(.medium)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer()
                            Text(row.kickAt)
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                        Text("\(row.reason) · \(row.purpose) · \(row.status)")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    private var selectedTitle: String {
        schedule.selectedScope == "default"
            ? "Default smart schedule"
            : "Smart schedule for \(schedule.selectedScope)"
    }

    private var visiblePendingRows: [SchedulePendingKickRow] {
        if schedule.selectedScope == "default" {
            return schedule.pendingKicks
        }
        return schedule.pendingKicks.filter { $0.account == schedule.selectedScope }
    }
}
