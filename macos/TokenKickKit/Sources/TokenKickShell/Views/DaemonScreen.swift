import SwiftUI
import TokenKickKit

/// Daemon screen (UX plan §11): ownership chip, Phase 3 status data, and —
/// since Phase 5B — live controls. Stop, takeover, and remove confirm via
/// the shared sheet with Cancel as the default; start/restart/repair run
/// directly.
public struct DaemonScreen: View {
    @Environment(SnapshotStore.self) private var store
    @Environment(DaemonViewModel.self) private var daemonModel

    public init() {}

    public var body: some View {
        @Bindable var daemonModel = daemonModel
        Form {
            ownershipSection
            resultSection
            controlsSection
            backgroundServiceSection
            detailSection
        }
        .formStyle(.grouped)
        .navigationTitle("Daemon")
        .sheet(item: $daemonModel.pendingConfirmation) { action in
            ConfirmationSheetView(
                action: action,
                onCancel: { daemonModel.cancelConfirmation() },
                onConfirm: {
                    Task { await daemonModel.confirmPendingAction(daemon: daemon) }
                }
            )
        }
        .task {
            await daemonModel.reloadAgentStatus(daemon: daemon)
        }
    }

    private var ownership: DaemonOwnershipPresentation { store.daemonOwnership }
    private var daemon: TKDaemonStatus? { store.snapshot?.daemon }

    private var ownershipSection: some View {
        Section {
            HStack(spacing: 10) {
                Image(systemName: ownershipSymbol)
                    .font(.title2)
                    .foregroundStyle(ownershipColor)
                VStack(alignment: .leading, spacing: 2) {
                    Text(ownership.title).font(.headline)
                    Text(ownership.detail)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if daemonModel.isBusy {
                    ProgressView().controlSize(.small)
                }
            }
            .padding(.vertical, 4)
        }
    }

    @ViewBuilder
    private var resultSection: some View {
        if case .finished(_, let success, let message) = daemonModel.phase {
            Section {
                HStack(spacing: 8) {
                    Image(systemName: success ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(success ? Color.green : Color.red)
                    Text(message)
                        .font(.callout)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    Button {
                        daemonModel.dismissResult()
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(.tertiary)
                    }
                    .buttonStyle(.plain)
                    .help("Dismiss")
                }
            }
        }
    }

    /// Controls follow ownership (UX plan §11); takeover is never implicit.
    private var controlsSection: some View {
        Section("Controls") {
            HStack(spacing: 8) {
                switch ownership.kind {
                case .appManaged:
                    stopButton
                    restartButton
                case .terminalManaged:
                    Button("Manage with TokenKick…") {
                        daemonModel.requestTakeover(daemon: daemon)
                    }
                    .disabled(daemonModel.isBusy)
                case .unknownRunning:
                    stopButton
                    restartButton
                case .notRunning:
                    Button("Start Daemon") {
                        Task { await daemonModel.performDirect(.start, daemon: daemon) }
                    }
                    .disabled(daemonModel.isBusy)
                case .stale:
                    Button("Clean Up & Start") {
                        Task { await daemonModel.performDirect(.start, daemon: daemon) }
                    }
                    .disabled(daemonModel.isBusy)
                }
            }
        }
    }

    private var stopButton: some View {
        Button("Stop Daemon") {
            daemonModel.requestStop()
        }
        .disabled(daemonModel.isBusy)
    }

    private var restartButton: some View {
        Button("Restart Daemon") {
            Task { await daemonModel.performDirect(.restart, daemon: daemon) }
        }
        .disabled(daemonModel.isBusy)
    }

    @ViewBuilder
    private var backgroundServiceSection: some View {
        Section {
            if let agent = daemonModel.agentStatus {
                if agent.installed {
                    LabeledContent("Login item", value: agent.loaded ? "Installed, active" : "Installed")
                    if agent.needsRepair {
                        HStack {
                            Label("The service files need repair", systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                                .font(.callout)
                            Spacer()
                            Button("Repair") {
                                Task { await daemonModel.performDirect(.repairAgent, daemon: daemon) }
                            }
                            .disabled(daemonModel.isBusy)
                        }
                    }
                    Button("Remove background service…") {
                        daemonModel.requestRemoveAgent()
                    }
                    .disabled(daemonModel.isBusy)
                } else {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Start automatically at login")
                            Text("Installs the TokenKick background service so kicks keep working after a restart.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Enable Background Kicking") {
                            Task { await daemonModel.performDirect(.enableBackground, daemon: daemon) }
                        }
                        .disabled(daemonModel.isBusy || ownership.kind == .terminalManaged)
                    }
                    if ownership.kind == .terminalManaged {
                        Text("A terminal-managed daemon is running — use “Manage with TokenKick…” first.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            } else {
                Text("Background service status unavailable.")
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("Background service")
        }
    }

    @ViewBuilder
    private var detailSection: some View {
        if let daemon {
            Section("Details") {
                if daemon.running, let uptime = daemon.uptimeSeconds {
                    LabeledContent("Uptime", value: RelativeTimeText.duration(TimeInterval(uptime)))
                }
                if let version = daemon.version {
                    LabeledContent("Version") {
                        HStack(spacing: 6) {
                            Text("v\(version)").monospacedDigit()
                            if daemon.versionMatch == false {
                                Label("v\(daemon.installedVersion) installed", systemImage: "exclamationmark.triangle.fill")
                                    .font(.caption)
                                    .foregroundStyle(.orange)
                            }
                        }
                    }
                }
                if let executable = daemon.executable {
                    LabeledContent("Executable") {
                        HStack(spacing: 6) {
                            Text(executable)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                                .truncationMode(.middle)
                                .lineLimit(1)
                            if daemon.executableMatch == false {
                                Image(systemName: "exclamationmark.triangle.fill")
                                    .foregroundStyle(.orange)
                                    .help("Differs from the app's bundled runtime")
                            }
                        }
                    }
                }
                LabeledContent("Poll interval", value: "\(daemon.pollIntervalMinutes) m")
                LabeledContent("Log") {
                    HStack(spacing: 8) {
                        Text(daemon.logPath)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .truncationMode(.middle)
                            .lineLimit(1)
                        Button("Open Log") {
                            NSWorkspace.shared.open(URL(fileURLWithPath: daemon.logPath))
                        }
                        .controlSize(.small)
                    }
                }
            }
        } else {
            Section("Details") {
                Text("Refresh to load background service details.")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var ownershipSymbol: String {
        switch ownership.kind {
        case .appManaged: return "checkmark.seal.fill"
        case .terminalManaged: return "terminal"
        case .unknownRunning: return "questionmark.circle"
        case .notRunning: return "pause.circle"
        case .stale: return "exclamationmark.circle"
        }
    }

    private var ownershipColor: Color {
        switch ownership.kind {
        case .appManaged: return .green
        case .terminalManaged, .unknownRunning: return .secondary
        case .notRunning: return .secondary
        case .stale: return .orange
        }
    }
}
