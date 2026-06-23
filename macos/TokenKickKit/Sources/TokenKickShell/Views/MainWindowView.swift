import SwiftUI

/// Main window shell (UX plan §4): sidebar, toolbar with global refresh,
/// warnings badge, daemon chip; Status and Daemon have real content, the
/// rest are placeholders until the workflow screens phase.
public struct MainWindowView: View {
    public static let windowID = "main"

    @Environment(SnapshotStore.self) private var store
    @Environment(NavigationModel.self) private var navigation
    @Environment(AppSettingsModel.self) private var settings
    @Environment(FirstRunModel.self) private var firstRun
    @State private var warningsPopoverShown = false
    @State private var firstRunShown = false

    public init() {}

    public var body: some View {
        @Bindable var navigation = navigation
        NavigationSplitView {
            List(selection: $navigation.selection) {
                ForEach(NavigationModel.sections) { section in
                    Section(section.title) {
                        ForEach(section.destinations) { destination in
                            Label(destination.title, systemImage: destination.symbolName)
                                .tag(destination)
                        }
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 180, ideal: 200)
        } detail: {
            VStack(spacing: 0) {
                if let blocker = store.warningItems.first(where: { $0.tier == .blocker }) {
                    WarningBannerView(item: blocker)
                }
                detailScreen
            }
        }
        .toolbar { toolbarContent }
        .task {
            await store.refresh()
            if FirstRunModel.shouldOffer(
                snapshot: store.snapshot,
                completedBefore: settings.firstRunCompleted
            ) {
                firstRunShown = true
            }
        }
        .sheet(isPresented: $firstRunShown) {
            FirstRunView {
                settings.firstRunCompleted = true
                firstRunShown = false
                navigation.open(.status)
            }
            .interactiveDismissDisabled(false)
        }
        .frame(minWidth: 760, minHeight: 440)
        .background(WindowFrameAutosaver(autosaveName: "TokenKickMainWindow"))
    }

    @ViewBuilder
    private var detailScreen: some View {
        switch navigation.selection {
        case .status:
            StatusScreen()
        case .kick:
            KickScreen()
        case .planner:
            PlannerScreen()
        case .schedule:
            ScheduleScreen()
        case .accounts:
            AccountsScreen()
        case .notifications:
            NotificationsScreen()
        case .daemon:
            DaemonScreen()
        case .history:
            HistoryScreen()
        case .diagnostics:
            DiagnosticsScreen()
        case .advanced:
            AdvancedScreen()
        }
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            updatedLabel
            Button {
                Task { await store.refresh() }
            } label: {
                if store.isRefreshing {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .help("Refresh status")
            .disabled(store.isRefreshing)

            warningsBadge

            daemonChipButton
        }
    }

    /// "Checked", not "Updated": this is when the app last fetched, not how
    /// old the underlying data is — stale rows carry their own age. Ticks
    /// without needing a refresh.
    @ViewBuilder
    private var updatedLabel: some View {
        if let lastUpdated = store.lastUpdated {
            TimelineView(.periodic(from: .now, by: 10)) { context in
                Text("Checked \(RelativeTimeText.ago(from: lastUpdated, now: context.date))")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        }
    }

    /// Badge and popover show the same set — blocker through advisory.
    /// Footnotes never badge (UX plan §5); they stay on their screens.
    @ViewBuilder
    private var warningsBadge: some View {
        let notices = store.warningItems.filter { $0.tier <= .advisory }
        let actionable = notices.filter { $0.tier <= .warning }
        let isAdvisoryOnly = actionable.isEmpty && !notices.isEmpty
        Button {
            warningsPopoverShown.toggle()
        } label: {
            if notices.isEmpty {
                Image(systemName: "checkmark.circle")
                    .foregroundStyle(.secondary)
            } else {
                Label {
                    Text("\(notices.count)")
                        .font(.caption.monospacedDigit())
                } icon: {
                    Image(systemName: isAdvisoryOnly ? "info.circle" : "exclamationmark.circle")
                        .foregroundStyle(isAdvisoryOnly ? Color.blue : Color.orange)
                }
            }
        }
        .help(notices.isEmpty ? "No active notices" : "Show active notices")
        .popover(isPresented: $warningsPopoverShown, arrowEdge: .bottom) {
            WarningListView(items: notices) { destination in
                navigation.open(destination)
                warningsPopoverShown = false
            }
        }

    }

    private var daemonChipButton: some View {
        let chip = store.daemonChip
        return Button {
            navigation.open(.daemon)
        } label: {
            Label(chip.title, systemImage: chip.symbolName)
                .foregroundStyle(chip.hasIssue ? Color.orange : Color.primary)
        }
        .help("Background service: \(chip.title)")
    }
}
