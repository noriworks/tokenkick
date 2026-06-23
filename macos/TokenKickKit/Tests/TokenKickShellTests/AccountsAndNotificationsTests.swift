import XCTest
import TokenKickKit
@testable import TokenKickShell

final class StubAccountConfigurator: AccountConfiguring, @unchecked Sendable {
    private let lock = NSLock()
    private(set) var mutationArguments: [[String]] = []
    private(set) var listLoads = 0
    var mutationResult: Result<TKEnvelope<TKJSONValue>, Error> = .success(
        try! StubAccountConfigurator.envelope(json: ["account": ["label": "codex (a)"]])
    )
    var listAccounts: [[String: Any]] = [
        StubAccountConfigurator.listAccount(label: "codex (a)", provider: "codex"),
        StubAccountConfigurator.listAccount(label: "gemini (m)", provider: "gemini", monitorOnly: true),
    ]

    static func listAccount(
        label: String,
        provider: String,
        monitorOnly: Bool = false,
        visible: Bool = true,
        autoKick: Bool = false
    ) -> [String: Any] {
        [
            "label": label,
            "provider": provider,
            "visible": visible,
            "kickable": !monitorOnly,
            "monitor_only": monitorOnly,
            "auto_kick": autoKick,
            "weekly_auto_kick": autoKick,
            "session_auto_kick": autoKick,
            "notifications_enabled": true,
            "notifications_route": "global default",
            "kick_model": "default",
            "status_probe_enabled": false,
        ]
    }

    static func envelope<Payload: Decodable & Sendable>(
        json payload: Any,
        ok: Bool = true,
        errorCode: String? = nil,
        message: String? = nil,
        as type: Payload.Type = TKJSONValue.self
    ) throws -> TKEnvelope<Payload> {
        let envelope: [String: Any] = [
            "schema_version": 1,
            "ok": ok,
            "error_code": errorCode ?? NSNull(),
            "message": message ?? NSNull(),
            "warnings": [String](),
            "payload": payload,
        ]
        let data = try JSONSerialization.data(withJSONObject: envelope)
        return try TKJSONDecoding.envelope(type, from: data)
    }

    func accountsList() async throws -> TKEnvelope<TKAccountsListPayload> {
        lock.lock()
        listLoads += 1
        let accounts = listAccounts
        lock.unlock()
        return try Self.envelope(json: ["accounts": accounts], as: TKAccountsListPayload.self)
    }

    func accountsPlanning() async throws -> TKEnvelope<TKAccountsPlanningPayload> {
        let accounts: [[String: Any]] = [
            [
                "label": "codex (a)",
                "provider": "codex",
                "visible": true,
                "auto_kick": false,
                "session_auto_kick": false,
                "usable_session_minutes": 240,
                "orchestration_role": "normal",
                "effective_orchestration_role": "normal",
                "weekly_reserve_threshold_percent": NSNull(),
            ]
        ]
        return try Self.envelope(json: ["accounts": accounts], as: TKAccountsPlanningPayload.self)
    }

    func accountsNotifications() async throws -> TKEnvelope<TKAccountNotificationsPayload> {
        let payload: [String: Any] = [
            "global_enabled": false,
            "destination": "global disabled",
            "backends": ["ntfy"],
            "accounts": [
                [
                    "label": "codex (a)",
                    "provider": "codex",
                    "notifications_enabled": true,
                    "backends": NSNull(),
                    "route": "global default",
                ]
            ],
        ]
        return try Self.envelope(json: payload, as: TKAccountNotificationsPayload.self)
    }

    func runMutation(arguments: [String]) async throws -> TKEnvelope<TKJSONValue> {
        lock.lock()
        mutationArguments.append(arguments)
        let result = mutationResult
        lock.unlock()
        return try result.get()
    }
}

final class AccountMutationArgumentTests: XCTestCase {
    func testMutationArguments() {
        XCTAssertEqual(
            AccountMutation.setVisible(false).arguments(label: "codex (a)"),
            ["accounts", "hide", "codex (a)", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setVisible(true).arguments(label: "codex (a)"),
            ["accounts", "show", "codex (a)", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setAutoKick(true).arguments(label: "codex (a)"),
            ["auto", "enable", "codex (a)", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setSessionAutoKick(false).arguments(label: "codex (a)"),
            ["auto", "session", "disable", "codex (a)", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setWeeklyAutoKick(true).arguments(label: "codex (a)"),
            ["auto", "weekly", "enable", "codex (a)", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setAutoKick(true).arguments(
                label: "codex (a)",
                acceptingRisk: true
            ),
            ["auto", "enable", "codex (a)", "--accept-risk", "ENABLE", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setSessionAutoKick(true).arguments(
                label: "codex (a)",
                acceptingRisk: true
            ),
            [
                "auto", "session", "enable", "codex (a)",
                "--accept-risk", "ENABLE", "--json-output",
            ]
        )
        XCTAssertEqual(
            AccountMutation.setAutoKick(false).arguments(
                label: "codex (a)",
                acceptingRisk: true
            ),
            ["auto", "disable", "codex (a)", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setUsableSessionMinutes(180).arguments(label: "codex (a)"),
            ["accounts", "set-usable", "codex (a)", "180", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setOrchestrationRole("backup").arguments(label: "codex (a)"),
            ["accounts", "set-role", "codex (a)", "backup", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setWeeklyReserveThreshold(80).arguments(label: "codex (a)"),
            ["accounts", "set-weekly-reserve", "codex (a)", "80", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setWeeklyReserveThreshold(nil).arguments(label: "codex (a)"),
            ["accounts", "clear-weekly-reserve", "codex (a)", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setNotificationRoute(.both).arguments(label: "codex (a)"),
            ["accounts", "set-notifications", "codex (a)", "--ntfy", "--telegram", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setNotificationRoute(.none).arguments(label: "codex (a)"),
            ["accounts", "set-notifications", "codex (a)", "--none", "--json-output"]
        )
        XCTAssertEqual(
            AccountMutation.setNotificationRoute(.globalDefault).arguments(label: "codex (a)"),
            ["accounts", "set-notifications", "codex (a)", "--global-default", "--json-output"]
        )
    }

    func testNotificationRouteFromDisplay() {
        XCTAssertEqual(NotificationRoute.from(routeDisplay: "global default", enabled: true), .globalDefault)
        XCTAssertEqual(NotificationRoute.from(routeDisplay: "ntfy", enabled: true), .ntfy)
        XCTAssertEqual(NotificationRoute.from(routeDisplay: "ntfy+telegram", enabled: true), .both)
        XCTAssertEqual(NotificationRoute.from(routeDisplay: "telegram", enabled: true), .telegram)
        XCTAssertEqual(NotificationRoute.from(routeDisplay: "anything", enabled: false), .none)
        XCTAssertEqual(NotificationRoute.from(routeDisplay: "disabled", enabled: true), .none)
    }
}

@MainActor
final class AccountsViewModelTests: XCTestCase {
    private var refreshCount = 0

    private func makeModel(
        service: StubAccountConfigurator = StubAccountConfigurator()
    ) -> (AccountsViewModel, StubAccountConfigurator) {
        refreshCount = 0
        let model = AccountsViewModel(service: service) { [weak self] in
            self?.refreshCount += 1
        }
        return (model, service)
    }

    func testLoadJoinsListPlanningAndNotifications() async {
        let (model, _) = makeModel()
        await model.load()

        XCTAssertEqual(model.loadPhase, .loaded)
        XCTAssertEqual(model.rows.map(\.label), ["codex (a)", "gemini (m)"])
        XCTAssertEqual(model.rows[0].planning?.usableSessionMinutes, 240)
        XCTAssertNil(model.rows[1].planning)
        XCTAssertEqual(model.notifications?.destination, "global disabled")
    }

    func testVisibilityMutationRunsReloadsAndRefreshes() async {
        let (model, service) = makeModel()
        await model.load()
        let loadsBefore = service.listLoads

        await model.apply(.setVisible(false), to: "codex (a)")

        XCTAssertEqual(
            service.mutationArguments,
            [["accounts", "hide", "codex (a)", "--json-output"]]
        )
        XCTAssertNil(model.mutationErrors["codex (a)"])
        XCTAssertGreaterThan(service.listLoads, loadsBefore, "reloads after mutation")
        XCTAssertEqual(refreshCount, 1, "snapshot refreshes after mutation")
        XCTAssertFalse(model.isBusy("codex (a)"))
    }

    func testRejectedMutationSurfacesCoreMessageVerbatim() async throws {
        let service = StubAccountConfigurator()
        service.mutationResult = .success(
            try StubAccountConfigurator.envelope(
                json: NSNull(),
                ok: false,
                errorCode: "mutation_rejected",
                message: "Note: Gemini auto-kick has been disabled (Gemini is now monitor-only)."
            )
        )
        let (model, _) = makeModel(service: service)
        await model.load()

        await model.apply(.setAutoKick(true), to: "gemini (m)")

        let error = try XCTUnwrap(model.mutationErrors["gemini (m)"])
        XCTAssertTrue(error.contains("monitor-only"))
        XCTAssertEqual(refreshCount, 1, "refresh still happens after a refusal")

        model.clearError(for: "gemini (m)")
        XCTAssertNil(model.mutationErrors["gemini (m)"])
    }

    func testAutoKickConsentRefusalOpensTypedConfirmationAndRetries() async throws {
        let service = StubAccountConfigurator()
        let consentText = """
        Enabling auto-kick for Codex
        ---------------------------------
        Type ENABLE to turn on auto-kick for Codex, or press Enter to cancel:
        """
        service.mutationResult = .success(
            try StubAccountConfigurator.envelope(
                json: [
                    "consent": [
                        "provider": "codex",
                        "version": 1,
                        "confirmation": "ENABLE",
                        "text": consentText,
                    ]
                ],
                ok: false,
                errorCode: "auto_kick_consent_required",
                message: "Auto-kick consent is required for Codex."
            )
        )
        let (model, _) = makeModel(service: service)
        await model.load()

        await model.apply(.setAutoKick(true), to: "codex (a)")

        let request = try XCTUnwrap(model.pendingAutoKickConsent)
        XCTAssertEqual(request.provider, "codex")
        XCTAssertEqual(request.confirmation, "ENABLE")
        XCTAssertEqual(request.text, consentText)
        XCTAssertNil(model.mutationErrors["codex (a)"])
        XCTAssertEqual(
            service.mutationArguments,
            [["auto", "enable", "codex (a)", "--json-output"]]
        )

        service.mutationResult = .success(
            try StubAccountConfigurator.envelope(json: ["account": ["label": "codex (a)"]])
        )
        await model.confirmAutoKickConsent()

        XCTAssertNil(model.pendingAutoKickConsent)
        XCTAssertEqual(
            service.mutationArguments.last,
            ["auto", "enable", "codex (a)", "--accept-risk", "ENABLE", "--json-output"]
        )
        XCTAssertEqual(refreshCount, 2)
    }

    func testCancellingAutoKickConsentDoesNotRetry() async throws {
        let service = StubAccountConfigurator()
        service.mutationResult = .success(
            try StubAccountConfigurator.envelope(
                json: [
                    "consent": [
                        "provider": "codex",
                        "version": 1,
                        "confirmation": "ENABLE",
                        "text": "Disclosure",
                    ]
                ],
                ok: false,
                errorCode: "auto_kick_consent_required",
                message: "Auto-kick consent is required for Codex."
            )
        )
        let (model, _) = makeModel(service: service)
        await model.load()
        await model.apply(.setWeeklyAutoKick(true), to: "codex (a)")

        model.cancelAutoKickConsent()

        XCTAssertNil(model.pendingAutoKickConsent)
        XCTAssertEqual(service.mutationArguments.count, 1)
    }

    func testThrownMutationErrorIsRecorded() async {
        let service = StubAccountConfigurator()
        service.mutationResult = .failure(StubError(description: "tk did not finish within 60s"))
        let (model, _) = makeModel(service: service)
        await model.load()

        await model.apply(.setNotificationRoute(.ntfy), to: "codex (a)")

        XCTAssertTrue(model.mutationErrors["codex (a)"]?.contains("60s") ?? false)
        XCTAssertEqual(refreshCount, 1)
    }

    func testNotificationRouteMutationSuccess() async {
        let (model, service) = makeModel()
        await model.load()

        await model.apply(.setNotificationRoute(.telegram), to: "codex (a)")

        XCTAssertEqual(
            service.mutationArguments.last,
            ["accounts", "set-notifications", "codex (a)", "--telegram", "--json-output"]
        )
        XCTAssertNil(model.mutationErrors["codex (a)"])
        XCTAssertEqual(refreshCount, 1)
    }
}


@MainActor
final class GlobalNotificationTests: XCTestCase {
    private var refreshCount = 0

    private func makeModel(
        service: StubAccountConfigurator = StubAccountConfigurator()
    ) -> (AccountsViewModel, StubAccountConfigurator) {
        refreshCount = 0
        let model = AccountsViewModel(service: service) { [weak self] in
            self?.refreshCount += 1
        }
        return (model, service)
    }

    func testGlobalMutationArguments() {
        XCTAssertEqual(
            GlobalNotificationMutation.enableNtfy(topic: "my-topic").arguments,
            ["notify", "--ntfy", "my-topic", "--json-output"]
        )
        XCTAssertEqual(
            GlobalNotificationMutation.enableTelegram(token: "tok", chatID: "42").arguments,
            ["notify", "--telegram", "tok", "42", "--json-output"]
        )
        XCTAssertEqual(
            GlobalNotificationMutation.sendTest.arguments,
            ["notify", "test", "--json-output"]
        )
        XCTAssertTrue(GlobalNotificationMutation.enableNtfy(topic: "x").changesConfiguration)
        XCTAssertFalse(GlobalNotificationMutation.sendTest.changesConfiguration)
    }

    func testGlobalNtfySuccessReloadsAndRefreshes() async throws {
        let service = StubAccountConfigurator()
        service.mutationResult = .success(
            try StubAccountConfigurator.envelope(
                json: ["global_enabled": true, "destination": "ntfy:my-topic"],
                message: "ntfy notifications enabled."
            )
        )
        let (model, _) = makeModel(service: service)
        await model.load()
        let loadsBefore = service.listLoads

        await model.applyGlobal(.enableNtfy(topic: "my-topic"))

        XCTAssertEqual(
            service.mutationArguments.last,
            ["notify", "--ntfy", "my-topic", "--json-output"]
        )
        XCTAssertEqual(model.globalMutationMessage, "ntfy notifications enabled.")
        XCTAssertNil(model.globalMutationError)
        XCTAssertGreaterThan(service.listLoads, loadsBefore, "reloads after mutation")
        XCTAssertEqual(refreshCount, 1, "snapshot refreshes after global mutation")
        XCTAssertFalse(model.globalBusy)
    }

    func testGlobalMutationFailureSurfacesMessage() async throws {
        let service = StubAccountConfigurator()
        service.mutationResult = .success(
            try StubAccountConfigurator.envelope(
                json: NSNull(),
                ok: false,
                errorCode: "usage_error",
                message: "Choose exactly one: --ntfy <topic> or --telegram <token> <chat_id>."
            )
        )
        let (model, _) = makeModel(service: service)
        await model.load()

        await model.applyGlobal(.enableNtfy(topic: "x"))

        XCTAssertNil(model.globalMutationMessage)
        XCTAssertTrue(model.globalMutationError?.contains("Choose exactly one") ?? false)
        XCTAssertEqual(refreshCount, 1)
    }

    func testGlobalThrownErrorIsRecorded() async {
        let service = StubAccountConfigurator()
        service.mutationResult = .failure(StubError(description: "tk did not finish within 60s"))
        let (model, _) = makeModel(service: service)
        await model.load()

        await model.applyGlobal(.enableTelegram(token: "t", chatID: "c"))

        XCTAssertTrue(model.globalMutationError?.contains("60s") ?? false)
        XCTAssertEqual(refreshCount, 1)
    }

    func testSendTestDoesNotReloadOrRefresh() async throws {
        let service = StubAccountConfigurator()
        service.mutationResult = .success(
            try StubAccountConfigurator.envelope(
                json: ["action": "test", "delivered": true],
                message: "Test notification sent."
            )
        )
        let (model, _) = makeModel(service: service)
        await model.load()
        let loadsBefore = service.listLoads

        await model.applyGlobal(.sendTest)

        XCTAssertEqual(model.globalMutationMessage, "Test notification sent.")
        XCTAssertEqual(service.listLoads, loadsBefore, "test changes no configuration")
        XCTAssertEqual(refreshCount, 0)
    }

    func testPerAccountRoutingUnchangedByGlobalAPI() async throws {
        let (model, service) = makeModel()
        await model.load()

        await model.apply(.setNotificationRoute(.ntfy), to: "codex (a)")

        XCTAssertEqual(
            service.mutationArguments.last,
            ["accounts", "set-notifications", "codex (a)", "--ntfy", "--json-output"]
        )
        XCTAssertNil(model.globalMutationMessage, "per-account path never touches global state")
        XCTAssertNil(model.globalMutationError)
        XCTAssertEqual(refreshCount, 1)

        model.clearGlobalResult()
        XCTAssertNil(model.globalMutationMessage)
    }
}
