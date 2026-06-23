import Foundation

/// Daemon state as reported by `tk daemon --status --json-output` and the
/// `daemon` section of `tk app snapshot`.
public struct TKDaemonStatus: Decodable, Sendable {
    public let running: Bool
    public let pid: Int?
    public let version: String?
    public let executable: String?
    public let installedVersion: String
    public let versionMatch: Bool?
    public let executableMatch: Bool?
    public let pidfileExists: Bool
    public let stalePidfile: Bool
    public let uptimeSeconds: Int?
    public let pollIntervalMinutes: Int
    public let pidfilePath: String
    public let logPath: String

    enum CodingKeys: String, CodingKey {
        case running
        case pid
        case version
        case executable
        case installedVersion = "installed_version"
        case versionMatch = "version_match"
        case executableMatch = "executable_match"
        case pidfileExists = "pidfile_exists"
        case stalePidfile = "stale_pidfile"
        case uptimeSeconds = "uptime_seconds"
        case pollIntervalMinutes = "poll_interval_minutes"
        case pidfilePath = "pidfile_path"
        case logPath = "log_path"
    }
}

/// Payload of `tk daemon --status --json-output`.
public struct TKDaemonEnvelopePayload: Decodable, Sendable {
    public let daemon: TKDaemonStatus
}

/// Payload of daemon mutation envelopes such as
/// `tk daemon --stop --json-output`.
public struct TKDaemonActionPayload: Decodable, Sendable {
    public let action: String
    public let daemon: TKDaemonStatus
    public let started: Bool?
    public let stopped: Bool?
    public let restarted: Bool?
    public let alreadyRunning: Bool?
    public let wasRunning: Bool?

    enum CodingKeys: String, CodingKey {
        case action
        case daemon
        case started
        case stopped
        case restarted
        case alreadyRunning = "already_running"
        case wasRunning = "was_running"
    }
}

/// Payload of `tk app snapshot`. Sections the prototype does not consume yet
/// stay generic (`TKJSONValue`) on purpose; they become typed with the
/// screens that use them.
public struct TKSnapshotPayload: Decodable, Sendable {
    public struct Core: Decodable, Sendable {
        public let version: String
        public let executable: String?
        public let pythonExecutable: String?
        public let pythonVersion: String?
        public let appMode: Bool

        enum CodingKeys: String, CodingKey {
            case version
            case executable
            case pythonExecutable = "python_executable"
            case pythonVersion = "python_version"
            case appMode = "app_mode"
        }
    }

    public struct ExternalTk: Decodable, Sendable {
        public let path: String
        public let isCurrentRuntime: Bool
        public let version: String?

        enum CodingKeys: String, CodingKey {
            case path
            case isCurrentRuntime = "is_current_runtime"
            case version
        }
    }

    public struct Runtime: Decodable, Sendable {
        public let externalTk: ExternalTk?

        enum CodingKeys: String, CodingKey {
            case externalTk = "external_tk"
        }
    }

    public struct StatusSection: Decodable, Sendable {
        public let cached: Bool
        public let cachedAt: String?
        public let refreshError: String?
        public let refreshInProgress: Bool
        public let accounts: [TKJSONValue]

        enum CodingKeys: String, CodingKey {
            case cached
            case cachedAt = "cached_at"
            case refreshError = "refresh_error"
            case refreshInProgress = "refresh_in_progress"
            case accounts
        }
    }

    public struct UpdateStatus: Decodable, Sendable {
        public let installedVersion: String
        public let daemonVersion: String?
        public let daemonRunning: Bool
        public let match: Bool
        public let daemonPid: Int?

        enum CodingKeys: String, CodingKey {
            case installedVersion = "installed_version"
            case daemonVersion = "daemon_version"
            case daemonRunning = "daemon_running"
            case match
            case daemonPid = "daemon_pid"
        }
    }

    public let generatedAt: String
    public let core: Core
    public let runtime: Runtime
    public let paths: [String: String]
    public let daemon: TKDaemonStatus
    public let status: StatusSection
    public let pendingKicks: [TKJSONValue]
    public let schedule: TKJSONValue
    public let advisories: [TKJSONValue]
    public let resetObservations: [TKJSONValue]
    public let notifications: TKJSONValue
    public let codexStrategy: TKJSONValue
    public let update: UpdateStatus

    enum CodingKeys: String, CodingKey {
        case generatedAt = "generated_at"
        case core
        case runtime
        case paths
        case daemon
        case status
        case pendingKicks = "pending_kicks"
        case schedule
        case advisories
        case resetObservations = "reset_observations"
        case notifications
        case codexStrategy = "codex_strategy"
        case update
    }
}

/// Payload of `tk accounts planning --json-output`.
public struct TKAccountsPlanningPayload: Decodable, Equatable, Sendable {
    public struct Account: Decodable, Equatable, Sendable {
        public let label: String
        public let provider: String
        public let visible: Bool
        public let autoKick: Bool
        public let sessionAutoKick: Bool
        public let usableSessionMinutes: Int
        public let orchestrationRole: String
        public let effectiveOrchestrationRole: String
        public let weeklyReserveThresholdPercent: Int?

        enum CodingKeys: String, CodingKey {
            case label
            case provider
            case visible
            case autoKick = "auto_kick"
            case sessionAutoKick = "session_auto_kick"
            case usableSessionMinutes = "usable_session_minutes"
            case orchestrationRole = "orchestration_role"
            case effectiveOrchestrationRole = "effective_orchestration_role"
            case weeklyReserveThresholdPercent = "weekly_reserve_threshold_percent"
        }
    }

    public let accounts: [Account]
}

/// Payload of `tk accounts notifications --json-output`.
public struct TKAccountNotificationsPayload: Decodable, Equatable, Sendable {
    public struct Account: Decodable, Equatable, Sendable {
        public let label: String
        public let provider: String
        public let notificationsEnabled: Bool
        public let backends: [String]?
        public let route: String

        enum CodingKeys: String, CodingKey {
            case label
            case provider
            case notificationsEnabled = "notifications_enabled"
            case backends
            case route
        }
    }

    public let globalEnabled: Bool
    public let destination: String
    public let backends: [String]
    public let accounts: [Account]

    enum CodingKeys: String, CodingKey {
        case globalEnabled = "global_enabled"
        case destination
        case backends
        case accounts
    }
}

/// Payload of `tk accounts list --json-output`.
public struct TKAccountsListPayload: Decodable, Equatable, Sendable {
    public struct Account: Decodable, Equatable, Sendable {
        public let label: String
        public let provider: String
        public let visible: Bool
        public let kickable: Bool
        public let monitorOnly: Bool
        public let autoKick: Bool
        public let weeklyAutoKick: Bool
        public let sessionAutoKick: Bool
        public let notificationsEnabled: Bool
        public let notificationsRoute: String
        public let kickModel: String
        public let statusProbeEnabled: Bool
        public let directUsageEnabled: Bool?

        enum CodingKeys: String, CodingKey {
            case label
            case provider
            case visible
            case kickable
            case monitorOnly = "monitor_only"
            case autoKick = "auto_kick"
            case weeklyAutoKick = "weekly_auto_kick"
            case sessionAutoKick = "session_auto_kick"
            case notificationsEnabled = "notifications_enabled"
            case notificationsRoute = "notifications_route"
            case kickModel = "kick_model"
            case statusProbeEnabled = "status_probe_enabled"
            case directUsageEnabled = "direct_usage_enabled"
        }
    }

    public let accounts: [Account]
}
