import SwiftUI
import TokenKickKit

/// Accounts management (UX plan §8/§9): compact list + inspector for the
/// selected account. Core refusals surface inline; first-time provider
/// automation consent uses the core-provided disclosure in a typed sheet.
public struct AccountsScreen: View {
    @Environment(SnapshotStore.self) private var store
    @Environment(AccountsViewModel.self) private var accounts
    @Environment(SetupViewModel.self) private var setup
    @State private var discoverSheetShown = false

    public init() {}

    public var body: some View {
        @Bindable var accounts = accounts
        Group {
            switch accounts.loadPhase {
            case .idle, .loading:
                ProgressView("Loading accounts…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                ContentUnavailableView {
                    Label("Couldn't read accounts", systemImage: "xmark.octagon")
                } description: {
                    Text(message)
                } actions: {
                    Button("Retry") { Task { await accounts.load() } }
                }
            case .loaded:
                if accounts.rows.isEmpty {
                    ContentUnavailableView {
                        Label("No accounts yet", systemImage: "person.crop.circle.badge.questionmark")
                    } description: {
                        Text("TokenKick finds the provider CLIs you're already logged into.")
                    } actions: {
                        Button("Discover Accounts") { startDiscovery() }
                            .buttonStyle(.borderedProminent)
                    }
                } else {
                    accountsList
                }
            }
        }
        .toolbar {
            ToolbarItem {
                Button {
                    startDiscovery()
                } label: {
                    Label("Discover Accounts", systemImage: "magnifyingglass")
                }
                .help("Re-run account discovery")
            }
        }
        .sheet(isPresented: $discoverSheetShown) {
            discoverSheet
        }
        .sheet(item: Binding(
            get: { accounts.pendingAutoKickConsent },
            set: { request in
                if request == nil { accounts.cancelAutoKickConsent() }
            }
        )) { request in
            AutoKickConsentSheet(
                request: request,
                onCancel: { accounts.cancelAutoKickConsent() },
                onConfirm: { Task { await accounts.confirmAutoKickConsent() } }
            )
        }
        .inspector(isPresented: Binding(
            get: { accounts.selectedRow != nil },
            set: { shown in
                if !shown { accounts.selectedLabel = nil }
            }
        )) {
            if let row = accounts.selectedRow {
                AccountInspectorView(row: row)
                    .inspectorColumnWidth(min: 280, ideal: 320)
            }
        }
        .task { await accounts.load() }
        .navigationTitle("Accounts")
    }

    private var accountsList: some View {
        @Bindable var accounts = accounts
        return List(selection: $accounts.selectedLabel) {
            ForEach(accounts.rows) { row in
                HStack(spacing: 8) {
                    Text(badge(row.list.provider))
                        .font(.caption2.weight(.bold))
                        .padding(.horizontal, 4)
                        .padding(.vertical, 2)
                        .background(.quaternary, in: RoundedRectangle(cornerRadius: 4))
                    VStack(alignment: .leading, spacing: 1) {
                        Text(row.label).truncationMode(.middle).lineLimit(1)
                        Text(subtitle(row))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if accounts.isBusy(row.label) {
                        ProgressView().controlSize(.small)
                    }
                    if !row.list.visible {
                        Image(systemName: "eye.slash")
                            .foregroundStyle(.secondary)
                            .help("Hidden from status")
                    }
                }
                .tag(row.label)
            }
        }
    }

    private var discoverSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Discover Accounts").font(.headline)
            SetupChecklistView()
            HStack {
                Spacer()
                if setup.isRunning {
                    Button("Cancel Discovery") { setup.cancelDiscovery() }
                } else {
                    Button("Close") { discoverSheetShown = false }
                        .keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(20)
        .frame(width: 460, height: 320)
    }

    private func startDiscovery() {
        setup.reset()
        discoverSheetShown = true
        setup.startDiscovery()
    }

    private func badge(_ provider: String) -> String {
        switch provider {
        case "codex": return "CX"
        case "claude": return "CL"
        case "gemini": return "GM"
        default: return provider.prefix(2).uppercased()
        }
    }

    private func subtitle(_ row: AccountConfigRow) -> String {
        if row.list.monitorOnly { return "\(row.list.provider) · monitor-only" }
        let auto = row.list.autoKick ? "auto-kick on" : "auto-kick off"
        return "\(row.list.provider) · \(auto)"
    }
}

private struct AutoKickConsentSheet: View {
    let request: AutoKickConsentRequest
    let onCancel: () -> Void
    let onConfirm: () -> Void
    @State private var confirmation = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ScrollView {
                Text(request.text)
                    .font(.callout.monospaced())
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 390)

            TextField("Type ENABLE", text: $confirmation)
                .textFieldStyle(.roundedBorder)

            HStack {
                Spacer()
                Button("Cancel", action: onCancel)
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                Button("Enable auto-kick", action: onConfirm)
                    .buttonStyle(.bordered)
                    .disabled(confirmation != request.confirmation)
            }
        }
        .padding(20)
        .frame(width: 560)
    }
}

/// Inspector for one account: visibility, automation, planning, routing.
struct AccountInspectorView: View {
    @Environment(AccountsViewModel.self) private var accounts
    let row: AccountConfigRow

    var body: some View {
        Form {
            if let error = accounts.mutationErrors[row.label] {
                Section {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                        Text(error)
                            .font(.caption)
                            .fixedSize(horizontal: false, vertical: true)
                        Spacer()
                        Button {
                            accounts.clearError(for: row.label)
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.tertiary)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            Section("Account") {
                LabeledContent("Provider", value: row.list.provider)
                Toggle("Visible in status", isOn: binding(
                    get: { row.list.visible },
                    mutation: { .setVisible($0) }
                ))
            }

            Section("Automation") {
                Toggle("Auto-kick", isOn: binding(
                    get: { row.list.autoKick },
                    mutation: { .setAutoKick($0) }
                ))
                .disabled(row.list.monitorOnly)
                Toggle("Session (5 h) auto-kick", isOn: binding(
                    get: { row.list.sessionAutoKick },
                    mutation: { .setSessionAutoKick($0) }
                ))
                .disabled(row.list.monitorOnly)
                Toggle("Weekly auto-kick", isOn: binding(
                    get: { row.list.weeklyAutoKick },
                    mutation: { .setWeeklyAutoKick($0) }
                ))
                .disabled(row.list.monitorOnly)
                if row.list.monitorOnly {
                    Text("This provider is monitor-only; it can't be kicked.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if let planning = row.planning {
                Section("Planning") {
                    Stepper(
                        value: bindingInt(
                            get: { planning.usableSessionMinutes },
                            mutation: { .setUsableSessionMinutes($0) }
                        ),
                        in: 30...1440,
                        step: 30
                    ) {
                        LabeledContent("Usable minutes", value: "\(planning.usableSessionMinutes) m")
                    }
                    Picker("Role", selection: bindingString(
                        get: { planning.orchestrationRole },
                        mutation: { .setOrchestrationRole($0) }
                    )) {
                        Text("Use first").tag("use_first")
                        Text("Normal").tag("normal")
                        Text("Backup").tag("backup")
                        Text("Specialist").tag("specialist")
                        Text("Excluded").tag("excluded")
                    }
                    if planning.effectiveOrchestrationRole != planning.orchestrationRole {
                        Text("Effective role right now: \(roleDisplay(planning.effectiveOrchestrationRole))")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    reserveControl(planning)
                }
            }
        }
        .formStyle(.grouped)
        .disabled(accounts.isBusy(row.label))
        .navigationTitle(row.label)
    }

    @ViewBuilder
    private func reserveControl(_ planning: TKAccountsPlanningPayload.Account) -> some View {
        if let threshold = planning.weeklyReserveThresholdPercent {
            Stepper(
                value: bindingInt(
                    get: { threshold },
                    mutation: { .setWeeklyReserveThreshold($0) }
                ),
                in: 1...99,
                step: 5
            ) {
                LabeledContent("Weekly reserve", value: "\(threshold) %")
            }
            Button("Clear weekly reserve") {
                apply(.setWeeklyReserveThreshold(nil))
            }
            .controlSize(.small)
        } else {
            HStack {
                LabeledContent("Weekly reserve", value: "—")
                Spacer()
                Button("Set 80 %") {
                    apply(.setWeeklyReserveThreshold(80))
                }
                .controlSize(.small)
                .help("Demote to backup once weekly usage passes the threshold")
            }
        }
    }

    private func apply(_ mutation: AccountMutation) {
        Task { await accounts.apply(mutation, to: row.label) }
    }

    /// Raw role values ("use_first") in display form ("Use first").
    private func roleDisplay(_ raw: String) -> String {
        let text = raw.replacingOccurrences(of: "_", with: " ")
        return text.prefix(1).uppercased() + text.dropFirst()
    }

    private func binding(
        get: @escaping () -> Bool,
        mutation: @escaping (Bool) -> AccountMutation
    ) -> Binding<Bool> {
        Binding(get: get) { newValue in
            apply(mutation(newValue))
        }
    }

    private func bindingInt(
        get: @escaping () -> Int,
        mutation: @escaping (Int) -> AccountMutation
    ) -> Binding<Int> {
        Binding(get: get) { newValue in
            apply(mutation(newValue))
        }
    }

    private func bindingString(
        get: @escaping () -> String,
        mutation: @escaping (String) -> AccountMutation
    ) -> Binding<String> {
        Binding(get: get) { newValue in
            apply(mutation(newValue))
        }
    }
}
