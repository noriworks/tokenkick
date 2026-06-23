import Foundation

public struct TKProcessResult: Sendable {
    public let exitCode: Int32
    public let stdout: Data
    public let stderr: Data

    public var stdoutText: String { String(decoding: stdout, as: UTF8.self) }
    public var stderrText: String { String(decoding: stderr, as: UTF8.self) }
}

public enum TKProcessError: Error, CustomStringConvertible {
    case launchFailed(path: String, underlying: Error)
    case timedOut(seconds: TimeInterval, stderr: String)
    case cancelled

    public var description: String {
        switch self {
        case .launchFailed(let path, let underlying):
            return "Could not launch \(path): \(underlying)"
        case .timedOut(let seconds, _):
            return "tk did not finish within \(seconds)s"
        case .cancelled:
            return "tk invocation was cancelled"
        }
    }
}

/// Runs one `tk` invocation with a timeout and cooperative cancellation.
/// On timeout or cancellation the process gets SIGTERM, then SIGKILL after a
/// short grace period, and is always reaped before the call returns.
public struct TKProcessRunner: Sendable {
    public var timeout: TimeInterval
    public static let killGraceSeconds: TimeInterval = 2.0

    public init(timeout: TimeInterval = 60) {
        self.timeout = timeout
    }

    public func run(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        currentDirectory: URL? = nil
    ) async throws -> TKProcessResult {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        if let currentDirectory {
            process.currentDirectoryURL = currentDirectory
        }
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

        let stdoutTask = Task.detached(priority: .utility) {
            stdoutPipe.fileHandleForReading.readDataToEndOfFile()
        }
        let stderrTask = Task.detached(priority: .utility) {
            stderrPipe.fileHandleForReading.readDataToEndOfFile()
        }

        let timeoutSeconds = timeout
        let exitedInTime = await withTaskCancellationHandler {
            await Self.waitForExit(exitWaiter, timeout: timeoutSeconds)
        } onCancel: {
            process.terminate()
        }

        if Task.isCancelled {
            await Self.forceStop(process)
            stdoutTask.cancel()
            stderrTask.cancel()
            throw TKProcessError.cancelled
        }

        if !exitedInTime {
            await Self.forceStop(process)
            let stderrData = await stderrTask.value
            stdoutTask.cancel()
            throw TKProcessError.timedOut(
                seconds: timeoutSeconds,
                stderr: String(decoding: stderrData, as: UTF8.self)
            )
        }

        let stdoutData = await stdoutTask.value
        let stderrData = await stderrTask.value
        return TKProcessResult(
            exitCode: process.terminationStatus,
            stdout: stdoutData,
            stderr: stderrData
        )
    }

    private static func waitForExit(_ waiter: ExitWaiter, timeout: TimeInterval) async -> Bool {
        await withTaskGroup(of: Bool.self) { group in
            group.addTask {
                await waiter.wait()
                return true
            }
            group.addTask {
                do {
                    try await Task.sleep(nanoseconds: UInt64(max(0, timeout) * 1_000_000_000))
                    return false
                } catch {
                    return false
                }
            }
            let first = await group.next() ?? false
            // The group only returns once every child finishes, so both
            // children must respond to cancelAll() — ExitWaiter.wait() is
            // cancellation-aware for exactly this reason.
            group.cancelAll()
            return first
        }
    }

    /// SIGTERM, short grace, SIGKILL, reap. Runs detached so it cannot be
    /// cancelled and may block while waiting on the dying process.
    private static func forceStop(_ process: Process) async {
        guard process.isRunning else { return }
        await Task.detached(priority: .utility) {
            process.terminate()
            let graceDeadline = Date().addingTimeInterval(killGraceSeconds)
            while process.isRunning, Date() < graceDeadline {
                usleep(50_000)
            }
            if process.isRunning {
                kill(process.processIdentifier, SIGKILL)
            }
            process.waitUntilExit()
        }.value
    }
}

/// Bridges Process.terminationHandler to async/await without losing an exit
/// that fires before anyone awaits, and without wedging task groups: a
/// cancelled wait() resumes immediately instead of holding the group open.
final class ExitWaiter: @unchecked Sendable {
    private let lock = NSLock()
    private var exited = false
    private var continuations: [UUID: CheckedContinuation<Void, Never>] = [:]

    func markExited() {
        lock.lock()
        exited = true
        let waiting = continuations
        continuations = [:]
        lock.unlock()
        for continuation in waiting.values {
            continuation.resume()
        }
    }

    func wait() async {
        let id = UUID()
        await withTaskCancellationHandler {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                lock.lock()
                if exited || Task.isCancelled {
                    lock.unlock()
                    continuation.resume()
                } else {
                    continuations[id] = continuation
                    lock.unlock()
                }
            }
        } onCancel: {
            lock.lock()
            let continuation = continuations.removeValue(forKey: id)
            lock.unlock()
            continuation?.resume()
        }
    }
}
