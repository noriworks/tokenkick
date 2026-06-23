import Foundation

public protocol TKLaunchctl {
    func run(_ arguments: [String]) async throws -> TKProcessResult
}

public struct TKSystemLaunchctl: TKLaunchctl {
    public let runner: TKProcessRunner
    public let executable: URL

    public init(
        executable: URL = URL(fileURLWithPath: "/bin/launchctl"),
        timeout: TimeInterval = 30
    ) {
        self.executable = executable
        self.runner = TKProcessRunner(timeout: timeout)
    }

    public func run(_ arguments: [String]) async throws -> TKProcessResult {
        try await runner.run(executable: executable, arguments: arguments, environment: [:])
    }
}

public protocol TKDaemonCommanding {
    func stopDaemon() async throws -> TKEnvelope<TKDaemonActionPayload>
}

extension TKClient: TKDaemonCommanding {}

public enum TKDaemonOwnership: Equatable, Sendable {
    case notRunning
    case appManaged
    case terminalManaged
    case stalePidfile
    case unknownRunning
}

public struct TKLaunchAgentStatus: Equatable, Sendable {
    public let label: String
    public let installed: Bool
    public let loaded: Bool
    public let helperURL: URL
    public let plistURL: URL
    public let runtimePathURL: URL
    public let configuredRuntime: String?
    public let runtimePathMatches: Bool
    public let plistProgramMatchesHelper: Bool
    public let needsRepair: Bool
    public let daemonOwnership: TKDaemonOwnership
    public let stalePidfile: Bool
    public let versionMismatch: Bool
    public let executablePathMismatch: Bool

    public init(
        label: String,
        installed: Bool,
        loaded: Bool,
        helperURL: URL,
        plistURL: URL,
        runtimePathURL: URL,
        configuredRuntime: String?,
        runtimePathMatches: Bool,
        plistProgramMatchesHelper: Bool,
        needsRepair: Bool,
        daemonOwnership: TKDaemonOwnership,
        stalePidfile: Bool,
        versionMismatch: Bool,
        executablePathMismatch: Bool
    ) {
        self.label = label
        self.installed = installed
        self.loaded = loaded
        self.helperURL = helperURL
        self.plistURL = plistURL
        self.runtimePathURL = runtimePathURL
        self.configuredRuntime = configuredRuntime
        self.runtimePathMatches = runtimePathMatches
        self.plistProgramMatchesHelper = plistProgramMatchesHelper
        self.needsRepair = needsRepair
        self.daemonOwnership = daemonOwnership
        self.stalePidfile = stalePidfile
        self.versionMismatch = versionMismatch
        self.executablePathMismatch = executablePathMismatch
    }
}

public enum TKLaunchAgentError: Error, CustomStringConvertible {
    case takeoverRequired(pid: Int?, executable: String?)
    case takeoverStopFailed(message: String)
    case launchctlFailed(arguments: [String], exitCode: Int32, stderr: String)

    public var description: String {
        switch self {
        case .takeoverRequired(let pid, let executable):
            let pidText = pid.map(String.init) ?? "unknown"
            return "A non-app TokenKick daemon is already running (pid \(pidText), executable \(executable ?? "unknown"))."
        case .takeoverStopFailed(let message):
            return "Could not stop existing TokenKick daemon for takeover: \(message)"
        case .launchctlFailed(let arguments, let exitCode, let stderr):
            return "launchctl \(arguments.joined(separator: " ")) failed with \(exitCode): \(stderr)"
        }
    }
}

public struct TKLaunchAgentManager {
    public static let label = "com.tokenkick.daemon"
    public static let appSupportDirectoryName = "TokenKick"
    public static let helperName = "tokenkick-daemon-helper.sh"
    public static let runtimePathFileName = "runtime-path"
    public static let plistName = "com.tokenkick.daemon.plist"

    public let runtime: URL
    public let home: URL
    public let launchctl: any TKLaunchctl
    public let daemonClient: (any TKDaemonCommanding)?
    public let fileManager: FileManager

    public init(
        runtime: URL,
        home: URL = URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true),
        launchctl: any TKLaunchctl = TKSystemLaunchctl(),
        daemonClient: (any TKDaemonCommanding)? = nil,
        fileManager: FileManager = .default
    ) {
        self.runtime = runtime
        self.home = home
        self.launchctl = launchctl
        self.daemonClient = daemonClient
        self.fileManager = fileManager
    }

    public var label: String { Self.label }
    public var launchDomain: String { "gui/\(getuid())" }
    public var serviceTarget: String { "\(launchDomain)/\(label)" }

    public var appSupportDirectory: URL {
        home
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("Application Support", isDirectory: true)
            .appendingPathComponent(Self.appSupportDirectoryName, isDirectory: true)
    }

    public var launchAgentsDirectory: URL {
        home
            .appendingPathComponent("Library", isDirectory: true)
            .appendingPathComponent("LaunchAgents", isDirectory: true)
    }

    public var helperURL: URL {
        appSupportDirectory.appendingPathComponent(Self.helperName, isDirectory: false)
    }

    public var runtimePathURL: URL {
        appSupportDirectory.appendingPathComponent(Self.runtimePathFileName, isDirectory: false)
    }

    public var plistURL: URL {
        launchAgentsDirectory.appendingPathComponent(Self.plistName, isDirectory: false)
    }

    public func status(daemon: TKDaemonStatus? = nil) async -> TKLaunchAgentStatus {
        let installed = fileManager.fileExists(atPath: plistURL.path)
        let helperExists = fileManager.fileExists(atPath: helperURL.path)
        let loaded = await launchAgentLoaded()
        let configuredRuntime = readConfiguredRuntime()
        let runtimePathMatches = configuredRuntime.map { pathsMatch($0, runtime.path) } ?? false
        let plistProgramMatchesHelper = readPlistProgramArgument().map { pathsMatch($0, helperURL.path) } ?? false
        let ownership = classifyDaemon(daemon)
        let executableMismatch = daemon?.running == true && daemon?.executableMatch == false
        let versionMismatch = daemon?.running == true && daemon?.versionMatch == false
        let stalePidfile = daemon?.stalePidfile == true
        let needsRepair = !installed || !helperExists || !runtimePathMatches || !plistProgramMatchesHelper
        return TKLaunchAgentStatus(
            label: label,
            installed: installed,
            loaded: loaded,
            helperURL: helperURL,
            plistURL: plistURL,
            runtimePathURL: runtimePathURL,
            configuredRuntime: configuredRuntime,
            runtimePathMatches: runtimePathMatches,
            plistProgramMatchesHelper: plistProgramMatchesHelper,
            needsRepair: needsRepair,
            daemonOwnership: ownership,
            stalePidfile: stalePidfile,
            versionMismatch: versionMismatch,
            executablePathMismatch: executableMismatch
        )
    }

    @discardableResult
    public func install() throws -> TKLaunchAgentStatus {
        try writeManagedFiles()
        return installedStatusWithoutLaunchctl()
    }

    @discardableResult
    public func repair() throws -> TKLaunchAgentStatus {
        try writeManagedFiles()
        return installedStatusWithoutLaunchctl()
    }

    public func start(
        daemon: TKDaemonStatus? = nil,
        takeover: Bool = false
    ) async throws -> TKLaunchAgentStatus {
        if classifyDaemon(daemon) == .appManaged {
            try writeManagedFiles()
            return await status(daemon: daemon)
        }
        try await prepareForStart(daemon: daemon, takeover: takeover)
        try writeManagedFiles()
        let bootstrap = try await launchctl.run(["bootstrap", launchDomain, plistURL.path])
        if bootstrap.exitCode != 0, !isAlreadyBootstrapped(bootstrap.stderrText) {
            throw TKLaunchAgentError.launchctlFailed(
                arguments: ["bootstrap", launchDomain, plistURL.path],
                exitCode: bootstrap.exitCode,
                stderr: bootstrap.stderrText
            )
        }
        let kickstart = try await launchctl.run(["kickstart", "-k", serviceTarget])
        if kickstart.exitCode != 0 {
            throw TKLaunchAgentError.launchctlFailed(
                arguments: ["kickstart", "-k", serviceTarget],
                exitCode: kickstart.exitCode,
                stderr: kickstart.stderrText
            )
        }
        return await status()
    }

    public func stop() async throws -> TKLaunchAgentStatus {
        let result = try await launchctl.run(["bootout", serviceTarget])
        if result.exitCode != 0, !isNotLoaded(result.stderrText) {
            throw TKLaunchAgentError.launchctlFailed(
                arguments: ["bootout", serviceTarget],
                exitCode: result.exitCode,
                stderr: result.stderrText
            )
        }
        return await status()
    }

    public func remove() async throws -> TKLaunchAgentStatus {
        _ = try? await launchctl.run(["bootout", serviceTarget])
        for url in [plistURL, helperURL, runtimePathURL] where fileManager.fileExists(atPath: url.path) {
            try fileManager.removeItem(at: url)
        }
        return await status()
    }

    public func plistDictionary() -> [String: Any] {
        [
            "Label": label,
            "ProgramArguments": [helperURL.path],
            "RunAtLoad": true,
            "KeepAlive": false,
            "StandardOutPath": appSupportDirectory
                .appendingPathComponent("launchd.out.log", isDirectory: false)
                .path,
            "StandardErrorPath": appSupportDirectory
                .appendingPathComponent("launchd.err.log", isDirectory: false)
                .path,
            "EnvironmentVariables": [
                "PATH": TKEnvironment.augmentedPath(
                    basePath: "/usr/bin:/bin:/usr/sbin:/sbin",
                    home: home.path
                ),
            ],
        ]
    }

    public func helperScript() -> String {
        """
        #!/bin/sh
        set -eu
        HELPER_DIR="$(CDPATH= cd "$(dirname "$0")" && pwd)"
        RUNTIME_FILE="$HELPER_DIR/\(Self.runtimePathFileName)"
        if [ ! -r "$RUNTIME_FILE" ]; then
          echo "TokenKick runtime path file missing: $RUNTIME_FILE" >&2
          exit 78
        fi
        RUNTIME="$(cat "$RUNTIME_FILE")"
        if [ ! -x "$RUNTIME" ]; then
          echo "TokenKick runtime is not executable: $RUNTIME" >&2
          exit 78
        fi
        exec "$RUNTIME" daemon
        """
    }

    private func writeManagedFiles() throws {
        try fileManager.createDirectory(
            at: appSupportDirectory,
            withIntermediateDirectories: true
        )
        try fileManager.createDirectory(
            at: launchAgentsDirectory,
            withIntermediateDirectories: true
        )
        try (runtime.path + "\n").write(to: runtimePathURL, atomically: true, encoding: .utf8)
        try helperScript().write(to: helperURL, atomically: true, encoding: .utf8)
        try fileManager.setAttributes([.posixPermissions: 0o755], ofItemAtPath: helperURL.path)
        let data = try PropertyListSerialization.data(
            fromPropertyList: plistDictionary(),
            format: .xml,
            options: 0
        )
        try data.write(to: plistURL, options: .atomic)
    }

    private func installedStatusWithoutLaunchctl() -> TKLaunchAgentStatus {
        let installed = fileManager.fileExists(atPath: plistURL.path)
        let helperExists = fileManager.fileExists(atPath: helperURL.path)
        let configuredRuntime = readConfiguredRuntime()
        let runtimePathMatches = configuredRuntime.map { pathsMatch($0, runtime.path) } ?? false
        let plistProgramMatchesHelper = readPlistProgramArgument().map { pathsMatch($0, helperURL.path) } ?? false
        let needsRepair = !installed || !helperExists || !runtimePathMatches || !plistProgramMatchesHelper
        return TKLaunchAgentStatus(
            label: label,
            installed: installed,
            loaded: false,
            helperURL: helperURL,
            plistURL: plistURL,
            runtimePathURL: runtimePathURL,
            configuredRuntime: configuredRuntime,
            runtimePathMatches: runtimePathMatches,
            plistProgramMatchesHelper: plistProgramMatchesHelper,
            needsRepair: needsRepair,
            daemonOwnership: .notRunning,
            stalePidfile: false,
            versionMismatch: false,
            executablePathMismatch: false
        )
    }

    private func prepareForStart(daemon: TKDaemonStatus?, takeover: Bool) async throws {
        let ownership = classifyDaemon(daemon)
        guard ownership == .terminalManaged || ownership == .unknownRunning else { return }
        guard takeover else {
            throw TKLaunchAgentError.takeoverRequired(
                pid: daemon?.pid,
                executable: daemon?.executable
            )
        }
        guard let daemonClient else {
            throw TKLaunchAgentError.takeoverStopFailed(message: "No daemon client is configured.")
        }
        let envelope = try await daemonClient.stopDaemon()
        if !envelope.ok || envelope.payload?.daemon.running == true {
            throw TKLaunchAgentError.takeoverStopFailed(
                message: envelope.message ?? "Existing daemon is still running."
            )
        }
    }

    private func classifyDaemon(_ daemon: TKDaemonStatus?) -> TKDaemonOwnership {
        guard let daemon else { return .notRunning }
        if daemon.stalePidfile { return .stalePidfile }
        guard daemon.running else { return .notRunning }
        guard let executable = daemon.executable else { return .unknownRunning }
        return pathsMatch(executable, runtime.path) ? .appManaged : .terminalManaged
    }

    private func launchAgentLoaded() async -> Bool {
        do {
            let result = try await launchctl.run(["print", serviceTarget])
            return result.exitCode == 0
        } catch {
            return false
        }
    }

    private func readConfiguredRuntime() -> String? {
        guard let text = try? String(contentsOf: runtimePathURL, encoding: .utf8) else { return nil }
        let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.isEmpty ? nil : value
    }

    private func readPlistProgramArgument() -> String? {
        guard
            let data = try? Data(contentsOf: plistURL),
            let plist = try? PropertyListSerialization.propertyList(
                from: data,
                options: [],
                format: nil
            ) as? [String: Any],
            let arguments = plist["ProgramArguments"] as? [String],
            let first = arguments.first
        else {
            return nil
        }
        return first
    }

    private func pathsMatch(_ first: String, _ second: String) -> Bool {
        URL(fileURLWithPath: first).standardizedFileURL.path
            == URL(fileURLWithPath: second).standardizedFileURL.path
    }

    private func isAlreadyBootstrapped(_ stderr: String) -> Bool {
        let text = stderr.lowercased()
        return text.contains("already") || text.contains("service is already loaded")
    }

    private func isNotLoaded(_ stderr: String) -> Bool {
        let text = stderr.lowercased()
        return text.contains("could not find service")
            || text.contains("no such process")
            || text.contains("not loaded")
    }
}
