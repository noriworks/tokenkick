import SwiftUI
import TokenKickKit

/// Owns the app-wide model objects and the scene wiring so the executable
/// target stays a one-line `@main`.
@MainActor
public struct AppRoot {
    public let store: SnapshotStore
    public let navigation: NavigationModel
    public let settings: AppSettingsModel
    public let kick: KickViewModel
    public let daemon: DaemonViewModel
    public let setup: SetupViewModel
    public let accounts: AccountsViewModel
    public let firstRun: FirstRunModel
    public let history: HistoryViewModel
    public let diagnostics: DiagnosticsViewModel
    public let planner: PlannerViewModel
    public let schedule: ScheduleViewModel
    public let advanced: AdvancedViewModel
    public let windowPresenter: MainWindowPresenter

    public init() {
        let settings = AppSettingsModel()
        self.settings = settings
        let store = SnapshotStore(provider: LiveSnapshotProvider())
        self.store = store
        let navigation = NavigationModel()
        self.navigation = navigation
        let kick = KickViewModel(performer: LiveKickPerformer()) {
            await store.refresh()
        }
        self.kick = kick
        let daemon = DaemonViewModel(
            controller: LiveDaemonController(),
            agent: LiveLaunchAgentManager()
        ) {
            await store.refresh()
        }
        self.daemon = daemon
        let setup = SetupViewModel(starter: LiveSetupSessionStarter()) {
            await store.refresh()
        }
        self.setup = setup
        let accounts = AccountsViewModel(service: LiveAccountConfigurator()) {
            await store.refresh()
        }
        self.accounts = accounts
        let firstRun = FirstRunModel()
        self.firstRun = firstRun
        let history = HistoryViewModel(provider: LiveHistoryProvider())
        self.history = history
        let diagnostics = DiagnosticsViewModel(provider: LiveDiagnosticsProvider()) {
            await store.refresh()
        }
        self.diagnostics = diagnostics
        let planner = PlannerViewModel(service: LivePlannerService()) {
            await store.refresh()
        }
        self.planner = planner
        let schedule = ScheduleViewModel(service: LiveScheduleService()) {
            await store.refresh()
        }
        self.schedule = schedule
        let advanced = AdvancedViewModel(service: LiveAdvancedService()) {
            await store.refresh()
        }
        self.advanced = advanced
        self.windowPresenter = MainWindowPresenter(navigation: navigation)
        store.setAutoRefresh(every: settings.refreshInterval.seconds)
    }

    public var menuBarSymbolName: String {
        switch store.menuBarIndicator {
        case .normal: return "bolt.circle"
        // A structurally distinct glyph: filled-vs-outline is illegible at
        // menu bar size.
        case .warning: return "bolt.trianglebadge.exclamationmark"
        case .blocker: return "exclamationmark.circle.fill"
        }
    }

    public func applyRefreshInterval() {
        store.setAutoRefresh(every: settings.refreshInterval.seconds)
    }
}

/// The app's scenes, shared between the executable target and previews.
public struct TokenKickScenes: Scene {
    private let root: AppRoot

    public init(root: AppRoot) {
        self.root = root
    }

    public var body: some Scene {
        MenuBarExtra {
            MenuBarPopoverView()
                .environment(root.store)
                .environment(root.navigation)
                .environment(root.settings)
                .environment(root.kick)
                .environment(root.planner)
                .environment(root.windowPresenter)
        } label: {
            Label("TokenKick", systemImage: root.menuBarSymbolName)
                .background(
                    MainWindowOpenWindowRegistrar()
                        .environment(root.windowPresenter)
                )
        }
        .menuBarExtraStyle(.window)

        Window("TokenKick", id: MainWindowView.windowID) {
            MainWindowView()
                .environment(root.store)
                .environment(root.navigation)
                .environment(root.settings)
                .environment(root.kick)
                .environment(root.daemon)
                .environment(root.setup)
                .environment(root.accounts)
                .environment(root.firstRun)
                .environment(root.history)
                .environment(root.diagnostics)
                .environment(root.planner)
                .environment(root.schedule)
                .environment(root.advanced)
                .environment(root.windowPresenter)
                .onChange(of: root.settings.refreshInterval) {
                    root.applyRefreshInterval()
                }
        }
        .defaultSize(width: 980, height: 640)

        Settings {
            SettingsView()
                .environment(root.settings)
                .environment(root.daemon)
                .environment(root.store)
        }
    }
}

private struct MainWindowOpenWindowRegistrar: View {
    @Environment(\.openWindow) private var openWindow
    @Environment(MainWindowPresenter.self) private var windowPresenter

    var body: some View {
        Color.clear
            .frame(width: 0, height: 0)
            .onAppear {
                windowPresenter.registerOpenWindow {
                    openWindow(id: MainWindowView.windowID)
                }
            }
    }
}
