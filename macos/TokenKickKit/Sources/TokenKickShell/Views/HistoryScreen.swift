import SwiftUI
import TokenKickKit

/// History screen (UX plan §13): reverse-chronological kick list, account
/// filter only in v1, verbose verification fields in the inspector.
public struct HistoryScreen: View {
    @Environment(HistoryViewModel.self) private var history

    public init() {}

    public var body: some View {
        @Bindable var history = history
        Group {
            switch history.phase {
            case .idle, .loading:
                ProgressView("Loading history…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                ContentUnavailableView {
                    Label("Couldn't read history", systemImage: "xmark.octagon")
                } description: {
                    Text(message)
                } actions: {
                    Button("Retry") { Task { await history.load() } }
                }
            case .loaded:
                if history.rows.isEmpty {
                    ContentUnavailableView {
                        Label("No kicks yet", systemImage: "clock.arrow.circlepath")
                    } description: {
                        Text("Kicks will appear here once the background service starts working.")
                    }
                } else {
                    eventList
                }
            }
        }
        .toolbar {
            ToolbarItem {
                Picker("Account", selection: $history.accountFilter) {
                    Text("All accounts").tag(String?.none)
                    ForEach(history.accountLabels, id: \.self) { label in
                        Text(label).tag(String?.some(label))
                    }
                }
                .pickerStyle(.menu)
            }
            ToolbarItem {
                Button {
                    Task { await history.load() }
                } label: {
                    Label("Reload history", systemImage: "arrow.clockwise")
                }
                .help("Reload history")
            }
        }
        .inspector(isPresented: Binding(
            get: { history.selectedRow != nil },
            set: { shown in
                if !shown { history.selectedID = nil }
            }
        )) {
            if let row = history.selectedRow {
                eventDetail(row)
                    .inspectorColumnWidth(min: 280, ideal: 340)
            }
        }
        .task { await history.load() }
        .navigationTitle("History")
    }

    private var eventList: some View {
        @Bindable var history = history
        return List(selection: $history.selectedID) {
            ForEach(history.filteredRows) { row in
                HStack(spacing: 8) {
                    Image(systemName: row.symbolName)
                        .foregroundStyle(outcomeColor(row))
                        .frame(width: 18)
                    VStack(alignment: .leading, spacing: 1) {
                        HStack(spacing: 6) {
                            Text(row.label).truncationMode(.middle).lineLimit(1)
                            if let kickType = row.kickType {
                                Text(kickType)
                                    .font(.caption2)
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 1)
                                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 3))
                            }
                        }
                        Text(row.resultText + (row.errorText.map { " · \($0)" } ?? ""))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    Spacer()
                    if let date = row.date {
                        Text(RelativeTimeText.ago(from: date, now: Date()))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(.secondary)
                            .help(date.formatted(date: .abbreviated, time: .shortened))
                    }
                }
                .tag(row.id)
                .padding(.vertical, 2)
            }
        }
    }

    private func eventDetail(_ row: HistoryEventRow) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Label(row.resultText, systemImage: row.symbolName)
                    .font(.headline)
                    .foregroundStyle(outcomeColor(row))
                if let date = row.date {
                    Text(date.formatted(date: .abbreviated, time: .standard))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Divider()
                Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 10, verticalSpacing: 4) {
                    ForEach(row.detailFields, id: \.key) { field in
                        GridRow {
                            Text(field.key)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                                .gridColumnAlignment(.trailing)
                            Text(field.value)
                                .font(.caption)
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
            }
            .padding(12)
        }
        .navigationTitle(row.label)
    }

    private func outcomeColor(_ row: HistoryEventRow) -> Color {
        if row.kind == "probe" || row.kind == "status_probe" { return .secondary }
        if row.success && row.confirmed { return .green }
        if row.success { return .orange }
        return .red
    }
}
