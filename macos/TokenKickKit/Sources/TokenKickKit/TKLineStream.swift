import Foundation

/// A running process whose stdout is consumed line by line — the transport
/// for `tk app setup --json-lines`. Cancellation is cooperative and
/// graceful: `interrupt()` sends SIGINT so the core can end the stream with
/// its own terminal record (`setup_cancelled`), and reading continues to
/// EOF so that record is never lost.
public final class TKLineStream: @unchecked Sendable {
    public let lines: AsyncThrowingStream<String, Error>

    private let process: Process
    private let exitWaiter: ExitWaiter
    private let stderrTask: Task<Data, Never>

    init(process: Process, exitWaiter: ExitWaiter, lines: AsyncThrowingStream<String, Error>, stderrTask: Task<Data, Never>) {
        self.process = process
        self.exitWaiter = exitWaiter
        self.lines = lines
        self.stderrTask = stderrTask
    }

    /// Ask the process to stop gracefully (SIGINT). Safe to call once or
    /// repeatedly, before or after exit.
    public func interrupt() {
        guard process.isRunning else { return }
        process.interrupt()
    }

    /// Force-stop: SIGTERM, short grace, SIGKILL. Used as the escalation
    /// path when a graceful interrupt is not honored.
    public func terminate() {
        guard process.isRunning else { return }
        process.terminate()
    }

    /// Wait for the process to exit and return (exit code, stderr).
    public func waitForExit() async -> (exitCode: Int32, stderr: String) {
        await exitWaiter.wait()
        let stderrData = await stderrTask.value
        return (process.terminationStatus, String(decoding: stderrData, as: UTF8.self))
    }
}

extension TKProcessRunner {
    /// Launch a process for line-by-line stdout consumption. The caller owns
    /// the lifetime via the returned handle; there is no implicit timeout —
    /// streams like setup may legitimately run for minutes.
    public func streamLines(
        executable: URL,
        arguments: [String],
        environment: [String: String]
    ) throws -> TKLineStream {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        let stdoutPipe = Pipe()
        let stderrPipe = Pipe()
        process.standardOutput = stdoutPipe
        process.standardError = stderrPipe
        process.standardInput = FileHandle.nullDevice

        let exitWaiter = ExitWaiter()
        process.terminationHandler = { _ in exitWaiter.markExited() }

        do {
            try process.run()
        } catch {
            throw TKProcessError.launchFailed(path: executable.path, underlying: error)
        }

        let stderrTask = Task.detached(priority: .utility) {
            stderrPipe.fileHandleForReading.readDataToEndOfFile()
        }

        let lines = AsyncThrowingStream<String, Error> { continuation in
            let reader = Task.detached(priority: .userInitiated) {
                let handle = stdoutPipe.fileHandleForReading
                var buffer = Data()
                while true {
                    let chunk = handle.availableData
                    if chunk.isEmpty { break }
                    buffer.append(chunk)
                    while let newlineIndex = buffer.firstIndex(of: UInt8(ascii: "\n")) {
                        let lineData = buffer[buffer.startIndex..<newlineIndex]
                        buffer.removeSubrange(buffer.startIndex...newlineIndex)
                        let line = String(decoding: lineData, as: UTF8.self)
                        if !line.trimmingCharacters(in: .whitespaces).isEmpty {
                            continuation.yield(line)
                        }
                    }
                }
                if !buffer.isEmpty {
                    let line = String(decoding: buffer, as: UTF8.self)
                    if !line.trimmingCharacters(in: .whitespaces).isEmpty {
                        continuation.yield(line)
                    }
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in
                reader.cancel()
            }
        }

        return TKLineStream(
            process: process,
            exitWaiter: exitWaiter,
            lines: lines,
            stderrTask: stderrTask
        )
    }
}
