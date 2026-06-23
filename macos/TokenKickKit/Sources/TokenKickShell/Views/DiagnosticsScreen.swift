import SwiftUI
import TokenKickKit

/// Diagnostics screen: doctor summary, reset observations, environment and
/// provider CLI info, with copyable CLI equivalents (UX plan §8).
public struct DiagnosticsScreen: View {
    @Environment(DiagnosticsViewModel.self) private var diagnostics

    public init() {}

    public var body: some View {
        Group {
            switch diagnostics.phase {
            case .idle, .loading:
                ProgressView("Running diagnostics…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let message):
                ContentUnavailableView {
                    Label("Diagnostics unavailable", systemImage: "xmark.octagon")
                } description: {
                    Text(message)
                } actions: {
                    Button("Retry") { Task { await diagnostics.load() } }
                }
            case .loaded:
                form
            }
        }
        .task { await diagnostics.load() }
        .navigationTitle("Diagnostics")
    }

    private var form: some View {
        @Bindable var diagnostics = diagnostics
        return Form {
            doctorSection
            resetSection
            environmentSection
            providerSection
            cliSection
        }
        .formStyle(.grouped)
        .inspector(isPresented: Binding(
            get: { diagnostics.selectedReset != nil },
            set: { shown in
                if !shown { diagnostics.selectedResetID = nil }
            }
        )) {
            if let reset = diagnostics.selectedReset {
                resetDetail(reset)
                    .inspectorColumnWidth(min: 280, ideal: 340)
            }
        }
    }

    @ViewBuilder
    private var doctorSection: some View {
        Section {
            if let error = diagnostics.doctorError {
                Label(error, systemImage: "xmark.circle.fill")
                    .foregroundStyle(.red)
                    .font(.callout)
            } else if let summary = diagnostics.doctorSummary {
                // Counts come from the checks listed below, not the core's
                // summary — the header must match what the user can see.
                let counts = diagnostics.checkCounts
                HStack(spacing: 14) {
                    Label("\(counts.ok) ok", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Label("\(counts.warn) warnings", systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(counts.warn > 0 ? .orange : .secondary)
                    Label("\(counts.fail) failing", systemImage: "xmark.circle.fill")
                        .foregroundStyle(counts.fail > 0 ? .red : .secondary)
                    Spacer()
                    Text("\(summary.accounts) accounts")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .font(.callout)
                ForEach(diagnostics.attentionChecks) { check in
                    VStack(alignment: .leading, spacing: 2) {
                        Label {
                            Text(check.message).font(.callout)
                        } icon: {
                            Image(systemName: check.level == "FAIL" ? "xmark.circle.fill" : "exclamationmark.triangle.fill")
                                .foregroundStyle(check.level == "FAIL" ? Color.red : Color.orange)
                        }
                        if let fix = check.fix {
                            Text(fix)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .padding(.leading, 24)
                        }
                    }
                }
                if diagnostics.attentionChecks.isEmpty {
                    Text("Nothing needs attention.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                ForEach(diagnostics.infoChecks) { check in
                    Label {
                        Text(check.message)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } icon: {
                        Image(systemName: "info.circle")
                            .foregroundStyle(.secondary)
                    }
                }
            }
        } header: {
            HStack {
                Text("Health checks")
                Spacer()
                // A screen-local toolbar item can make AppKit register the
                // split-view separator twice while switching destinations.
                Button {
                    Task { await diagnostics.load() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help("Re-run diagnostics")
                .accessibilityLabel("Re-run diagnostics")
            }
        }
    }

    @ViewBuilder
    private var resetSection: some View {
        @Bindable var diagnostics = diagnostics
        Section("Reset observations") {
            if let error = diagnostics.resetError {
                Label(error, systemImage: "xmark.circle.fill")
                    .foregroundStyle(.red)
                    .font(.callout)
            } else if diagnostics.resetRows.isEmpty {
                Text("No provider resets observed.")
                    .foregroundStyle(.secondary)
            } else {
                ForEach(diagnostics.resetRows) { row in
                    HStack(spacing: 8) {
                        Button {
                            diagnostics.selectedResetID =
                                diagnostics.selectedResetID == row.id ? nil : row.id
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: "arrow.counterclockwise.circle")
                                    .foregroundStyle(row.acknowledged ? Color.secondary : Color.orange)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text("\(row.typeText) · \(row.provider.capitalized) · confidence: \(row.confidence)")
                                        .font(.callout)
                                    if let summary = row.summary {
                                        Text(summary)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                    }
                                }
                                Spacer()
                                if let date = row.detectedAt {
                                    Text(RelativeTimeText.ago(from: date, now: Date()))
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        if !row.acknowledged {
                            if diagnostics.ackingResetID == row.id {
                                ProgressView().controlSize(.small)
                            } else {
                                Button("Acknowledge") {
                                    Task { await diagnostics.acknowledgeReset(id: row.id) }
                                }
                                .controlSize(.small)
                                .disabled(diagnostics.ackingResetID != nil)
                            }
                        }
                    }
                }
            }
        }
    }

    private var environmentSection: some View {
        Section("Environment") {
            ForEach(diagnostics.environmentFields, id: \.key) { field in
                LabeledContent(field.key.replacingOccurrences(of: "_", with: " ")) {
                    Text(field.value)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
            ForEach(diagnostics.stateFields, id: \.key) { field in
                LabeledContent(field.key.replacingOccurrences(of: "_", with: " ")) {
                    Text(field.value)
                        .font(.caption.monospaced())
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .truncationMode(.middle)
                        .lineLimit(1)
                }
            }
        }
    }

    private var providerSection: some View {
        Section("Provider CLIs") {
            ForEach(diagnostics.providerCLIs, id: \.name) { cli in
                LabeledContent(cli.name) {
                    if cli.found, let path = cli.path {
                        Label {
                            Text(path)
                                .font(.caption.monospaced())
                                .truncationMode(.middle)
                                .lineLimit(1)
                        } icon: {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                        }
                    } else {
                        Label("not found", systemImage: "questionmark.circle")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private var cliSection: some View {
        Section {
            ForEach(DiagnosticsViewModel.cliEquivalents, id: \.command) { item in
                HStack {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(item.title).font(.callout)
                        Text(item.command)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button {
                        NSPasteboard.general.clearContents()
                        NSPasteboard.general.setString(item.command, forType: .string)
                    } label: {
                        Image(systemName: "doc.on.doc")
                    }
                    .buttonStyle(.borderless)
                    .help("Copy command")
                }
            }
        } header: {
            Text("Equivalent commands")
        } footer: {
            Text("The same data is available from a terminal — useful for bug reports.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func resetDetail(_ row: ResetEventRow) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                Label(row.typeText, systemImage: "arrow.counterclockwise.circle")
                    .font(.headline)
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
        .navigationTitle(row.provider.capitalized)
    }
}
