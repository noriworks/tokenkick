import Foundation
import Observation
import TokenKickKit

public protocol AdvancedServicing: Sendable {
    func accountsList() async throws -> TKEnvelope<TKAccountsListPayload>
    func codexStrategyStatus() async throws -> TKCodexStrategyPayload
    func codexSurfaces(label: String) async throws -> TKCodexSurfacesPayload
    func codexSurfacePatterns(label: String?) async throws -> TKCodexSurfacePatternsPayload
    func runMutation(arguments: [String]) async throws -> TKEnvelope<TKJSONValue>
}

public struct LiveAdvancedService: AdvancedServicing {
    public let timeout: TimeInterval

    public init(timeout: TimeInterval = 60) {
        self.timeout = timeout
    }

    public func accountsList() async throws -> TKEnvelope<TKAccountsListPayload> {
        try await LiveTKClient.make(timeout: timeout).accountsList()
    }

    public func codexStrategyStatus() async throws -> TKCodexStrategyPayload {
        try await LiveTKClient.make(timeout: timeout).codexStrategyStatus()
    }

    public func codexSurfaces(label: String) async throws -> TKCodexSurfacesPayload {
        try await LiveTKClient.make(timeout: timeout).codexSurfaces(label: label)
    }

    public func codexSurfacePatterns(label: String?) async throws -> TKCodexSurfacePatternsPayload {
        try await LiveTKClient.make(timeout: timeout).codexSurfacePatterns(label: label)
    }

    public func runMutation(arguments: [String]) async throws -> TKEnvelope<TKJSONValue> {
        try await LiveTKClient.make(timeout: timeout).envelope(TKJSONValue.self, arguments: arguments)
    }
}

public struct AdvancedProviderAccountRow: Identifiable, Equatable, Sendable {
    public let id: String
    public let label: String
    public let provider: String
    public let kickable: Bool
    public let kickModel: String
    public let statusProbeEnabled: Bool
    public let directUsageEnabled: Bool?

    public var supportsClaudeInternals: Bool { provider == "claude" }

    init(account: TKAccountsListPayload.Account) {
        self.id = account.label
        self.label = account.label
        self.provider = account.provider
        self.kickable = account.kickable
        self.kickModel = account.kickModel
        self.statusProbeEnabled = account.statusProbeEnabled
        self.directUsageEnabled = account.directUsageEnabled
    }
}

@MainActor
@Observable
public final class AdvancedViewModel {
    public enum Phase: Equatable, Sendable {
        case idle
        case loading
        case loaded
        case failed(message: String)
    }

    public private(set) var phase: Phase = .idle
    public private(set) var strategy: TKCodexStrategyPayload?
    public private(set) var codexAccounts: [AdvancedProviderAccountRow] = []
    public private(set) var providerAccounts: [AdvancedProviderAccountRow] = []
    public private(set) var surfaces: TKCodexSurfacesPayload?
    public private(set) var patterns: TKCodexSurfacePatternsPayload?
    public private(set) var detailError: String?
    public private(set) var mutationError: String?
    public private(set) var resultMessage: String?
    public private(set) var isLoadingDetails = false
    public private(set) var isMutating = false

    public var selectedCodexLabel: String?
    public var selectedProviderLabel: String?
    public var strategyOrderText = ""
    public var strategyGapSeconds = 90
    public var forceKeepText = ""
    public var forcePruneText = ""
    public var kickModelText = ""
    public var pendingConfirmation: ConfirmedAction?

    private let service: any AdvancedServicing
    private let onMutation: @MainActor () async -> Void
    private var pendingMutationArguments: [String]?

    public init(
        service: any AdvancedServicing,
        onMutation: @escaping @MainActor () async -> Void
    ) {
        self.service = service
        self.onMutation = onMutation
    }

    public var selectedCodexAccount: AdvancedProviderAccountRow? {
        guard let selectedCodexLabel else { return nil }
        return codexAccounts.first { $0.label == selectedCodexLabel }
    }

    public var selectedProviderAccount: AdvancedProviderAccountRow? {
        guard let selectedProviderLabel else { return nil }
        return providerAccounts.first { $0.label == selectedProviderLabel }
    }

    public var strategyBehavior: String {
        guard let strategy else { return "" }
        return strategy.enabled ? strategy.enabledBehavior : strategy.disabledBehavior
    }

    public func load() async {
        phase = .loading
        mutationError = nil
        detailError = nil
        do {
            async let strategyValue = service.codexStrategyStatus()
            async let accountsEnvelope = service.accountsList()
            let (strategy, accounts) = try await (strategyValue, accountsEnvelope)
            guard accounts.ok, let accountPayload = accounts.payload else {
                throw NSError(
                    domain: "TokenKickAdvanced",
                    code: 1,
                    userInfo: [NSLocalizedDescriptionKey: accounts.message ?? "Could not load accounts."]
                )
            }
            self.strategy = strategy
            self.providerAccounts = accountPayload.accounts
                .filter(\.kickable)
                .map(AdvancedProviderAccountRow.init(account:))
            self.codexAccounts = providerAccounts.filter { $0.provider == "codex" }
            if selectedCodexLabel == nil || !codexAccounts.contains(where: { $0.label == selectedCodexLabel }) {
                selectedCodexLabel = codexAccounts.first?.label
            }
            if selectedProviderLabel == nil || !providerAccounts.contains(where: { $0.label == selectedProviderLabel }) {
                selectedProviderLabel = providerAccounts.first?.label
            }
            syncStrategyEditor()
            syncProviderEditor()
            phase = .loaded
            await loadSelectedCodexDetails()
        } catch {
            phase = .failed(message: String(describing: error))
        }
    }

    public func reload() async {
        await load()
    }

    public func selectCodexAccount(_ label: String) async {
        guard selectedCodexLabel != label else { return }
        selectedCodexLabel = label
        await loadSelectedCodexDetails()
    }

    public func selectProviderAccount(_ label: String) {
        selectedProviderLabel = label
        syncProviderEditor()
    }

    public func loadSelectedCodexDetails() async {
        guard let selectedCodexLabel else {
            surfaces = nil
            patterns = nil
            return
        }
        isLoadingDetails = true
        detailError = nil
        do {
            async let surfacePayload = service.codexSurfaces(label: selectedCodexLabel)
            async let patternPayload = service.codexSurfacePatterns(label: selectedCodexLabel)
            let (surfaces, patterns) = try await (surfacePayload, patternPayload)
            self.surfaces = surfaces
            self.patterns = patterns
            forceKeepText = surfaces.demotion.forceKeep.joined(separator: ", ")
            forcePruneText = surfaces.demotion.forcePrune.joined(separator: ", ")
        } catch {
            surfaces = nil
            patterns = nil
            detailError = String(describing: error)
        }
        isLoadingDetails = false
    }

    /// Burst mode can spend quota faster, so enabling it confirms;
    /// returning to the patient default is a plain save.
    public func requestEnableBurstLadder() {
        requestMutation(
            id: "codex-strategy:enable",
            title: "Enable Codex burst ladder?",
            explanation: "Auto and scheduled Codex kicks will try the configured surface sequence.",
            disclosures: ["Applies to auto and scheduled Codex kicks only."],
            scopeLabel: "Codex strategy",
            verb: "Enable Strategy",
            isDestructive: false,
            arguments: ["codex-strategy", "enable", "--json-output"]
        )
    }

    public func usePatientLadder() async {
        await performMutation(arguments: ["codex-strategy", "disable", "--json-output"])
    }

    public func saveStrategyGap() async {
        await performMutation(
            arguments: ["codex-strategy", "gap", String(max(0, strategyGapSeconds)), "--json-output"]
        )
    }

    public func saveStrategyOrder() async {
        let surfaces = Self.parseSurfaceList(strategyOrderText)
        guard !surfaces.isEmpty else {
            mutationError = "Choose at least one Codex surface."
            return
        }
        await performMutation(arguments: ["codex-strategy", "order"] + surfaces + ["--json-output"])
    }

    public func resetStrategyOrder() async {
        await performMutation(arguments: ["codex-strategy", "order", "--reset", "--json-output"])
    }

    public func setSelectedDemotionEnabled(_ enabled: Bool) async {
        guard let label = selectedCodexLabel else { return }
        await performMutation(
            arguments: ["codex-strategy", "demotion", enabled ? "enable" : "disable", label, "--json-output"]
        )
    }

    public func saveForceKeep() async {
        guard let label = selectedCodexLabel else { return }
        let surfaces = Self.parseSurfaceList(forceKeepText)
        guard !surfaces.isEmpty else {
            mutationError = "Choose at least one surface to force-keep, or clear overrides."
            return
        }
        await performMutation(
            arguments: ["codex-strategy", "demotion", "force-keep", label] + surfaces + ["--json-output"]
        )
    }

    public func requestSaveForcePrune() {
        guard let label = selectedCodexLabel else { return }
        let surfaces = Self.parseSurfaceList(forcePruneText)
        guard !surfaces.isEmpty else {
            mutationError = "Choose at least one surface to force-prune, or clear overrides."
            return
        }
        requestMutation(
            id: "codex-force-prune:\(label)",
            title: "Save force-prune override?",
            explanation: "Force-pruned surfaces are removed from the Codex kicking order for this account.",
            disclosures: [
                "Force-pruned surfaces are not automatically reintroduced after a miss.",
                "Leaving too few surfaces can reduce recovery options.",
            ],
            scopeLabel: label,
            verb: "Save Force-Prune",
            isDestructive: true,
            arguments: ["codex-strategy", "demotion", "force-prune", label] + surfaces + ["--json-output"]
        )
    }

    public func clearOverrides() async {
        guard let label = selectedCodexLabel else { return }
        await performMutation(
            arguments: ["codex-strategy", "demotion", "clear-overrides", label, "--json-output"]
        )
    }

    public func requestResetDemotionEvidence() {
        guard let label = selectedCodexLabel else { return }
        requestMutation(
            id: "codex-reset-evidence:\(label)",
            title: "Reset demotion evidence?",
            explanation: "Clears the evidence TokenKick uses for Codex surface auto-demotion.",
            disclosures: ["Kick history, learned surface scores, and force overrides are unchanged."],
            scopeLabel: label,
            verb: "Reset Evidence",
            isDestructive: true,
            arguments: ["codex-strategy", "demotion", "reset-evidence", label, "--json-output"]
        )
    }

    public func requestResetSurfaceStats() {
        guard let label = selectedCodexLabel else { return }
        requestMutation(
            id: "codex-reset-stats:\(label)",
            title: "Reset learned surface stats?",
            explanation: "Clears learned Codex surface scores and order for this account.",
            disclosures: ["Kick history, demotion settings, force overrides, and demotion evidence are unchanged."],
            scopeLabel: label,
            verb: "Reset Stats",
            isDestructive: true,
            arguments: ["codex-surfaces", label, "reset-stats", "--json-output"]
        )
    }

    public func requestResetStatsAndEvidence() {
        guard let label = selectedCodexLabel else { return }
        requestMutation(
            id: "codex-reset-all:\(label)",
            title: "Reset stats and demotion evidence?",
            explanation: "Clears learned surface scores and demotion evidence for this account.",
            disclosures: ["Kick history and force overrides are unchanged."],
            scopeLabel: label,
            verb: "Reset Stats & Evidence",
            isDestructive: true,
            arguments: ["codex-surfaces", label, "reset-all", "--json-output"]
        )
    }

    public func saveKickModel() async {
        guard let label = selectedProviderLabel else { return }
        let model = kickModelText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !model.isEmpty else {
            mutationError = "Kick model cannot be empty. Use Clear model to return to the provider default."
            return
        }
        await performMutation(arguments: ["accounts", "set-kick-model", label, model, "--json-output"])
    }

    public func clearKickModel() async {
        guard let label = selectedProviderLabel else { return }
        await performMutation(arguments: ["accounts", "clear-kick-model", label, "--json-output"])
    }

    /// Enabling the probe can spend Claude quota, so it confirms;
    /// disabling is a plain save.
    public func requestEnableStatusProbe() {
        guard let label = selectedProviderLabel else { return }
        requestMutation(
            id: "status-probe:\(label):enable",
            title: "Enable Claude status probe?",
            explanation: "Changes whether routine status refresh can use Claude's explicit /usage probe.",
            disclosures: ["The explicit probe can consume a tiny amount of Claude quota."],
            scopeLabel: label,
            verb: "Enable Probe",
            isDestructive: true,
            arguments: ["accounts", "enable-probe", label, "--json-output"]
        )
    }

    public func disableStatusProbe() async {
        guard let label = selectedProviderLabel else { return }
        await performMutation(arguments: ["accounts", "disable-probe", label, "--json-output"])
    }

    public func setDirectUsage(_ enabled: Bool) async {
        guard let label = selectedProviderLabel else { return }
        await performMutation(
            arguments: ["accounts", "set-direct-usage", label, enabled ? "--enable" : "--disable", "--json-output"]
        )
    }

    public func cancelPendingAction() {
        pendingConfirmation = nil
        pendingMutationArguments = nil
    }

    public func confirmPendingAction() async {
        guard let arguments = pendingMutationArguments else { return }
        pendingConfirmation = nil
        pendingMutationArguments = nil
        await performMutation(arguments: arguments)
    }

    /// Shared executor for plain saves and confirmed actions alike: run the
    /// mutation, surface the envelope verdict inline, refresh, reload.
    private func performMutation(arguments: [String]) async {
        guard !isMutating else { return }
        isMutating = true
        mutationError = nil
        resultMessage = nil
        do {
            let result = try await service.runMutation(arguments: arguments)
            if result.ok {
                resultMessage = result.message ?? "Advanced setting saved."
                await onMutation()
                await load()
            } else {
                mutationError = result.message ?? "The advanced setting was rejected."
                await onMutation()
            }
        } catch {
            mutationError = String(describing: error)
            await onMutation()
        }
        isMutating = false
    }

    private func requestMutation(
        id: String,
        title: String,
        explanation: String,
        disclosures: [String],
        scopeLabel: String,
        verb: String,
        isDestructive: Bool,
        arguments: [String]
    ) {
        mutationError = nil
        pendingMutationArguments = arguments
        pendingConfirmation = ConfirmedAction(
            id: id,
            title: title,
            explanation: explanation,
            costLine: nil,
            disclosures: disclosures,
            scopeLabel: scopeLabel,
            verb: verb,
            isDestructive: isDestructive,
            tkArguments: arguments
        )
    }

    private func syncStrategyEditor() {
        guard let strategy else { return }
        strategyOrderText = (strategy.configuredOrder.isEmpty ? strategy.activeOrder : strategy.configuredOrder)
            .joined(separator: ", ")
        strategyGapSeconds = max(0, Int(strategy.configuredGapSeconds))
    }

    private func syncProviderEditor() {
        guard let selectedProviderAccount else {
            kickModelText = ""
            return
        }
        kickModelText = selectedProviderAccount.kickModel == "default" ? "" : selectedProviderAccount.kickModel
    }

    public static func parseSurfaceList(_ text: String) -> [String] {
        text
            .split { character in
                character == "," || character == " " || character == "\n" || character == "\t"
            }
            .map { String($0).trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
    }
}
