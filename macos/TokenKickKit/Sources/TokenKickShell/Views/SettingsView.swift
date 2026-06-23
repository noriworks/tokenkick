import SwiftUI

/// App-level Settings shell (UX plan §15). Account/provider configuration
/// stays in the Configure screens; the login toggle is the same switch as
/// the Daemon screen's background service — one source of truth.
public struct SettingsView: View {
    @Environment(AppSettingsModel.self) private var settings
    @Environment(DaemonViewModel.self) private var daemonModel
    @Environment(SnapshotStore.self) private var store

    public init() {}

    public var body: some View {
        TabView {
            generalTab
                .tabItem { Label("General", systemImage: "gearshape") }
            environmentTab
                .tabItem { Label("Environment", systemImage: "terminal") }
        }
        .frame(width: 480, height: 320)
    }

    private var generalTab: some View {
        @Bindable var settings = settings
        @Bindable var daemonModel = daemonModel
        return Form {
            Section {
                Toggle("Run TokenKick in the background at login", isOn: backgroundToggleBinding)
                    .disabled(daemonModel.isBusy || daemonModel.agentStatus == nil || terminalManaged)
                backgroundCaption
                backgroundResult
            }
            Section {
                Picker("Refresh status every", selection: $settings.refreshInterval) {
                    ForEach(AppSettingsModel.RefreshInterval.allCases) { interval in
                        Text(interval.label).tag(interval)
                    }
                }
            }
            Section {
                Toggle("Check for updates and show availability", isOn: $settings.updateChecksVisible)
            }
        }
        .formStyle(.grouped)
        .task {
            await daemonModel.reloadAgentStatus(daemon: store.snapshot?.daemon)
        }
        .sheet(item: $daemonModel.pendingConfirmation) { action in
            ConfirmationSheetView(
                action: action,
                onCancel: { daemonModel.cancelConfirmation() },
                onConfirm: {
                    Task { await daemonModel.confirmPendingAction(daemon: store.snapshot?.daemon) }
                }
            )
        }
    }

    private var terminalManaged: Bool {
        store.daemonOwnership.kind == .terminalManaged
    }

    /// The LaunchAgent is the single source of truth: on installs and starts
    /// it directly (like the Daemon screen); off goes through the same
    /// remove confirmation. Cancelling snaps the toggle back.
    private var backgroundToggleBinding: Binding<Bool> {
        Binding(
            get: { daemonModel.agentStatus?.installed == true },
            set: { enabled in
                if enabled {
                    Task {
                        await daemonModel.performDirect(.enableBackground, daemon: store.snapshot?.daemon)
                    }
                } else {
                    daemonModel.requestRemoveAgent()
                }
            }
        )
    }

    @ViewBuilder
    private var backgroundCaption: some View {
        Group {
            if daemonModel.agentStatus == nil {
                Text("Background service status unavailable.")
            } else if terminalManaged {
                Text("A terminal-managed daemon is running — use Daemon → Manage with TokenKick… first.")
            } else {
                Text("Keeps kicks working after a restart. Same switch as Daemon → Background service.")
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    @ViewBuilder
    private var backgroundResult: some View {
        if case .finished(_, let success, let message) = daemonModel.phase {
            Label {
                Text(message).font(.caption)
            } icon: {
                Image(systemName: success ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(success ? Color.green : Color.red)
            }
        }
    }

    private var environmentTab: some View {
        @Bindable var settings = settings
        return Form {
            Section {
                if settings.extraPathEntries.isEmpty {
                    Text("No extra locations.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(settings.extraPathEntries, id: \.self) { entry in
                        HStack {
                            Text(entry)
                                .font(.callout.monospaced())
                                .truncationMode(.middle)
                                .lineLimit(1)
                            Spacer()
                            entryIndicator(entry)
                            Button {
                                if let index = settings.extraPathEntries.firstIndex(of: entry) {
                                    settings.removePathEntries(at: IndexSet(integer: index))
                                }
                            } label: {
                                Image(systemName: "minus.circle")
                            }
                            .buttonStyle(.borderless)
                        }
                    }
                }
                PathEntryField { entry in
                    settings.addPathEntry(entry)
                }
            } header: {
                Text("Extra PATH locations")
            } footer: {
                Text("Apps launched from Finder don't see your shell PATH. TokenKick already checks the common install locations; add others here so provider CLIs like codex and claude are found.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }

    @ViewBuilder
    private func entryIndicator(_ entry: String) -> some View {
        let tools = AppSettingsModel.providerTools(foundIn: entry)
        if tools.isEmpty {
            Label("nothing found", systemImage: "questionmark.circle")
                .font(.caption)
                .foregroundStyle(.secondary)
        } else {
            Label(tools.joined(separator: ", "), systemImage: "checkmark.circle.fill")
                .font(.caption)
                .foregroundStyle(.green)
        }
    }
}

private struct PathEntryField: View {
    var onSubmit: (String) -> Void
    @State private var text = ""

    var body: some View {
        HStack {
            TextField("/path/to/bin", text: $text)
                .textFieldStyle(.roundedBorder)
                .font(.callout.monospaced())
                .onSubmit(submit)
            Button("Add", action: submit)
                .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
        }
    }

    private func submit() {
        let entry = text.trimmingCharacters(in: .whitespaces)
        guard !entry.isEmpty else { return }
        onSubmit(entry)
        text = ""
    }
}
