import AppKit
import SwiftUI

struct WindowFrameAutosaver: NSViewRepresentable {
    let autosaveName: String

    func makeNSView(context: Context) -> AutosaveView {
        AutosaveView(autosaveName: autosaveName)
    }

    func updateNSView(_ nsView: AutosaveView, context: Context) {
        nsView.autosaveName = autosaveName
        nsView.applyAutosaveName()
    }
}

final class AutosaveView: NSView {
    var autosaveName: String

    init(autosaveName: String) {
        self.autosaveName = autosaveName
        super.init(frame: .zero)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        nil
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        applyAutosaveName()
    }

    func applyAutosaveName() {
        window?.setFrameAutosaveName(autosaveName)
    }
}
