import Foundation
import Darwin

/// A dedicated POSIX process group with nonblocking output. Only the collector
/// reaps the leader, after the last group signal, so its PID cannot be reused.
final class TKOwnedProcess: @unchecked Sendable {
    private let pid: pid_t
    private let output: Pipe
    private let errors: Pipe
    private let lock = NSLock()
    private var stopAt: TimeInterval?
    private var finished = false

    init(executable: URL, arguments: [String], environment: [String: String], directory: URL? = nil) throws {
        output = Pipe()
        errors = Pipe()
        var actions: posix_spawn_file_actions_t?
        var attributes: posix_spawnattr_t?
        posix_spawn_file_actions_init(&actions)
        posix_spawnattr_init(&attributes)
        defer {
            posix_spawn_file_actions_destroy(&actions)
            posix_spawnattr_destroy(&attributes)
        }
        var setupError: Int32 = 0
        func check(_ code: Int32) { if setupError == 0 { setupError = code } }
        check(posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETSIGMASK)))
        check(posix_spawnattr_setpgroup(&attributes, 0))
        var defaults = sigset_t(0), mask = sigset_t(0)
        sigemptyset(&defaults)
        for signal in [SIGINT, SIGTERM, SIGHUP, SIGPIPE] { sigaddset(&defaults, signal) }
        sigemptyset(&mask)
        check(posix_spawnattr_setsigdefault(&attributes, &defaults))
        check(posix_spawnattr_setsigmask(&attributes, &mask))
        check(posix_spawn_file_actions_addopen(&actions, STDIN_FILENO, "/dev/null", O_RDONLY, 0))
        check(posix_spawn_file_actions_adddup2(&actions, output.fileHandleForWriting.fileDescriptor, STDOUT_FILENO))
        check(posix_spawn_file_actions_adddup2(&actions, errors.fileHandleForWriting.fileDescriptor, STDERR_FILENO))
        if let directory {
            check(posix_spawn_file_actions_addchdir_np(&actions, directory.path))
        }
        let argv = ([executable.path] + arguments).map { strdup($0) } + [nil]
        let env = environment.sorted { $0.key < $1.key }.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer { for pointer in argv + env { free(pointer) } }
        var spawned: pid_t = 0
        let code = setupError == 0
            ? posix_spawn(&spawned, executable.path, &actions, &attributes, argv, env)
            : setupError
        guard code == 0 else {
            throw TKProcessError.launchFailed(path: executable.path, underlying: NSError(domain: NSPOSIXErrorDomain, code: Int(code)))
        }
        pid = spawned
        output.fileHandleForWriting.closeFile()
        errors.fileHandleForWriting.closeFile()
        for pipe in [output, errors] {
            let fd = pipe.fileHandleForReading.fileDescriptor
            _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)
        }
    }

    func stop(signal: Int32) {
        lock.lock()
        defer { lock.unlock() }
        guard !finished else { return }
        if stopAt == nil { stopAt = ProcessInfo.processInfo.systemUptime }
        _ = kill(-pid, signal)
    }

    func collect(timeout: TimeInterval?, onOutput: (@Sendable (Data) -> Void)? = nil)
        -> (result: TKProcessResult, timedOut: Bool) {
        let started = ProcessInfo.processInfo.systemUptime
        var stdout = Data(), stderr = Data()
        var outputOpen = true, errorOpen = true, timedOut = false
        var exitedAt: TimeInterval?
        var forcedAt: TimeInterval?
        func drain(_ pipe: Pipe, into data: inout Data, open: inout Bool, emit: Bool) {
            guard open else { return }
            var bytes = [UInt8](repeating: 0, count: 16384)
            // Bound each turn so continuous output cannot starve cancellation.
            for _ in 0..<64 {
                let count = Darwin.read(pipe.fileHandleForReading.fileDescriptor, &bytes, bytes.count)
                if count > 0 {
                    let chunk = Data(bytes.prefix(count))
                    if emit, let onOutput { onOutput(chunk) }
                    else { data.append(chunk) }
                } else if count == 0 {
                    open = false
                    break
                } else if errno == EINTR { continue }
                else {
                    if errno != EAGAIN && errno != EWOULDBLOCK { open = false }
                    break
                }
            }
        }
        while true {
            let now = ProcessInfo.processInfo.systemUptime
            drain(output, into: &stdout, open: &outputOpen, emit: true)
            drain(errors, into: &stderr, open: &errorOpen, emit: false)
            var info = siginfo_t()
            if waitid(P_PID, id_t(pid), &info, WEXITED | WNOHANG | WNOWAIT) == 0, info.si_pid == pid {
                if exitedAt == nil { exitedAt = now }
                if !outputOpen && !errorOpen { break }
            }
            if let timeout, now - started >= max(0, timeout), !timedOut {
                timedOut = true
                stop(signal: SIGTERM)
            }
            // A stream can wait for user input, but cannot hang on orphaned pipes.
            if let exitedAt, now - exitedAt >= TKProcessRunner.killGraceSeconds {
                stop(signal: SIGTERM)
            }
            lock.lock()
            let shouldKill = stopAt.map { now - $0 >= TKProcessRunner.killGraceSeconds } ?? false
            if shouldKill, forcedAt == nil {
                _ = kill(-pid, SIGKILL)
                forcedAt = now
            }
            lock.unlock()
            // Escaped descendants may keep descriptors: close our readers after
            // escalation rather than blocking forever waiting for their EOF.
            if let forcedAt, now - forcedAt >= 0.1 { break }
            usleep(10_000)
        }
        lock.lock()
        _ = kill(-pid, SIGKILL)
        finished = true
        lock.unlock()
        output.fileHandleForReading.closeFile()
        errors.fileHandleForReading.closeFile()
        var status: Int32 = 0
        while waitpid(pid, &status, 0) < 0 && errno == EINTR {}
        let signal = status & 0x7f
        let exitCode = signal == 0 ? (status >> 8) & 0xff : signal
        return (TKProcessResult(exitCode: exitCode, stdout: stdout, stderr: stderr), timedOut)
    }
}
