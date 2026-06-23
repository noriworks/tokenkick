import SwiftUI

/// The one confirmation pattern for risky/quota-consuming actions
/// (UX plan §7): what happens → cost line → disclosures → verb buttons.
/// Cancel owns Return and Esc — the safe choice always has the keyboard.
struct ConfirmationSheetView: View {
    let action: ConfirmedAction
    var onCancel: () -> Void
    var onConfirm: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(action.title)
                .font(.headline)
            Text(action.explanation)
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if let costLine = action.costLine {
                Label {
                    Text(costLine)
                        .font(.callout)
                        .fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "bolt.fill")
                        .foregroundStyle(.orange)
                }
            }
            ForEach(action.disclosures, id: \.self) { disclosure in
                Label {
                    Text(disclosure)
                        .font(.callout)
                        .fixedSize(horizontal: false, vertical: true)
                } icon: {
                    Image(systemName: "info.circle")
                        .foregroundStyle(.secondary)
                }
            }
            HStack {
                Spacer()
                // Esc also cancels, via an invisible stand-in (a button can
                // carry only one keyboard shortcut).
                Button("", action: onCancel)
                    .keyboardShortcut(.cancelAction)
                    .hidden()
                    .frame(width: 0, height: 0)
                Button("Cancel", action: onCancel)
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                if action.isDestructive {
                    Button(action.verb, role: .destructive, action: onConfirm)
                        .buttonStyle(.bordered)
                } else {
                    Button(action.verb, action: onConfirm)
                        .buttonStyle(.bordered)
                }
            }
            .padding(.top, 4)
        }
        .padding(20)
        .frame(width: 380)
    }
}
