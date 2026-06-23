import XCTest
import TokenKickKit
@testable import TokenKickShell

@MainActor
final class NavigationModelTests: XCTestCase {
    func testSidebarSectionsMatchUXPlan() {
        let sections = NavigationModel.sections
        XCTAssertEqual(sections.map(\.id), ["monitor", "act", "configure", "maintain"])
        XCTAssertEqual(sections[0].destinations, [.status, .history])
        XCTAssertEqual(sections[1].destinations, [.kick, .planner, .schedule])
        XCTAssertEqual(sections[2].destinations, [.accounts, .notifications, .daemon])
        XCTAssertEqual(sections[3].destinations, [.diagnostics, .advanced])
    }

    func testEveryDestinationAppearsExactlyOnce() {
        let listed = NavigationModel.sections.flatMap(\.destinations)
        XCTAssertEqual(listed.count, SidebarDestination.allCases.count)
        XCTAssertEqual(Set(listed), Set(SidebarDestination.allCases))
    }

    func testDefaultSelectionIsStatus() {
        XCTAssertEqual(NavigationModel().selection, .status)
    }

    func testImplementedScreensMatchShippedPhases() {
        let implemented = SidebarDestination.allCases.filter(\.isImplemented)
        XCTAssertEqual(Set(implemented), Set(SidebarDestination.allCases))
        let placeholders = SidebarDestination.allCases.filter { !$0.isImplemented }
        XCTAssertTrue(placeholders.isEmpty)
    }

    func testOpenChangesSelection() {
        let model = NavigationModel()
        model.open(.daemon)
        XCTAssertEqual(model.selection, .daemon)
    }
}

@MainActor
final class AppSettingsModelTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "tk-settings-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    func testFreshDefaults() {
        let settings = AppSettingsModel(defaults: defaults)
        XCTAssertEqual(settings.extraPathEntries, [])
        XCTAssertEqual(settings.refreshInterval, .oneMinute)
        XCTAssertTrue(settings.updateChecksVisible)
        XCTAssertFalse(settings.mainWindowAutoOpened)
        XCTAssertTrue(settings.shouldAutoOpenMainWindowOnLaunch)
    }

    func testRefreshIntervalSecondsMapping() {
        XCTAssertEqual(AppSettingsModel.RefreshInterval.thirtySeconds.seconds, 30)
        XCTAssertEqual(AppSettingsModel.RefreshInterval.oneMinute.seconds, 60)
        XCTAssertEqual(AppSettingsModel.RefreshInterval.fiveMinutes.seconds, 300)
    }

    func testPathEntriesPersistAcrossInstances() {
        let settings = AppSettingsModel(defaults: defaults)
        settings.addPathEntry("  /opt/tools/bin  ")
        settings.addPathEntry("/opt/tools/bin")
        settings.refreshInterval = .fiveMinutes
        settings.updateChecksVisible = false
        settings.markMainWindowAutoOpened()

        let reloaded = AppSettingsModel(defaults: defaults)
        XCTAssertEqual(reloaded.extraPathEntries, ["/opt/tools/bin"], "trimmed and deduplicated")
        XCTAssertEqual(reloaded.refreshInterval, .fiveMinutes)
        XCTAssertFalse(reloaded.updateChecksVisible)
        XCTAssertTrue(reloaded.mainWindowAutoOpened)
        XCTAssertFalse(reloaded.shouldAutoOpenMainWindowOnLaunch)
        XCTAssertEqual(
            AppSettingsModel.storedExtraPathEntries(defaults: defaults),
            ["/opt/tools/bin"]
        )
    }

    func testRemovePathEntries() {
        let settings = AppSettingsModel(defaults: defaults)
        settings.addPathEntry("/a")
        settings.addPathEntry("/b")
        settings.removePathEntries(at: IndexSet(integer: 0))
        XCTAssertEqual(settings.extraPathEntries, ["/b"])
    }

    func testPathAdditionsCombineBuiltinsAndExtras() {
        let settings = AppSettingsModel(defaults: defaults)
        settings.addPathEntry("/opt/tools/bin")
        XCTAssertEqual(
            settings.pathAdditions,
            TKEnvironment.defaultPathAdditions + ["/opt/tools/bin"]
        )
    }

    func testProviderToolIndicatorFindsExecutables() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("tk-settings-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let codex = directory.appendingPathComponent("codex")
        try Data("#!/bin/sh\n".utf8).write(to: codex)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: codex.path
        )

        XCTAssertEqual(AppSettingsModel.providerTools(foundIn: directory.path), ["codex"])
        XCTAssertEqual(AppSettingsModel.providerTools(foundIn: "/nonexistent-dir"), [])
    }

    func testStoredPathEntriesFeedSubprocessEnvironment() {
        let settings = AppSettingsModel(defaults: defaults)
        settings.addPathEntry("/opt/tools/bin")

        // The same composition LiveTKClient/LiveSetupSessionStarter use.
        let stored = AppSettingsModel.storedExtraPathEntries(defaults: defaults)
        let environment = TKEnvironment.subprocessEnvironment(
            base: ["PATH": "/usr/bin", "HOME": "/Users/fixture"],
            pathAdditions: TKEnvironment.defaultPathAdditions + stored
        )
        XCTAssertTrue(try! XCTUnwrap(environment["PATH"]).contains("/opt/tools/bin"))
        XCTAssertTrue(try! XCTUnwrap(environment["PATH"]).contains("/opt/homebrew/bin"))
        XCTAssertEqual(environment["TK_APP_MODE"], "1")
    }

    func testProviderToolIndicatorExpandsTilde() {
        var checked: [String] = []
        _ = AppSettingsModel.providerTools(
            foundIn: "~/bin",
            home: "/Users/fixture",
            isExecutable: { path in
                checked.append(path)
                return false
            }
        )
        XCTAssertEqual(checked, ["/Users/fixture/bin/codex", "/Users/fixture/bin/claude"])
    }

    func testMainWindowAutoOpenIsSkippedAfterFirstRunCompleted() {
        let settings = AppSettingsModel(defaults: defaults)
        settings.firstRunCompleted = true

        XCTAssertFalse(settings.shouldAutoOpenMainWindowOnLaunch)
    }
}
