import SwiftUI
import TokenKickShell

@MainActor
final class TokenKickAppDelegate: NSObject, NSApplicationDelegate {
    var root: AppRoot?

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let root else { return }
        if root.settings.shouldAutoOpenMainWindowOnLaunch {
            root.settings.markMainWindowAutoOpened()
            root.windowPresenter.open(.status)
        }
    }

    func applicationShouldHandleReopen(
        _ sender: NSApplication,
        hasVisibleWindows flag: Bool
    ) -> Bool {
        root?.windowPresenter.open()
        return false
    }
}

@main
struct TokenKickApp: App {
    @NSApplicationDelegateAdaptor(TokenKickAppDelegate.self) private var appDelegate
    private let root: AppRoot

    init() {
        let root = AppRoot()
        self.root = root
        appDelegate.root = root
    }

    var body: some Scene {
        TokenKickScenes(root: root)
    }
}
