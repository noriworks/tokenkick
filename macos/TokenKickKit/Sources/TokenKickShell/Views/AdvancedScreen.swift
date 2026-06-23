import SwiftUI
import TokenKickKit

/// Advanced provider internals (UX plan §9): Codex strategy, surface stats,
/// demotion overrides, and provider-specific experimental toggles.
public struct AdvancedScreen: View {
    @Environment(AdvancedViewModel.self) private var advanced

    public init() {}

    public var body: some View {
        @Bindable var advanced = advanced
        Group {
            switch advanced.phase {
            case .idle, .loading:
                ProgressView("Loading advanced settings…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                ContentUnavailableView {
                    Label("Advanced settings unavailable", systemImage: "xmark.octagon")
                } description: {
                    Text(message)
                } actions: {
                    Button("Retry") { Task { await advanced.load() } }
                }
            case .loaded:
                form
            }
        }
        .toolbar {
            ToolbarItem {
                Button {
                    Task { await advanced.reload() }
                } label: {
                    Label("Reload Advanced", systemImage: "arrow.clockwise")
                }
                .help("Reload advanced provider settings")
                .disabled(advanced.isMutating)
            }
        }
        .sheet(item: $advanced.pendingConfirmation) { action in
            ConfirmationSheetView(
                action: action,
                onCancel: { advanced.cancelPendingAction() },
                onConfirm: { Task { await advanced.confirmPendingAction() } }
            )
        }
        .task { await advanced.load() }
        .navigationTitle("Advanced")
    }

    private var form: some View {
        @Bindable var advanced = advanced
        return Form {
            if let error = advanced.mutationError {
                Section {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                        .font(.callout)
                }
            }
            if let message = advanced.resultMessage {
                Section {
                    Label(message, systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .font(.callout)
                }
            }

            strategySection
            orderSection
            codexAccountSection
            surfaceStatsSection
            surfaceDemotionSection
            surfacePatternsSection
            providerOverridesSection
            rawCommandsSection
        }
        .formStyle(.grouped)
        .disabled(advanced.isMutating)
    }

    @ViewBuilder
    private var strategySection: some View {
        @Bindable var advanced = advanced
        Section {
            if let strategy = advanced.strategy {
                LabeledContent("Mode") {
                    Label(
                        strategy.enabled ? "Burst ladder" : "Patient adaptive ladder",
                        systemImage: strategy.enabled ? "bolt.horizontal.circle" : "clock.arrow.circlepath"
                    )
                }
                LabeledContent("Auto-demotion", value: strategy.autoDemotion.summary)
                LabeledContent("Effective order", value: strategy.effectiveKickingOrderSummary)
                LabeledContent("Applies to", value: strategy.appliesTo)
                Text(advanced.strategyBehavior)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                HStack {
                    // Enabling burst confirms (it can spend quota faster);
                    // returning to the patient default saves directly.
                    Button(strategy.enabled ? "Use patient ladder" : "Enable burst ladder") {
                        if strategy.enabled {
                            Task { await advanced.usePatientLadder() }
                        } else {
                            advanced.requestEnableBurstLadder()
                        }
                    }
                    Button("Save gap") {
                        Task { await advanced.saveStrategyGap() }
                    }
                    Stepper(value: $advanced.strategyGapSeconds, in: 0...900, step: 15) {
                        Text("Gap \(advanced.strategyGapSeconds) s")
                            .font(.caption.monospacedDigit())
                    }
                    .frame(maxWidth: 180)
                }
                .controlSize(.small)
            } else {
                Text("No Codex strategy payload available.")
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("Codex strategy")
        } footer: {
            Text("Advanced Codex strategy affects automatic and scheduled Codex kicks. Manual account configuration stays in Accounts.")
        }
    }

    private var orderSection: some View {
        @Bindable var advanced = advanced
        return Section("Surface order") {
            if let strategy = advanced.strategy {
                LabeledContent("Default", value: strategy.defaultOrder.joined(separator: ", "))
                TextField("Surface order", text: $advanced.strategyOrderText)
                    .textFieldStyle(.roundedBorder)
                    .font(.callout.monospaced())
                HStack {
                    Button("Save order") { Task { await advanced.saveStrategyOrder() } }
                    Button("Reset to default") { Task { await advanced.resetStrategyOrder() } }
                }
                .controlSize(.small)
                if !strategy.effectiveKickingOrderErrors.isEmpty {
                    ForEach(strategy.effectiveKickingOrderErrors.keys.sorted(), id: \.self) { label in
                        Label {
                            Text("\(label): \(strategy.effectiveKickingOrderErrors[label] ?? "")")
                        } icon: {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange)
                        }
                        .font(.caption)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var codexAccountSection: some View {
        @Bindable var advanced = advanced
        Section("Codex account") {
            if advanced.codexAccounts.isEmpty {
                Text("No Codex accounts are configured.")
                    .foregroundStyle(.secondary)
            } else {
                Picker("Account", selection: Binding(
                    get: { advanced.selectedCodexLabel ?? "" },
                    set: { label in Task { await advanced.selectCodexAccount(label) } }
                )) {
                    ForEach(advanced.codexAccounts) { account in
                        Text(account.label).tag(account.label)
                    }
                }
                .pickerStyle(.menu)
                if advanced.isLoadingDetails {
                    ProgressView("Loading Codex surface details…")
                        .controlSize(.small)
                }
                if let error = advanced.detailError {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.orange)
                        .font(.caption)
                }
            }
        }
    }

    @ViewBuilder
    private var surfaceStatsSection: some View {
        Section("Surface stats") {
            if let surfaces = advanced.surfaces {
                LabeledContent("Current order", value: surfaces.order.joined(separator: ", "))
                Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 4) {
                    GridRow {
                        Text("Surface").font(.caption.weight(.semibold))
                        Text("State").font(.caption.weight(.semibold))
                        Text("Score").font(.caption.weight(.semibold))
                        Text("Confirmed").font(.caption.weight(.semibold))
                        Text("Issues").font(.caption.weight(.semibold))
                    }
                    Divider().gridCellColumns(5)
                    ForEach(surfaces.surfaces) { surface in
                        GridRow {
                            Text(surface.surface).font(.caption.monospaced())
                            Text(surface.state).font(.caption)
                            Text(String(format: "%.2f", surface.score))
                                .font(.caption.monospacedDigit())
                            Text("\(surface.confirmed)/\(surface.attempts)")
                                .font(.caption.monospacedDigit())
                            Text("no output \(surface.noGeneration) · failed \(surface.failures)")
                                .font(.caption.monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } else {
                Text("Select a Codex account to inspect learned surface stats.")
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var surfaceDemotionSection: some View {
        @Bindable var advanced = advanced
        Section {
            if let surfaces = advanced.surfaces {
                LabeledContent("Auto-demotion", value: surfaces.demotion.enabled ? "enabled" : "disabled")
                LabeledContent("Strong clusters", value: "\(surfaces.demotion.strongClusterCount)")
                LabeledContent("Rule") {
                    Text("after \(surfaces.demotion.afterStrongClusters), keep ≥\(surfaces.demotion.minActiveSurfaces), anchor ≥\(Int((surfaces.demotion.minKeptAnchorRate * 100).rounded())) %")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                HStack {
                    Button(surfaces.demotion.enabled ? "Disable auto-demotion" : "Enable auto-demotion") {
                        Task { await advanced.setSelectedDemotionEnabled(!surfaces.demotion.enabled) }
                    }
                    Button("Reset evidence") {
                        advanced.requestResetDemotionEvidence()
                    }
                }
                .controlSize(.small)

                TextField("Force-keep surfaces", text: $advanced.forceKeepText)
                    .textFieldStyle(.roundedBorder)
                    .font(.callout.monospaced())
                TextField("Force-prune surfaces", text: $advanced.forcePruneText)
                    .textFieldStyle(.roundedBorder)
                    .font(.callout.monospaced())
                HStack {
                    Button("Save force-keep") { Task { await advanced.saveForceKeep() } }
                    Button("Save force-prune") { advanced.requestSaveForcePrune() }
                    Button("Clear overrides") { Task { await advanced.clearOverrides() } }
                }
                .controlSize(.small)
                Divider()
                HStack {
                    Button("Reset learned stats") { advanced.requestResetSurfaceStats() }
                    Button("Reset stats and evidence") { advanced.requestResetStatsAndEvidence() }
                }
                .controlSize(.small)
            } else {
                Text("Demotion controls load with the selected Codex account.")
                    .foregroundStyle(.secondary)
            }
        } header: {
            Text("Surface demotion")
        } footer: {
            Text("Force-prune and reset actions are advanced recovery tools. They do not delete kick history.")
        }
    }

    @ViewBuilder
    private var surfacePatternsSection: some View {
        Section("Surface patterns") {
            if let patterns = advanced.patterns {
                LabeledContent("Scope", value: patterns.scopeLabel ?? "all Codex accounts")
                LabeledContent("Eligible clusters", value: "\(patterns.eligibleClusters)")
                LabeledContent("Evaluated samples", value: "\(patterns.evaluatedSamples)")
                if let verdict = patterns.verdict.message {
                    Text(verdict)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if !patterns.candidates.isEmpty {
                    Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 4) {
                        GridRow {
                            Text("Predictor").font(.caption.weight(.semibold))
                            Text("Top-1").font(.caption.weight(.semibold))
                            Text("Top-2").font(.caption.weight(.semibold))
                        }
                        ForEach(patterns.candidates.keys.sorted(), id: \.self) { key in
                            if let metrics = patterns.candidates[key] {
                                GridRow {
                                    Text(key).font(.caption)
                                    Text(rate(metrics.top1Rate)).font(.caption.monospacedDigit())
                                    Text(rate(metrics.top2Rate)).font(.caption.monospacedDigit())
                                }
                            }
                        }
                    }
                }
            } else {
                Text("Pattern analysis is read-only and appears after surface details load.")
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private var providerOverridesSection: some View {
        @Bindable var advanced = advanced
        Section {
            if advanced.providerAccounts.isEmpty {
                Text("No kickable provider accounts are configured.")
                    .foregroundStyle(.secondary)
            } else {
                Picker("Account", selection: Binding(
                    get: { advanced.selectedProviderLabel ?? "" },
                    set: { label in advanced.selectProviderAccount(label) }
                )) {
                    ForEach(advanced.providerAccounts) { account in
                        Text("\(account.label) (\(account.provider))").tag(account.label)
                    }
                }
                .pickerStyle(.menu)
                if let account = advanced.selectedProviderAccount {
                    LabeledContent("Current kick model", value: account.kickModel)
                    TextField("Kick model override", text: $advanced.kickModelText)
                        .textFieldStyle(.roundedBorder)
                        .font(.callout.monospaced())
                    HStack {
                        Button("Save model") { Task { await advanced.saveKickModel() } }
                        Button("Clear model") { Task { await advanced.clearKickModel() } }
                    }
                    .controlSize(.small)
                    if account.supportsClaudeInternals {
                        Divider()
                        LabeledContent("Status probe", value: account.statusProbeEnabled ? "enabled" : "disabled")
                        if let direct = account.directUsageEnabled {
                            LabeledContent("Direct /usage", value: direct ? "enabled" : "disabled")
                        }
                        HStack {
                            // Enabling the probe confirms (it can spend
                            // quota); disabling saves directly.
                            Button(account.statusProbeEnabled ? "Disable status probe" : "Enable status probe") {
                                if account.statusProbeEnabled {
                                    Task { await advanced.disableStatusProbe() }
                                } else {
                                    advanced.requestEnableStatusProbe()
                                }
                            }
                            if let direct = account.directUsageEnabled {
                                Button(direct ? "Disable direct /usage" : "Enable direct /usage") {
                                    Task { await advanced.setDirectUsage(!direct) }
                                }
                            }
                        }
                        .controlSize(.small)
                    }
                }
            }
        } header: {
            Text("Provider overrides")
        } footer: {
            Text("Kick model and Claude probe settings are provider-internal controls. Leave them unchanged unless you are tuning or diagnosing TokenKick behavior.")
        }
    }

    private var rawCommandsSection: some View {
        Section {
            commandRow("Strategy JSON", command: "tk codex-strategy status --json-output")
            if let label = advanced.selectedCodexLabel {
                commandRow("Surface stats", command: "tk codex-surfaces \"\(label)\" --json-output")
                commandRow("Surface patterns", command: "tk codex-surface-patterns \"\(label)\" --json-output")
            }
        } header: {
            Text("Equivalent commands")
        } footer: {
            Text("The same data is available from a terminal — useful for bug reports.")
        }
    }

    private func commandRow(_ title: String, command: String) -> some View {
        HStack {
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.callout)
                Text(command)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
            }
            Spacer()
            Button {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(command, forType: .string)
            } label: {
                Image(systemName: "doc.on.doc")
            }
            .buttonStyle(.borderless)
            .help("Copy command")
        }
    }

    private func rate(_ value: Double?) -> String {
        guard let value else { return "—" }
        return "\(String(format: "%.1f", value * 100)) %"
    }
}
