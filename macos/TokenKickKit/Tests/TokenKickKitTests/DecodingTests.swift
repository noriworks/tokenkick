import XCTest
@testable import TokenKickKit

final class DecodingTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        guard
            let url = Bundle.module.url(
                forResource: name,
                withExtension: nil,
                subdirectory: "Fixtures"
            )
        else {
            throw XCTSkip("fixture \(name) missing — run scripts/generate-swift-fixtures.sh")
        }
        return try Data(contentsOf: url)
    }

    func testUsageErrorEnvelope() throws {
        let envelope = try TKJSONDecoding.envelope(TKJSONValue.self, from: fixture("usage_error.json"))
        XCTAssertEqual(envelope.schemaVersion, 1)
        XCTAssertFalse(envelope.ok)
        XCTAssertEqual(envelope.errorCode, "usage_error")
        XCTAssertNotNil(envelope.message)
        XCTAssertNil(envelope.payload)
    }

    func testMutationErrorEnvelope() throws {
        let envelope = try TKJSONDecoding.envelope(TKJSONValue.self, from: fixture("mutation_error.json"))
        XCTAssertFalse(envelope.ok)
        XCTAssertEqual(envelope.errorCode, "mutation_failed")
        XCTAssertTrue(envelope.message?.contains("not found") ?? false)
    }

    func testDaemonStatusEnvelope() throws {
        let envelope = try TKJSONDecoding.envelope(
            TKDaemonEnvelopePayload.self,
            from: fixture("daemon_status.json")
        )
        XCTAssertTrue(envelope.ok)
        let daemon = try XCTUnwrap(envelope.payload).daemon
        XCTAssertFalse(daemon.running)
        XCTAssertNil(daemon.pid)
        XCTAssertFalse(daemon.stalePidfile)
        XCTAssertFalse(daemon.installedVersion.isEmpty)
        XCTAssertGreaterThan(daemon.pollIntervalMinutes, 0)
        XCTAssertTrue(daemon.pidfilePath.hasSuffix("daemon.pid"))
    }

    func testAccountsListEnvelope() throws {
        let envelope = try TKJSONDecoding.envelope(
            TKAccountsListPayload.self,
            from: fixture("accounts_list.json")
        )
        XCTAssertTrue(envelope.ok)
        let accounts = try XCTUnwrap(envelope.payload).accounts
        XCTAssertEqual(accounts.count, 2)
        let codex = try XCTUnwrap(accounts.first { $0.provider == "codex" })
        XCTAssertEqual(codex.label, "codex (fixture)")
        XCTAssertTrue(codex.kickable)
        XCTAssertTrue(codex.autoKick)
        XCTAssertFalse(codex.monitorOnly)
        let gemini = try XCTUnwrap(accounts.first { $0.provider == "gemini" })
        XCTAssertTrue(gemini.monitorOnly)
        XCTAssertFalse(gemini.kickable)
    }

    func testSnapshotEnvelope() throws {
        let envelope = try TKJSONDecoding.envelope(
            TKSnapshotPayload.self,
            from: fixture("snapshot.json")
        )
        XCTAssertTrue(envelope.ok)
        let payload = try XCTUnwrap(envelope.payload)

        XCTAssertFalse(payload.core.version.isEmpty)
        XCTAssertTrue(payload.core.appMode)
        XCTAssertEqual(payload.core.version, payload.daemon.installedVersion)
        XCTAssertEqual(payload.core.version, payload.update.installedVersion)

        // Fixture is captured with a stripped PATH: no external tk visible.
        XCTAssertNil(payload.runtime.externalTk)

        // Path keys are verbatim snake_case strings.
        XCTAssertNotNil(payload.paths["config_dir"])
        XCTAssertNotNil(payload.paths["daemon_pidfile"])

        XCTAssertFalse(payload.daemon.running)
        XCTAssertFalse(payload.status.cached)
        XCTAssertEqual(payload.pendingKicks.count, 0)

        let notificationAccounts = try XCTUnwrap(payload.notifications["accounts"]?.arrayValue)
        XCTAssertEqual(notificationAccounts.count, 2)
        XCTAssertEqual(
            notificationAccounts.first?["label"]?.stringValue,
            "codex (fixture)"
        )

        XCTAssertNotNil(payload.schedule["enabled"]?.boolValue)
        XCTAssertNotNil(payload.codexStrategy["enabled"]?.boolValue)

        // The "no status cache yet" warning must surface to the app.
        XCTAssertTrue(envelope.warnings.contains { $0.lowercased().contains("status cache") })
    }

    func testSetupEventStream() throws {
        let events = try TKSetupStream.events(from: fixture("app_setup_events.jsonl"))
        XCTAssertGreaterThanOrEqual(events.count, 3)
        XCTAssertEqual(events.first?.event, "setup_started")
        XCTAssertTrue(events.contains { $0.event == "discovery_completed" })

        for event in events.dropLast() {
            XCTAssertFalse(event.isTerminal, "non-final record \(event.event) carries envelope keys")
        }

        let terminal = try TKSetupStream.terminalEvent(in: events)
        XCTAssertEqual(terminal.event, "setup_completed")
        XCTAssertEqual(terminal.ok, true)
        XCTAssertNotNil(terminal.payload)
        XCTAssertNotNil(terminal.warnings)
    }

    func testKickSkippedEnvelope() throws {
        let envelope = try TKJSONDecoding.envelope(
            TKKickResultPayload.self,
            from: fixture("kick_skipped.json")
        )
        XCTAssertTrue(envelope.ok)
        let payload = try XCTUnwrap(envelope.payload)
        XCTAssertEqual(payload.action, "kick")
        XCTAssertEqual(payload.account, "codex (fixture)")
        XCTAssertEqual(payload.decision, "skipped")
        XCTAssertEqual(payload.reasonCode, "not_fresh")
        XCTAssertEqual(payload.kicked, false)
        XCTAssertNil(payload.result)
        XCTAssertTrue(envelope.message?.contains("not fresh") ?? false)
    }

    func testHistoryFixtureDecodesAsBareArray() throws {
        let value = try TKJSONDecoding.bareValue(from: fixture("history.json"))
        let events = try XCTUnwrap(value.arrayValue)
        XCTAssertEqual(events.count, 3)
        XCTAssertEqual(events[0]["label"]?.stringValue, "codex (fixture)")
        XCTAssertEqual(events[0]["confirmed"]?.boolValue, true)
        XCTAssertEqual(events[1]["post_kick_status"]?.stringValue, "phantom")
        XCTAssertEqual(events[2]["success"]?.boolValue, false)
    }

    func testResetLogFixtureDecodes() throws {
        let value = try TKJSONDecoding.bareValue(from: fixture("reset_log.json"))
        let events = try XCTUnwrap(value["events"]?.arrayValue)
        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0]["id"]?.stringValue, "fixture-reset-1")
        XCTAssertEqual(events[0]["provider"]?.stringValue, "codex")
        XCTAssertEqual(events[0]["confidence"]?.stringValue, "confirmed")
    }

    func testAppDoctorFixtureDecodes() throws {
        let envelope = try TKJSONDecoding.envelope(TKJSONValue.self, from: fixture("app_doctor.json"))
        XCTAssertTrue(envelope.ok)
        let payload = try XCTUnwrap(envelope.payload)
        for section in ["environment", "provider_clis", "state", "daemon", "doctor"] {
            XCTAssertNotNil(payload[section], "missing doctor section \(section)")
        }
        XCTAssertNotNil(payload["doctor"]?["summary"]?["ok"]?.numberValue)
    }

    func testNotifyGlobalFixtureDecodes() throws {
        let envelope = try TKJSONDecoding.envelope(
            TKAccountNotificationsPayload.self,
            from: fixture("notify_global.json")
        )
        XCTAssertTrue(envelope.ok)
        XCTAssertEqual(envelope.message, "ntfy notifications enabled.")
        let payload = try XCTUnwrap(envelope.payload)
        XCTAssertTrue(payload.globalEnabled)
        XCTAssertEqual(payload.destination, "ntfy:fixture-topic")
    }

    func testPlanPreviewFixtureDecodesAsBarePayload() throws {
        let payload = try JSONDecoder().decode(TKPlanPayload.self, from: fixture("plan_preview.json"))
        XCTAssertTrue(payload.readOnly)
        XCTAssertFalse(payload.applied)
        XCTAssertEqual(payload.segments.first?.accountLabel, "codex (fixture)")
        XCTAssertEqual(payload.segments.first?.source, "planned_fresh_session")
        XCTAssertEqual(payload.plannedKicks.first?.purpose, "coverage")
        XCTAssertEqual(payload.plannedKicks.first?.usableSessionMinutes, 150)
    }

    func testScheduleShowFixtureDecodes() throws {
        let envelope = try TKJSONDecoding.envelope(
            TKScheduleShowPayload.self,
            from: fixture("schedule_show.json")
        )
        XCTAssertTrue(envelope.ok)
        let payload = try XCTUnwrap(envelope.payload)
        XCTAssertTrue(payload.enabled)
        XCTAssertEqual(payload.timezone, "Europe/Berlin")
        XCTAssertEqual(payload.default.weekdays, "09:00-17:00")
        XCTAssertEqual(payload.accounts["codex (fixture)"]?.weekends, "10:00-14:00")
        XCTAssertEqual(payload.pendingKicks.first?["account_label"]?.stringValue, "codex (fixture)")
    }

    func testCodexStrategyFixtureDecodes() throws {
        let payload = try TKJSONDecoding.bare(
            TKCodexStrategyPayload.self,
            from: fixture("codex_strategy.json")
        )
        XCTAssertFalse(payload.enabled)
        XCTAssertEqual(payload.autoDemotion.summary, "mixed (1 on, 1 off)")
        XCTAssertEqual(payload.defaultOrder.first, "legacy")
        XCTAssertEqual(payload.effectiveKickingOrderByAccount["codex (fixture)"]?.first, "legacy")
    }

    func testCodexSurfacesFixtureDecodes() throws {
        let payload = try TKJSONDecoding.bare(
            TKCodexSurfacesPayload.self,
            from: fixture("codex_surfaces.json")
        )
        XCTAssertEqual(payload.label, "codex (fixture)")
        XCTAssertTrue(payload.demotion.enabled)
        XCTAssertEqual(payload.demotion.forceKeep, ["repo"])
        XCTAssertEqual(payload.demotion.forcePrune, ["interactive-like"])
        XCTAssertEqual(payload.surfaces.first?.state, "force-kept")
    }

    func testCodexSurfacePatternsFixtureDecodes() throws {
        let payload = try TKJSONDecoding.bare(
            TKCodexSurfacePatternsPayload.self,
            from: fixture("codex_surface_patterns.json")
        )
        XCTAssertEqual(payload.scopeLabel, "codex (fixture)")
        XCTAssertEqual(payload.eligibleClusters, 8)
        XCTAssertEqual(payload.candidates["previous_same_account_surface"]?.top1Hits, 4)
        XCTAssertEqual(payload.verdict.message, "No stable sequence pattern detected.")
    }

    func testFixturesContainNoMachineSpecificPaths() throws {
        let fixturesURL = try XCTUnwrap(
            Bundle.module.url(forResource: "Fixtures", withExtension: nil)
        )
        let files = try FileManager.default.contentsOfDirectory(
            at: fixturesURL,
            includingPropertiesForKeys: nil
        )
        XCTAssertFalse(files.isEmpty)
        let forbidden = try NSRegularExpression(
            pattern: "/var/folders/|/tmp/tmp|/Users/(?!fixture\\b)[^/\"]+"
        )
        for file in files {
            let text = try String(contentsOf: file, encoding: .utf8)
            let match = forbidden.firstMatch(
                in: text,
                range: NSRange(text.startIndex..., in: text)
            )
            XCTAssertNil(
                match,
                "\(file.lastPathComponent) leaks a machine-specific path: "
                    + (match.flatMap { Range($0.range, in: text).map { String(text[$0]) } } ?? "")
            )
        }
    }

    func testEmptyOutputThrows() {
        XCTAssertThrowsError(try TKJSONDecoding.envelope(TKJSONValue.self, from: Data()))
    }

    func testNonEnvelopeOutputThrows() {
        let data = Data("Traceback (most recent call last):".utf8)
        XCTAssertThrowsError(try TKJSONDecoding.envelope(TKJSONValue.self, from: data)) { error in
            guard case TKDecodingError.invalidEnvelope = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }
}
