import SwiftUI
import TokenKickKit

/// Notifications (UX plan Configure section): per-account routing and the
/// global destination are both editable through the core's JSON commands
/// (`tk accounts set-notifications`, `tk notify`).
public struct NotificationsScreen: View {
    @Environment(AccountsViewModel.self) private var accounts

    public init() {}

    public var body: some View {
        Group {
            switch accounts.loadPhase {
            case .idle, .loading:
                ProgressView("Loading notification routes…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                ContentUnavailableView {
                    Label("Couldn't read notification routes", systemImage: "xmark.octagon")
                } description: {
                    Text(message)
                } actions: {
                    Button("Retry") { Task { await accounts.load() } }
                }
            case .loaded:
                form
            }
        }
        .task { await accounts.load() }
        .navigationTitle("Notifications")
    }

    private var form: some View {
        Form {
            globalSection
            accountsSection
        }
        .formStyle(.grouped)
    }

    @ViewBuilder
    private var globalSection: some View {
        Section {
            if let summary = accounts.notifications {
                LabeledContent("Notifications", value: summary.globalEnabled ? "Enabled" : "Disabled")
                LabeledContent("Destination", value: Self.destinationDisplay(summary.destination))
                if !summary.backends.isEmpty {
                    LabeledContent("Backends", value: summary.backends.joined(separator: ", "))
                }
            } else {
                Text("No global notification summary available.")
                    .foregroundStyle(.secondary)
            }
            globalResult
        } header: {
            Text("Global destination")
        }
        GlobalDestinationEditor()
    }

    @ViewBuilder
    private var globalResult: some View {
        if accounts.globalBusy {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Applying…").font(.caption).foregroundStyle(.secondary)
            }
        } else if let message = accounts.globalMutationMessage {
            Label {
                Text(message).font(.callout)
            } icon: {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            }
        } else if let error = accounts.globalMutationError {
            Label {
                Text(error).font(.callout)
            } icon: {
                Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
            }
        }
    }

    @ViewBuilder
    private var accountsSection: some View {
        Section("Per-account routing") {
            if accounts.rows.isEmpty {
                Text("No accounts yet — discover accounts first.")
                    .foregroundStyle(.secondary)
            }
            ForEach(accounts.rows) { row in
                accountRow(row)
            }
        }
    }

    @ViewBuilder
    private func accountRow(_ row: AccountConfigRow) -> some View {
        let current = NotificationRoute.from(
            routeDisplay: row.list.notificationsRoute,
            enabled: row.list.notificationsEnabled
        )
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                VStack(alignment: .leading, spacing: 1) {
                    Text(row.label).truncationMode(.middle).lineLimit(1)
                    Text(Self.routeDisplay(row.list.notificationsRoute))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                if accounts.isBusy(row.label) {
                    ProgressView().controlSize(.small)
                }
                Picker("", selection: Binding(
                    get: { current },
                    set: { newRoute in
                        guard newRoute != current else { return }
                        Task {
                            await accounts.apply(.setNotificationRoute(newRoute), to: row.label)
                        }
                    }
                )) {
                    ForEach(NotificationRoute.allCases) { route in
                        Text(route.label).tag(route)
                    }
                }
                .labelsHidden()
                .fixedSize()
                .disabled(accounts.isBusy(row.label))
            }
            if let error = accounts.mutationErrors[row.label] {
                Label {
                    Text(error).font(.caption)
                } icon: {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                }
            }
        }
        .padding(.vertical, 2)
    }

    /// The core's route strings ("❌ disabled", "✅ ntfy+telegram") are
    /// terminal-flavored; map the known set to native wording.
    static func routeDisplay(_ raw: String) -> String {
        switch raw {
        case "❌ disabled": return "Off"
        case "✅ ntfy": return "ntfy"
        case "✅ telegram": return "Telegram"
        case "✅ ntfy+telegram", "✅ telegram+ntfy": return "ntfy + Telegram"
        default: return raw
        }
    }

    static func destinationDisplay(_ raw: String) -> String {
        raw == "global disabled" ? "Not configured" : raw
    }
}

/// Editable global destinations through `tk notify --json-output`.
/// Configuring a destination enables it; secret values are write-only —
/// the summary above never echoes tokens back.
private struct GlobalDestinationEditor: View {
    @Environment(AccountsViewModel.self) private var accounts
    @State private var ntfyTopic = ""
    @State private var telegramToken = ""
    @State private var telegramChatID = ""

    var body: some View {
        Section {
            HStack {
                TextField("ntfy topic", text: $ntfyTopic)
                    .textFieldStyle(.roundedBorder)
                    .font(.callout.monospaced())
                Button("Enable ntfy") {
                    let topic = ntfyTopic.trimmingCharacters(in: .whitespaces)
                    guard !topic.isEmpty else { return }
                    Task {
                        await accounts.applyGlobal(.enableNtfy(topic: topic))
                        ntfyTopic = ""
                    }
                }
                .disabled(
                    accounts.globalBusy
                        || ntfyTopic.trimmingCharacters(in: .whitespaces).isEmpty
                )
            }
            HStack {
                SecureField("Telegram bot token", text: $telegramToken)
                    .textFieldStyle(.roundedBorder)
                TextField("Chat ID", text: $telegramChatID)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 120)
                Button("Enable Telegram") {
                    let token = telegramToken.trimmingCharacters(in: .whitespaces)
                    let chatID = telegramChatID.trimmingCharacters(in: .whitespaces)
                    guard !token.isEmpty, !chatID.isEmpty else { return }
                    Task {
                        await accounts.applyGlobal(
                            .enableTelegram(token: token, chatID: chatID)
                        )
                        telegramToken = ""
                        telegramChatID = ""
                    }
                }
                .disabled(
                    accounts.globalBusy
                        || telegramToken.trimmingCharacters(in: .whitespaces).isEmpty
                        || telegramChatID.trimmingCharacters(in: .whitespaces).isEmpty
                )
            }
            Button("Send test notification") {
                Task { await accounts.applyGlobal(.sendTest) }
            }
            .disabled(accounts.globalBusy || accounts.notifications?.globalEnabled != true)
        } header: {
            Text("Configure destination")
        } footer: {
            Text("Configuring a destination enables it for all routed accounts. Per-account routing below decides who uses it.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }
}
