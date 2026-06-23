import XCTest
import TokenKickKit
@testable import TokenKickShell

private final class StubPlannerService: PlannerServicing, @unchecked Sendable {
    var planningResult: Result<TKEnvelope<TKAccountsPlanningPayload>, Error>
    var previewResult: Result<TKPlanPayload, Error>
    var applyResult: Result<TKPlanPayload, Error>
    var cancelResult: Result<TKPlanCancelPayload, Error>
    private(set) var previewCalls: [(workWindow: String, date: String, usage: [String: Int])] = []
    private(set) var applyCalls: [(workWindow: String, date: String, usage: [String: Int])] = []
    private(set) var cancelCalls: [[String]] = []

    init(
        planning: TKEnvelope<TKAccountsPlanningPayload>,
        preview: TKPlanPayload,
        apply: TKPlanPayload? = nil,
        cancel: TKPlanCancelPayload? = nil
    ) {
        self.planningResult = .success(planning)
        self.previewResult = .success(preview)
        self.applyResult = .success(apply ?? preview)
        self.cancelResult = .success(cancel ?? Self.cancelPayload())
    }

    func accountsPlanning() async throws -> TKEnvelope<TKAccountsPlanningPayload> {
        try planningResult.get()
    }

    func previewPlan(
        workWindow: String,
        date: String,
        usage: [String: Int]
    ) async throws -> TKPlanPayload {
        previewCalls.append((workWindow, date, usage))
        return try previewResult.get()
    }

    func applyPlan(
        workWindow: String,
        date: String,
        usage: [String: Int]
    ) async throws -> TKPlanPayload {
        applyCalls.append((workWindow, date, usage))
        return try applyResult.get()
    }

    func cancelPlan(accountLabels: [String]) async throws -> TKPlanCancelPayload {
        cancelCalls.append(accountLabels)
        return try cancelResult.get()
    }

    static func planningEnvelope() throws -> TKEnvelope<TKAccountsPlanningPayload> {
        try StubAccountConfigurator.envelope(
            json: [
                "accounts": [
                    planningAccount(label: "codex (a)", minutes: 150),
                    planningAccount(label: "codex (b)", minutes: 60),
                ]
            ],
            as: TKAccountsPlanningPayload.self
        )
    }

    static func planningAccount(label: String, minutes: Int) -> [String: Any] {
        [
            "label": label,
            "provider": "codex",
            "visible": true,
            "auto_kick": true,
            "session_auto_kick": true,
            "usable_session_minutes": minutes,
            "orchestration_role": "normal",
            "effective_orchestration_role": "normal",
            "weekly_reserve_threshold_percent": NSNull(),
        ]
    }

    static func planPayload(
        readOnly: Bool = true,
        applied: Bool = false,
        message: String? = "read-only plan",
        conflicts: [[String: Any]] = [],
        segments: [[String: Any]]? = nil,
        plannedKicks: [[String: Any]]? = nil
    ) -> TKPlanPayload {
        let start = "2026-06-11T19:00:00Z"
        let end = "2026-06-11T21:30:00Z"
        let segmentList = segments ?? [
            [
                "account_key": "manual|codex|a",
                "account_label": "codex (a)",
                "provider": "codex",
                "start": start,
                "end": end,
                "source": "planned_fresh_session",
                "usable_session_minutes": 150,
                "kick_at": start,
                "note": "kick at 21:00",
            ]
        ]
        let kickList = plannedKicks ?? [
            [
                "account_key": "manual|codex|a",
                "account_label": "codex (a)",
                "provider": "codex",
                "kick_at": start,
                "work_start": start,
                "work_end": end,
                "segment_start": start,
                "segment_end": end,
                "usable_session_minutes": 150,
                "reason": "orchestrated",
                "window_basis": "session",
                "purpose": "coverage",
            ]
        ]
        let object: [String: Any] = [
            "schema_version": 1,
            "read_only": readOnly,
            "applied": applied,
            "built_at": "2026-06-11T12:00:00Z",
            "work_window": ["start": start, "end": end, "timezone": "UTC"],
            "cache_age_seconds": 0,
            "accounts_considered": [],
            "segments": segmentList,
            "planned_kicks": kickList,
            "coverage_gaps": [],
            "diff": [
                "adds": [],
                "replaces_orchestrated": [],
                "unchanged_orchestrated": [],
                "conflicts_unmanaged": conflicts,
                "skipped": [],
                "removes_orchestrated": [],
            ],
            "skipped_accounts": [],
            "limitations": [],
            "message": message ?? NSNull(),
        ]
        let data = try! JSONSerialization.data(withJSONObject: object)
        return try! JSONDecoder().decode(TKPlanPayload.self, from: data)
    }

    static func emptyPlanPayload() -> TKPlanPayload {
        planPayload(segments: [], plannedKicks: [])
    }

    static func cancelPayload() -> TKPlanCancelPayload {
        let object: [String: Any] = [
            "read_only": false,
            "applied": true,
            "message": "cancelled 1 orchestration pending kick(s)",
            "result": ["removed": [["account_label": "codex (a)"]], "kept_count": 0],
            "matching": [],
        ]
        let data = try! JSONSerialization.data(withJSONObject: object)
        return try! JSONDecoder().decode(TKPlanCancelPayload.self, from: data)
    }
}

@MainActor
final class PlannerViewModelTests: XCTestCase {
    private var refreshCount = 0

    private func makeModel(
        service: StubPlannerService
    ) -> PlannerViewModel {
        refreshCount = 0
        return PlannerViewModel(service: service, now: Date(timeIntervalSince1970: 1_781_200_000)) {
            self.refreshCount += 1
        }
    }

    func testPreviewLoadsPlanningDefaultsAndSnapshotPendingRows() async throws {
        let service = StubPlannerService(
            planning: try StubPlannerService.planningEnvelope(),
            preview: StubPlannerService.planPayload()
        )
        let model = makeModel(service: service)
        let snapshot = try ShellFixtures.snapshot(
            pendingKicks: [
                ShellFixtures.pendingKick(
                    accountLabel: "codex (a)",
                    kickAt: "2026-06-11T18:30:00Z"
                )
            ]
        )

        await model.load(snapshot: snapshot)

        XCTAssertEqual(model.phase, .loaded)
        XCTAssertEqual(model.planningAccounts.map(\.label), ["codex (a)", "codex (b)"])
        XCTAssertEqual(model.customUsageMinutes["codex (a)"], 150)
        XCTAssertEqual(model.segmentRows.map(\.source), ["Fresh session"])
        XCTAssertEqual(model.plannedKickRows.map(\.usage), ["2h30m"])
        XCTAssertEqual(model.activeOrchestratedPendingRows.map(\.account), ["codex (a)"])
        XCTAssertEqual(service.previewCalls.count, 1)
    }

    func testEmptyPreviewCannotApply() async throws {
        let service = StubPlannerService(
            planning: try StubPlannerService.planningEnvelope(),
            preview: StubPlannerService.emptyPlanPayload()
        )
        let model = makeModel(service: service)

        await model.load(snapshot: nil)

        XCTAssertTrue(model.segmentRows.isEmpty)
        XCTAssertFalse(model.canApplyPreview)
    }

    func testApplyConfirmationAndSuccessRefresh() async throws {
        let applied = StubPlannerService.planPayload(
            readOnly: false,
            applied: true,
            message: "applied; orchestrated pending session kicks were written"
        )
        let service = StubPlannerService(
            planning: try StubPlannerService.planningEnvelope(),
            preview: StubPlannerService.planPayload(),
            apply: applied
        )
        let model = makeModel(service: service)
        await model.load(snapshot: nil)

        model.requestApply()
        let action = try XCTUnwrap(model.pendingConfirmation)
        XCTAssertEqual(action.verb, "Apply Plan")
        XCTAssertFalse(action.isDestructive)
        XCTAssertTrue(action.disclosures.first?.contains("never replaced") ?? false)

        await model.confirmPendingAction(snapshot: nil)

        XCTAssertEqual(service.applyCalls.count, 1)
        XCTAssertEqual(refreshCount, 1)
        XCTAssertTrue(model.actionMessage?.contains("applied") ?? false)
        XCTAssertEqual(model.activeOrchestratedPendingRows.map(\.account), ["codex (a)"])
    }

    func testUnmanagedConflictDisablesApply() async throws {
        let service = StubPlannerService(
            planning: try StubPlannerService.planningEnvelope(),
            preview: StubPlannerService.planPayload(
                conflicts: [["account_label": "codex (manual)", "reason": "manual"]]
            )
        )
        let model = makeModel(service: service)

        await model.load(snapshot: nil)

        XCTAssertFalse(model.canApplyPreview)
        model.requestApply()
        XCTAssertNil(model.pendingConfirmation)
    }

    func testStaleApplyRefusalSurfacesMessageWithoutMarkingPlanActive() async throws {
        let refused = StubPlannerService.planPayload(
            readOnly: true,
            applied: false,
            message: "not applied; plan is stale, rebuild the plan"
        )
        let service = StubPlannerService(
            planning: try StubPlannerService.planningEnvelope(),
            preview: StubPlannerService.planPayload(),
            apply: refused
        )
        let model = makeModel(service: service)
        await model.load(snapshot: nil)

        model.requestApply()
        await model.confirmPendingAction(snapshot: nil)

        XCTAssertTrue(model.actionMessage?.contains("plan is stale") ?? false)
        XCTAssertTrue(model.activeOrchestratedPendingRows.isEmpty)
        XCTAssertEqual(refreshCount, 1)
    }

    func testCancelActivePlanFlow() async throws {
        let service = StubPlannerService(
            planning: try StubPlannerService.planningEnvelope(),
            preview: StubPlannerService.planPayload()
        )
        let model = makeModel(service: service)
        let snapshot = try ShellFixtures.snapshot(
            pendingKicks: [
                ShellFixtures.pendingKick(
                    accountLabel: "codex (a)",
                    kickAt: "2026-06-11T18:30:00Z"
                )
            ]
        )
        await model.load(snapshot: snapshot)

        model.requestCancelPlan()
        let action = try XCTUnwrap(model.pendingConfirmation)
        XCTAssertEqual(action.verb, "Cancel Plan")
        XCTAssertTrue(action.isDestructive)

        await model.confirmPendingAction(snapshot: snapshot)

        XCTAssertEqual(service.cancelCalls, [[]])
        XCTAssertEqual(refreshCount, 1)
        XCTAssertEqual(model.activeOrchestratedPendingRows, [])
    }
}

private final class StubScheduleService: ScheduleServicing, @unchecked Sendable {
    var showResult: Result<TKEnvelope<TKScheduleShowPayload>, Error>
    var accountsResult: Result<TKEnvelope<TKAccountsListPayload>, Error>
    var mutationResult: Result<TKEnvelope<TKScheduleMutationPayload>, Error>
    private(set) var setCalls: [(scope: String, weekdays: String?, weekends: String?, timezone: String?)] = []
    private(set) var clearCalls: [String] = []
    private(set) var disableCalls: [String] = []

    init(
        show: TKEnvelope<TKScheduleShowPayload>,
        accounts: TKEnvelope<TKAccountsListPayload>,
        mutation: TKEnvelope<TKScheduleMutationPayload>
    ) {
        self.showResult = .success(show)
        self.accountsResult = .success(accounts)
        self.mutationResult = .success(mutation)
    }

    func scheduleShow() async throws -> TKEnvelope<TKScheduleShowPayload> {
        try showResult.get()
    }

    func accountsList() async throws -> TKEnvelope<TKAccountsListPayload> {
        try accountsResult.get()
    }

    func setSchedule(
        scope: String,
        weekdays: String?,
        weekends: String?,
        timezone: String?
    ) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        setCalls.append((scope, weekdays, weekends, timezone))
        return try mutationResult.get()
    }

    func clearSchedule(scope: String) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        clearCalls.append(scope)
        return try mutationResult.get()
    }

    func disableSchedule(scope: String) async throws -> TKEnvelope<TKScheduleMutationPayload> {
        disableCalls.append(scope)
        return try mutationResult.get()
    }

    static func showEnvelope() throws -> TKEnvelope<TKScheduleShowPayload> {
        try StubAccountConfigurator.envelope(
            json: showPayload(),
            as: TKScheduleShowPayload.self
        )
    }

    static func accountsEnvelope() throws -> TKEnvelope<TKAccountsListPayload> {
        try StubAccountConfigurator.envelope(
            json: [
                "accounts": [
                    StubAccountConfigurator.listAccount(
                        label: "codex (a)",
                        provider: "codex",
                        autoKick: true
                    ),
                    StubAccountConfigurator.listAccount(
                        label: "gemini (m)",
                        provider: "gemini",
                        monitorOnly: true
                    ),
                ]
            ],
            as: TKAccountsListPayload.self
        )
    }

    static func mutationEnvelope(
        action: String = "set",
        scope: String = "default"
    ) throws -> TKEnvelope<TKScheduleMutationPayload> {
        try StubAccountConfigurator.envelope(
            json: [
                "action": action,
                "scope": scope,
                "removed_pending_kicks": [],
                "schedule": showPayload(),
            ],
            message: "Schedule updated.",
            as: TKScheduleMutationPayload.self
        )
    }

    static func showPayload() -> [String: Any] {
        [
            "enabled": true,
            "timezone": "Europe/Berlin",
            "scheduling_target": "auto",
            "default": ["enabled": true, "weekdays": "09:00-17:00"],
            "accounts": [
                "codex (a)": ["enabled": true, "weekends": "10:00-14:00"]
            ],
            "pending_kicks": [
                [
                    "key": "manual|codex|a",
                    "account_label": "codex (a)",
                    "kick_at": "2026-06-11T10:00:00Z",
                    "reason": "scheduled",
                    "purpose": "coverage",
                ]
            ],
        ]
    }
}

@MainActor
final class ScheduleViewModelTests: XCTestCase {
    private var refreshCount = 0

    private func makeModel(
        service: StubScheduleService
    ) -> ScheduleViewModel {
        refreshCount = 0
        return ScheduleViewModel(service: service) {
            self.refreshCount += 1
        }
    }

    private func makeService() throws -> StubScheduleService {
        try StubScheduleService(
            show: StubScheduleService.showEnvelope(),
            accounts: StubScheduleService.accountsEnvelope(),
            mutation: StubScheduleService.mutationEnvelope()
        )
    }

    func testLoadBuildsDefaultAndPerAccountRows() async throws {
        let service = try makeService()
        let model = makeModel(service: service)

        await model.load()

        XCTAssertEqual(model.phase, .loaded)
        XCTAssertEqual(model.rows.map(\.id), ["default", "codex (a)"])
        XCTAssertEqual(model.rows[0].weekdays, "09:00-17:00")
        XCTAssertEqual(model.rows[1].weekends, "10:00-14:00")
        // The default scope lists every pending kick, so it counts them all.
        XCTAssertEqual(model.rows.map(\.pendingCount), [1, 1])
        XCTAssertEqual(model.pendingKicks.map(\.account), ["codex (a)"])
        XCTAssertEqual(model.timezone, "Europe/Berlin")
        XCTAssertTrue(model.selectedScopeEnabled)
    }

    func testEnableSendsSetWithNoWindowOverrides() async throws {
        let service = try makeService()
        let model = makeModel(service: service)
        await model.load()

        await model.enable()

        let call = try XCTUnwrap(service.setCalls.last)
        XCTAssertEqual(call.0, "default")
        XCTAssertNil(call.1, "enable keeps the configured weekday window")
        XCTAssertNil(call.2, "enable keeps the configured weekend window")
        XCTAssertNil(call.3)
        XCTAssertEqual(refreshCount, 1)
    }

    func testSaveDefaultScheduleRunsMutationRefreshesAndReloads() async throws {
        let service = try makeService()
        let model = makeModel(service: service)
        await model.load()
        model.weekdays = "08:00-16:00"
        model.weekends = ""
        model.timezone = "UTC"

        await model.save()

        XCTAssertEqual(service.setCalls.count, 1)
        XCTAssertEqual(service.setCalls[0].scope, "default")
        XCTAssertEqual(service.setCalls[0].weekdays, "08:00-16:00")
        XCTAssertNil(service.setCalls[0].weekends)
        XCTAssertEqual(service.setCalls[0].timezone, "UTC")
        XCTAssertEqual(refreshCount, 1)
        XCTAssertEqual(model.resultMessage, "Schedule updated.")
    }

    func testSavePerAccountScheduleUsesSelectedScope() async throws {
        let service = try makeService()
        let model = makeModel(service: service)
        await model.load()
        model.selectedScope = "codex (a)"
        model.weekdays = "12:00-18:00"

        await model.save()

        XCTAssertEqual(service.setCalls.first?.scope, "codex (a)")
        XCTAssertEqual(refreshCount, 1)
    }

    func testClearConfirmationSafeDefaultAndCancelDoesNotMutate() async throws {
        let service = try makeService()
        let model = makeModel(service: service)
        await model.load()

        model.requestClear()
        let action = try XCTUnwrap(model.pendingConfirmation)
        XCTAssertEqual(action.verb, "Clear Schedule")
        XCTAssertTrue(action.isDestructive)
        XCTAssertEqual(action.tkArguments, ["schedule", "clear", "--default", "--json-output"])

        model.cancelConfirmation()

        XCTAssertNil(model.pendingConfirmation)
        XCTAssertEqual(service.clearCalls, [])
        XCTAssertEqual(refreshCount, 0)
    }

    func testDisableConfirmationForAccountAndMutation() async throws {
        let service = try makeService()
        let model = makeModel(service: service)
        await model.load()
        model.selectedScope = "codex (a)"

        model.requestDisable()
        let action = try XCTUnwrap(model.pendingConfirmation)
        XCTAssertEqual(action.tkArguments, [
            "schedule",
            "disable",
            "--account",
            "codex (a)",
            "--json-output",
        ])

        await model.confirmPendingAction()

        XCTAssertEqual(service.disableCalls, ["codex (a)"])
        XCTAssertEqual(refreshCount, 1)
    }

    func testLoadFailureSurfacesErrorState() async throws {
        let service = try makeService()
        service.showResult = .failure(StubError(description: "tk timed out"))
        let model = makeModel(service: service)

        await model.load()

        guard case .failed(let message) = model.phase else {
            return XCTFail("expected failed state")
        }
        XCTAssertTrue(message.contains("timed out"))
    }
}
