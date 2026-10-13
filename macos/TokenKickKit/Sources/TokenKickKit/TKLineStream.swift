import Foundation

/// Streaming setup output with graceful interrupt and bounded escalation.
public final class TKLineStream: @unchecked Sendable {
    public let lines: AsyncThrowingStream<String, Error>
    private let child: TKOwnedProcess
    private let completion: Task<TKProcessResult, Never>

    init(child: TKOwnedProcess) {
        self.child = child
        var continuation: AsyncThrowingStream<String, Error>.Continuation!
        self.lines = AsyncThrowingStream { continuation = $0 }
        let sink = continuation!
        self.completion = Task.detached(priority: .utility) {
            let buffer = LineBuffer(sink: sink)
            let outcome = child.collect(timeout: nil) { buffer.append($0) }
            buffer.finish()
            sink.finish()
            return outcome.result
        }
        sink.onTermination = { termination in
            if case .cancelled = termination { child.stop(signal: SIGTERM) }
        }
    }

    deinit { child.stop(signal: SIGTERM) }
    public func interrupt() { child.stop(signal: SIGINT) }
    public func terminate() { child.stop(signal: SIGTERM) }
    public func waitForExit() async -> (exitCode: Int32, stderr: String) {
        let result = await withTaskCancellationHandler {
            await completion.value
        } onCancel: { child.stop(signal: SIGTERM) }
        return (result.exitCode, result.stderrText)
    }
}

// Used exclusively by the collector thread.
private final class LineBuffer: @unchecked Sendable {
    var buffer = Data()
    let sink: AsyncThrowingStream<String, Error>.Continuation
    init(sink: AsyncThrowingStream<String, Error>.Continuation) { self.sink = sink }
    func append(_ chunk: Data) {
        buffer.append(chunk)
        while let newline = buffer.firstIndex(of: UInt8(ascii: "\n")) {
            emit(Data(buffer[..<newline]))
            buffer.removeSubrange(...newline)
        }
    }
    func emit(_ data: Data) {
        let line = String(decoding: data, as: UTF8.self)
        if !line.trimmingCharacters(in: .whitespaces).isEmpty { sink.yield(line) }
    }
    func finish() { if !buffer.isEmpty { emit(buffer) } }
}

extension TKProcessRunner {
    public func streamLines(executable: URL, arguments: [String], environment: [String: String]) throws -> TKLineStream {
        TKLineStream(child: try TKOwnedProcess(executable: executable, arguments: arguments, environment: environment))
    }
}
