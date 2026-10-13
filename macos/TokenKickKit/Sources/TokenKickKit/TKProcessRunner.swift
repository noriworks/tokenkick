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

/// The deadline includes child processes and pipe draining.
public struct TKProcessRunner: Sendable {
    public var timeout: TimeInterval
    public static let killGraceSeconds: TimeInterval = 2.0
    public init(timeout: TimeInterval = 60) { self.timeout = timeout }

    public func run(executable: URL, arguments: [String], environment: [String: String],
                    currentDirectory: URL? = nil) async throws -> TKProcessResult {
        let child = try TKOwnedProcess(executable: executable, arguments: arguments,
                                       environment: environment, directory: currentDirectory)
        let task = Task.detached(priority: .utility) {
            child.collect(timeout: timeout)
        }
        let outcome = await withTaskCancellationHandler {
            await task.value
        } onCancel: {
            child.stop(signal: SIGTERM)
        }
        if Task.isCancelled { throw TKProcessError.cancelled }
        if outcome.timedOut {
            throw TKProcessError.timedOut(seconds: timeout, stderr: outcome.result.stderrText)
        }
        return outcome.result
    }
}
