import SwiftUI

extension WarningTier {
    var color: Color {
        switch self {
        case .blocker: return .red
        case .warning: return .orange
        case .advisory: return .blue
        case .footnote: return .secondary
        }
    }

    var symbolName: String {
        switch self {
        case .blocker: return "xmark.octagon.fill"
        case .warning: return "exclamationmark.circle"
        case .advisory: return "info.circle"
        case .footnote: return "circle.dotted"
        }
    }
}

/// One banner style for the whole app (UX plan §5): one per screen maximum.
struct WarningBannerView: View {
    let item: WarningItem
    var action: (() -> Void)?

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: item.tier.symbolName)
                .foregroundStyle(item.tier.color)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title)
                    .font(.callout.weight(.medium))
                if let detail = item.detail {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 0)
            if let action {
                Button("Show", action: action)
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
        }
        .padding(10)
        .background(item.tier.color.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
        .padding(.horizontal)
        .padding(.top, 8)
    }
}

/// List content for the toolbar warnings badge popover.
struct WarningListView: View {
    let items: [WarningItem]
    var open: (SidebarDestination) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if items.isEmpty {
                Text("No active warnings")
                    .foregroundStyle(.secondary)
                    .padding()
            } else {
                ForEach(items) { item in
                    Button {
                        if let destination = item.destination { open(destination) }
                    } label: {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Image(systemName: item.tier.symbolName)
                                .foregroundStyle(item.tier.color)
                                .frame(width: 16)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.title).font(.callout)
                                if let detail = item.detail {
                                    Text(detail)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                }
                            }
                            Spacer(minLength: 0)
                        }
                        .contentShape(Rectangle())
                        .padding(.horizontal, 12)
                        .padding(.vertical, 6)
                    }
                    .buttonStyle(.plain)
                    .disabled(item.destination == nil)
                }
            }
        }
        .padding(.vertical, 6)
        .frame(width: 340)
    }
}

/// Thin capacity bar for used-% cells (UX plan §13).
struct UsageBarView: View {
    let percent: Double?

    var body: some View {
        if let percent {
            HStack(spacing: 6) {
                ProgressView(value: min(max(percent, 0), 100), total: 100)
                    .progressViewStyle(.linear)
                    .frame(width: 48)
                Text("\(Int(percent.rounded())) %")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
        } else {
            Text("—").foregroundStyle(.secondary)
        }
    }
}

/// Screens that arrive with later phases (UX plan scope control).
struct PlaceholderScreen: View {
    let destination: SidebarDestination

    var body: some View {
        ContentUnavailableView {
            Label(destination.title, systemImage: destination.symbolName)
        } description: {
            Text("This screen arrives with the workflow screens phase.")
        }
        .navigationTitle(destination.title)
    }
}
