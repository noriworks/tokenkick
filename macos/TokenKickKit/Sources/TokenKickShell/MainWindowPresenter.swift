import AppKit
import SwiftUI

@MainActor
@Observable
public final class MainWindowPresenter {
    private let navigation: NavigationModel
    private var openMainWindow: (() -> Void)?
    private var pendingOpen = false

    public init(navigation: NavigationModel) {
        self.navigation = navigation
    }

    public func registerOpenWindow(_ action: @escaping () -> Void) {
        openMainWindow = action
        if pendingOpen {
            pendingOpen = false
            open()
        }
    }

    public func open(_ destination: SidebarDestination? = nil) {
        if let destination {
            navigation.open(destination)
        }
        if focusExistingWindow() {
            return
        }

        guard let openMainWindow else {
            pendingOpen = true
            return
        }

        openMainWindow()
        DispatchQueue.main.async { [weak self] in
            self?.focusExistingWindow()
        }
    }

    @discardableResult
    private func focusExistingWindow() -> Bool {
        guard let window = NSApplication.shared.windows.first(where: { window in
            window.identifier?.rawValue == MainWindowView.windowID
                || window.title == "TokenKick"
        }) else {
            return false
        }
        if window.isMiniaturized {
            window.deminiaturize(nil)
        }
        window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
        return true
    }
}
