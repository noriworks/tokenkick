import SwiftUI
import TokenKickKit

/// Discovery checklist shared by first-run and the Accounts screen:
/// completed steps with checkmarks, a spinner on the current one, and the
/// terminal state rendered inline (UX plan §8 — never a log dump).
struct SetupChecklistView: View {
    @Environment(SetupViewModel.self) private var setup

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            switch setup.phase {
            case .idle:
                EmptyView()
            case .running, .completed, .noAccounts, .failed, .cancelled:
                steps
            }
            terminalState
        }
    }

    private var steps: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(setup.steps) { step in
                HStack(spacing: 8) {
                    if step.id == setup.steps.count - 1, setup.isRunning {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    }
                    Text(step.title)
                        .font(.callout)
                        .foregroundStyle(step.id == setup.steps.count - 1 ? .primary : .secondary)
                }
            }
        }
    }

    @ViewBuilder
    private var terminalState: some View {
        switch setup.phase {
        case .idle, .running:
            EmptyView()
        case .completed(let summary):
            VStack(alignment: .leading, spacing: 6) {
                Label(summary.summaryText, systemImage: "checkmark.seal.fill")
                    .foregroundStyle(.green)
                    .font(.callout.weight(.medium))
                if !summary.newAccountLabels.isEmpty {
                    Text("New: \(summary.newAccountLabels.joined(separator: ", "))")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(summary.warnings, id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }
            }
        case .noAccounts(let message):
            VStack(alignment: .leading, spacing: 6) {
                Label("No accounts found", systemImage: "person.crop.circle.badge.questionmark")
                    .font(.callout.weight(.medium))
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text("Log in with `codex` or `claude` in a terminal first, then try again.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        case .failed(let message):
            Label {
                Text(message).font(.caption)
            } icon: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
            }
        case .cancelled:
            Label("Discovery cancelled — nothing was changed.", systemImage: "minus.circle")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }
}

/// First-run flow (UX plan §10): Welcome → Discover → Background. Window
/// based, skippable at every point, ends on Status.
struct FirstRunView: View {
    @Environment(FirstRunModel.self) private var firstRun
    @Environment(SetupViewModel.self) private var setup
    @Environment(DaemonViewModel.self) private var daemonModel
    @Environment(SnapshotStore.self) private var store
    var onFinished: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            content
            Spacer(minLength: 0)
            footer
        }
        .padding(24)
        .frame(width: 520, height: 380)
        .onChange(of: setup.phase) { _, newPhase in
            firstRun.discoveryResolved(newPhase)
        }
    }

    @ViewBuilder
    private var content: some View {
        switch firstRun.step {
        case .welcome:
            VStack(alignment: .leading, spacing: 12) {
                Text("Welcome to TokenKick").font(.title2.weight(.semibold))
                Text("TokenKick starts your AI coding quota windows the moment they reset — a tiny request (a “kick”) so the clock runs while you sleep, not while you work.")
                    .fixedSize(horizontal: false, vertical: true)
                Text("Everything runs locally. TokenKick talks to provider CLIs you've already signed into; it never sees your API keys.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        case .discover:
            VStack(alignment: .leading, spacing: 12) {
                Text("Find your accounts").font(.title2.weight(.semibold))
                Text("TokenKick looks for the provider CLIs you're already logged into.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                SetupChecklistView()
            }
        case .background:
            VStack(alignment: .leading, spacing: 12) {
                Text("Run in the background").font(.title2.weight(.semibold))
                Text("The background service watches for resets and kicks automatically — it's how TokenKick does its job without the app open.")
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                if case .finished(_, let success, let message) = daemonModel.phase {
                    Label {
                        Text(message).font(.callout)
                    } icon: {
                        Image(systemName: success ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(success ? Color.green : Color.red)
                    }
                }
            }
        case .done:
            EmptyView()
        }
    }

    @ViewBuilder
    private var footer: some View {
        HStack {
            Button("Not Now") { finish() }
            Spacer()
            switch firstRun.step {
            case .welcome:
                Button("Find My Accounts") {
                    firstRun.beginDiscovery()
                    setup.startDiscovery()
                }
                .buttonStyle(.borderedProminent)
            case .discover:
                if setup.isRunning {
                    Button("Cancel Discovery") { setup.cancelDiscovery() }
                } else {
                    switch setup.phase {
                    case .noAccounts, .failed, .cancelled:
                        Button("Try Again") { setup.startDiscovery() }
                            .buttonStyle(.borderedProminent)
                    default:
                        EmptyView()
                    }
                }
            case .background:
                Button("Enable Background Kicking") {
                    Task {
                        await daemonModel.performDirect(
                            .enableBackground,
                            daemon: store.snapshot?.daemon
                        )
                        finish()
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(daemonModel.isBusy)
            case .done:
                EmptyView()
            }
        }
    }

    private func finish() {
        firstRun.finish()
        onFinished()
    }
}
