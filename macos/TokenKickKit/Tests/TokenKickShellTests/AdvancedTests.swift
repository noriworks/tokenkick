import XCTest
import TokenKickKit
@testable import TokenKickShell

private final class StubAdvancedService: AdvancedServicing, @unchecked Sendable {
    var strategyResult: Result<TKCodexStrategyPayload, Error>
    var accountsResult: Result<TKEnvelope<TKAccountsListPayload>, Error>
    var surfacesResult: Result<TKCodexSurfacesPayload, Error>
    var patternsResult: Result<TKCodexSurfacePatternsPayload, Error>
    var mutationResult: Result<TKEnvelope<TKJSONValue>, Error>
    private(set) var mutationArguments: [[String]] = []
    private(set) var strategyLoads = 0
    private(set) var accountLoads = 0

    init(
        strategy: TKCodexStrategyPayload = StubAdvancedService.strategyPayload(),
        accounts: TKEnvelope<TKAccountsListPayload> = StubAdvancedService.accountsEnvelope(),
        surfaces: TKCodexSurfacesPayload = StubAdvancedService.surfacesPayload(),
        patterns: TKCodexSurfacePatternsPayload = StubAdvancedService.patternsPayload(),
        mutation: TKEnvelope<TKJSONValue> = try! StubAccountConfigurator.envelope(
            json: ["action": "saved"],
            message: "Advanced setting saved."
        )
    ) {
        self.strategyResult = .success(strategy)
        self.accountsResult = .success(accounts)
        self.surfacesResult = .success(surfaces)
        self.patternsResult = .success(patterns)
        self.mutationResult = .success(mutation)
    }

    func accountsList() async throws -> TKEnvelope<TKAccountsListPayload> {
        accountLoads += 1
        let result = accountsResult
        return try result.get()
    }

    func codexStrategyStatus() async throws -> TKCodexStrategyPayload {
        strategyLoads += 1
        let result = strategyResult
        return try result.get()
    }

    func codexSurfaces(label: String) async throws -> TKCodexSurfacesPayload {
        try surfacesResult.get()
    }

    func codexSurfacePatterns(label: String?) async throws -> TKCodexSurfacePatternsPayload {
        try patternsResult.get()
    }

    func runMutation(arguments: [String]) async throws -> TKEnvelope<TKJSONValue> {
        mutationArguments.append(arguments)
        let result = mutationResult
        return try result.get()
    }

    static func strategyPayload() -> TKCodexStrategyPayload {
        decodeBare(
            [
                "schema_version": 1,
                "strategy": "patient_adaptive_ladder",
                "enabled": false,
                "config_enabled": false,
                "active_order": ["legacy", "repo-skip", "repo", "interactive-like"],
                "effective_kicking_order": ["legacy", "repo-skip", "repo", "interactive-like"],
                "effective_kicking_order_summary": "legacy, repo-skip, repo, interactive-like",
                "effective_kicking_order_by_account": [
                    "codex (a)": ["legacy", "repo-skip", "repo", "interactive-like"]
                ],
                "effective_kicking_order_errors": [:],
                "configured_order": [],
                "default_order": ["legacy", "repo-skip", "repo", "interactive-like"],
                "active_gap_seconds": 90.0,
                "configured_gap_seconds": 90,
                "auto_demotion": [
                    "state": "mixed",
                    "summary": "mixed (1 on, 1 off)",
                    "enabled_count": 1,
                    "disabled_count": 1,
                    "total_codex_accounts": 2,
                    "enabled_labels": ["codex (a)"],
                    "disabled_labels": ["codex (b)"],
                ],
                "applies_to": "auto/scheduled Codex kicks only",
                "enabled_behavior": "Burst ladder fires the configured set at the gap with no early-stop.",
                "disabled_behavior": "Patient adaptive ladder uses verified retries and the 900s retry backoff.",
            ],
            as: TKCodexStrategyPayload.self
        )
    }

    static func accountsEnvelope(codexAccounts: [[String: Any]]? = nil) -> TKEnvelope<TKAccountsListPayload> {
        let accounts = codexAccounts ?? [
            account(label: "codex (a)", provider: "codex"),
            account(label: "claude (a)", provider: "claude", directUsage: true),
        ]
        return try! StubAccountConfigurator.envelope(
            json: ["accounts": accounts],
            as: TKAccountsListPayload.self
        )
    }

    static func account(
        label: String,
        provider: String,
        directUsage: Bool? = nil
    ) -> [String: Any] {
        [
            "label": label,
            "provider": provider,
            "visible": true,
            "kickable": true,
            "monitor_only": false,
            "auto_kick": true,
            "weekly_auto_kick": true,
            "session_auto_kick": true,
            "notifications_enabled": true,
            "notifications_route": "global default",
            "kick_model": "default",
            "status_probe_enabled": false,
            "direct_usage_enabled": directUsage ?? NSNull(),
        ]
    }

    static func surfacesPayload() -> TKCodexSurfacesPayload {
        decodeBare(
            [
                "schema_version": 1,
                "read_only": true,
                "label": "codex (a)",
                "account_key": "manual|codex|a",
                "provider_home": "/Users/fixture/.codex",
                "order": ["repo", "legacy"],
                "demotion": [
                    "enabled": true,
                    "after_strong_clusters": 5,
                    "min_active_surfaces": 2,
                    "min_kept_anchor_rate": 0.95,
                    "measurement_clusters": 20,
                    "rescue_cooldown_strong_clusters": 20,
                    "force_keep": ["repo"],
                    "force_prune": ["interactive-like"],
                    "strong_cluster_count": 3,
                    "demoted": [:],
                    "rescues": [:],
                    "last_reintroduction": NSNull(),
                ],
                "surfaces": [
                    surface("repo", rank: 1, state: "force-kept", score: 3.0),
                    surface("legacy", rank: 2, state: "active", score: 1.0),
                ],
            ],
            as: TKCodexSurfacesPayload.self
        )
    }

    static func surface(_ name: String, rank: Int?, state: String, score: Double) -> [String: Any] {
        [
            "surface": name,
            "rank": rank ?? NSNull(),
            "state": state,
            "demotion_reason": NSNull(),
            "rescue_cooldown_remaining_strong_clusters": NSNull(),
            "score": score,
            "attempts": 2,
            "confirmed": 1,
            "timing_matches": 0,
            "external_possible": 0,
            "no_generation": 0,
            "failures": 0,
            "last_attempt_at": NSNull(),
            "last_confirmed_at": NSNull(),
        ]
    }

    static func patternsPayload() -> TKCodexSurfacePatternsPayload {
        decodeBare(
            [
                "scope_label": "codex (a)",
                "eligible_clusters": 4,
                "evaluated_samples": 3,
                "baseline": [
                    "samples": 3,
                    "top1_hits": 1,
                    "top2_hits": 2,
                    "top1_rate": 0.333,
                    "top2_rate": 0.667,
                ],
                "candidates": [
                    "previous_same_account_surface": [
                        "samples": 3,
                        "top1_hits": 2,
                        "top2_hits": 2,
                        "top1_rate": 0.667,
                        "top2_rate": 0.667,
                        "top1_lift_hits": 1,
                        "top2_lift_hits": 0,
                        "top1_lift_rate": 0.334,
                        "top2_lift_rate": 0.0,
                    ]
                ],
                "ignored": ["missing_surface": 1],
                "verdict": ["message": "No stable sequence pattern detected."],
                "sequence_hints": [],
            ],
            as: TKCodexSurfacePatternsPayload.self
        )
    }

    static func decodeBare<Payload: Decodable & Sendable>(_ object: Any, as type: Payload.Type) -> Payload {
        let data = try! JSONSerialization.data(withJSONObject: object)
        return try! JSONDecoder().decode(Payload.self, from: data)
    }
}

@MainActor
final class AdvancedViewModelTests: XCTestCase {
    private var refreshCount = 0

    private func makeModel(service: StubAdvancedService = StubAdvancedService()) -> AdvancedViewModel {
        refreshCount = 0
        return AdvancedViewModel(service: service) { [weak self] in
            self?.refreshCount += 1
        }
    }

    func testLoadSuccessBuildsStrategyAndAccountState() async {
        let service = StubAdvancedService()
        let model = makeModel(service: service)

        await model.load()

        XCTAssertEqual(model.phase, .loaded)
        XCTAssertEqual(model.strategy?.effectiveKickingOrderSummary, "legacy, repo-skip, repo, interactive-like")
        XCTAssertEqual(model.codexAccounts.map(\.label), ["codex (a)"])
        XCTAssertEqual(model.providerAccounts.map(\.label), ["codex (a)", "claude (a)"])
        XCTAssertEqual(model.selectedCodexLabel, "codex (a)")
        XCTAssertEqual(model.surfaces?.demotion.forceKeep, ["repo"])
        XCTAssertEqual(model.forcePruneText, "interactive-like")
    }

    func testLoadEmptyCodexAccountsStillShowsProviderOverrides() async {
        let service = StubAdvancedService(
            accounts: StubAdvancedService.accountsEnvelope(
                codexAccounts: [StubAdvancedService.account(label: "claude (a)", provider: "claude", directUsage: true)]
            )
        )
        let model = makeModel(service: service)

        await model.load()

        XCTAssertEqual(model.phase, .loaded)
        XCTAssertTrue(model.codexAccounts.isEmpty)
        XCTAssertEqual(model.selectedProviderLabel, "claude (a)")
        XCTAssertNil(model.surfaces)
    }

    func testLoadFailureSurfacesError() async {
        let service = StubAdvancedService()
        service.strategyResult = .failure(StubError(description: "strategy unavailable"))
        let model = makeModel(service: service)

        await model.load()

        guard case .failed(let message) = model.phase else {
            return XCTFail("expected failed phase")
        }
        XCTAssertTrue(message.contains("strategy unavailable"))
    }

    func testStrategyMutationConfirmsRunsRefreshesAndReloads() async {
        let service = StubAdvancedService()
        let model = makeModel(service: service)
        await model.load()

        model.requestEnableBurstLadder()
        let action = try! XCTUnwrap(model.pendingConfirmation)
        XCTAssertEqual(action.verb, "Enable Strategy")
        XCTAssertFalse(action.isDestructive)
        XCTAssertEqual(action.disclosures, ["Applies to auto and scheduled Codex kicks only."])

        await model.confirmPendingAction()

        XCTAssertEqual(service.mutationArguments.last, ["codex-strategy", "enable", "--json-output"])
        XCTAssertEqual(refreshCount, 1)
        XCTAssertEqual(model.resultMessage, "Advanced setting saved.")
        XCTAssertEqual(model.phase, .loaded)
    }

    func testPlainSavesRunDirectlyWithoutConfirmation() async {
        let service = StubAdvancedService()
        let model = makeModel(service: service)
        await model.load()

        model.strategyGapSeconds = 120
        await model.saveStrategyGap()

        XCTAssertNil(model.pendingConfirmation, "plain saves never open the sheet")
        XCTAssertEqual(service.mutationArguments.last, ["codex-strategy", "gap", "120", "--json-output"])
        XCTAssertEqual(model.resultMessage, "Advanced setting saved.")

        await model.usePatientLadder()
        XCTAssertNil(model.pendingConfirmation)
        XCTAssertEqual(service.mutationArguments.last, ["codex-strategy", "disable", "--json-output"])

        model.forceKeepText = ""
        await model.saveForceKeep()
        XCTAssertEqual(
            model.mutationError,
            "Choose at least one surface to force-keep, or clear overrides.",
            "validation still guards direct saves"
        )
    }

    func testForcePruneConfirmationIsDangerousAndUsesSurfaceList() async {
        let service = StubAdvancedService()
        let model = makeModel(service: service)
        await model.load()

        model.forcePruneText = "legacy, repo-skip"
        model.requestSaveForcePrune()

        let action = try! XCTUnwrap(model.pendingConfirmation)
        XCTAssertTrue(action.isDestructive)
        XCTAssertEqual(action.verb, "Save Force-Prune")
        XCTAssertEqual(
            action.tkArguments,
            [
                "codex-strategy", "demotion", "force-prune",
                "codex (a)", "legacy", "repo-skip", "--json-output",
            ]
        )
    }

    func testProviderOverrideAndClaudeProbeArguments() async {
        let service = StubAdvancedService()
        let model = makeModel(service: service)
        await model.load()

        model.selectProviderAccount("claude (a)")
        model.kickModelText = "claude-opus-test"
        await model.saveKickModel()

        XCTAssertNil(model.pendingConfirmation, "kick model is a plain save")
        XCTAssertEqual(
            service.mutationArguments.last,
            ["accounts", "set-kick-model", "claude (a)", "claude-opus-test", "--json-output"]
        )

        model.requestEnableStatusProbe()
        let action = try! XCTUnwrap(model.pendingConfirmation)
        XCTAssertTrue(action.isDestructive, "probe enable can spend quota, so it confirms")
        XCTAssertEqual(action.tkArguments, ["accounts", "enable-probe", "claude (a)", "--json-output"])
        model.cancelPendingAction()

        await model.disableStatusProbe()
        XCTAssertNil(model.pendingConfirmation, "probe disable is a plain save")
        XCTAssertEqual(
            service.mutationArguments.last,
            ["accounts", "disable-probe", "claude (a)", "--json-output"]
        )
    }

    func testMutationFailureDoesNotReloadAsSuccess() async throws {
        let service = StubAdvancedService()
        service.mutationResult = .success(
            try StubAccountConfigurator.envelope(
                json: NSNull(),
                ok: false,
                errorCode: "mutation_rejected",
                message: "Cannot force-prune every Codex surface."
            )
        )
        let model = makeModel(service: service)
        await model.load()
        let loadsBefore = service.strategyLoads

        model.forcePruneText = "repo"
        model.requestSaveForcePrune()
        await model.confirmPendingAction()

        XCTAssertEqual(model.mutationError, "Cannot force-prune every Codex surface.")
        XCTAssertEqual(service.strategyLoads, loadsBefore)
        XCTAssertEqual(refreshCount, 1)
    }

    func testParseSurfaceListAcceptsCommasAndWhitespace() {
        XCTAssertEqual(
            AdvancedViewModel.parseSurfaceList("repo, legacy\nrepo-skip"),
            ["repo", "legacy", "repo-skip"]
        )
    }
}
