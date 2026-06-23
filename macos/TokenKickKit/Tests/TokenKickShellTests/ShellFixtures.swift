import Foundation
import TokenKickKit
@testable import TokenKickShell

/// Inline snapshot builders for shell tests. They go through the production
/// envelope decoder so model tests double as decoding checks.
enum ShellFixtures {
    static func accountRow(
        label: String,
        provider: String = "codex",
        state: String = "fresh",
        visible: Bool = true,
        kickable: Bool = true,
        autoKick: Bool = false,
        usedPercent: Double? = nil,
        resetsIn: String = "2h 14m",
        stale: Bool = false
    ) -> [String: Any] {
        var row: [String: Any] = [
            "label": label,
            "provider": provider,
            "state": state,
            "visible": visible,
            "kickable": kickable,
            "auto_kick": autoKick,
            "resets_in_human": resetsIn,
            "stale": stale,
            "account_key": "test|\(provider)|\(label)",
        ]
        if let usedPercent {
            row["used_percent"] = usedPercent
        }
        return row
    }

    static func pendingKick(
        accountLabel: String,
        kickAt: String,
        reason: String = "orchestrated"
    ) -> [String: Any] {
        [
            "account_label": accountLabel,
            "kick_at": kickAt,
            "reason": reason,
        ]
    }

    static func envelope(
        ok: Bool = true,
        errorCode: String? = nil,
        message: String? = nil,
        warnings: [String] = [],
        daemonRunning: Bool = false,
        daemonVersion: String? = nil,
        installedVersion: String = "1.9.14",
        versionMatch: Bool? = nil,
        executable: String? = nil,
        executableMatch: Bool? = nil,
        stalePidfile: Bool = false,
        accounts: [[String: Any]] = [],
        pendingKicks: [[String: Any]] = [],
        advisories: [[String: Any]] = [],
        resetObservations: [[String: Any]] = []
    ) throws -> TKEnvelope<TKSnapshotPayload> {
        let daemon: [String: Any] = [
            "running": daemonRunning,
            "pid": daemonRunning ? 4242 : NSNull(),
            "version": daemonVersion ?? NSNull(),
            "executable": executable ?? NSNull(),
            "installed_version": installedVersion,
            "version_match": versionMatch ?? NSNull(),
            "executable_match": executableMatch ?? NSNull(),
            "pidfile_exists": daemonRunning || stalePidfile,
            "stale_pidfile": stalePidfile,
            "uptime_seconds": daemonRunning ? 3600 : NSNull(),
            "poll_interval_minutes": 5,
            "pidfile_path": "/Users/fixture/.tokenkick/daemon.pid",
            "log_path": "/Users/fixture/.tokenkick/daemon.log",
        ]
        let payload: [String: Any] = [
            "generated_at": "2026-06-11T08:00:00+00:00",
            "core": [
                "version": installedVersion,
                "executable": "/Users/fixture/tk",
                "python_executable": "/Users/fixture/python",
                "python_version": "3.14.5",
                "app_mode": true,
            ],
            "runtime": ["external_tk": NSNull()],
            "paths": [
                "config_dir": "/Users/fixture/.tokenkick",
                "config_file": "/Users/fixture/.tokenkick/config.json",
            ],
            "daemon": daemon,
            "status": [
                "cached": true,
                "cached_at": "2026-06-11T07:58:00+00:00",
                "refresh_error": NSNull(),
                "refresh_in_progress": false,
                "schema_version": 1,
                "accounts": accounts,
            ],
            "pending_kicks": pendingKicks,
            "schedule": ["enabled": false, "default": ["enabled": false], "accounts": [:]],
            "advisories": advisories,
            "reset_observations": resetObservations,
            "notifications": ["enabled": false, "accounts": []],
            "codex_strategy": ["enabled": false],
            "update": [
                "installed_version": installedVersion,
                "daemon_version": daemonVersion ?? NSNull(),
                "daemon_running": daemonRunning,
                "match": versionMatch ?? !daemonRunning,
                "daemon_pid": NSNull(),
            ],
        ]
        let envelope: [String: Any] = [
            "schema_version": 1,
            "ok": ok,
            "error_code": errorCode ?? NSNull(),
            "message": message ?? NSNull(),
            "warnings": warnings,
            "payload": ok ? payload : NSNull(),
        ]
        let data = try JSONSerialization.data(withJSONObject: envelope)
        return try TKJSONDecoding.envelope(TKSnapshotPayload.self, from: data)
    }

    static func snapshot(
        daemonRunning: Bool = true,
        daemonVersion: String? = "1.9.14",
        versionMatch: Bool? = true,
        executable: String? = "/Users/fixture/tk",
        executableMatch: Bool? = true,
        stalePidfile: Bool = false,
        accounts: [[String: Any]] = [],
        pendingKicks: [[String: Any]] = [],
        advisories: [[String: Any]] = [],
        resetObservations: [[String: Any]] = []
    ) throws -> TKSnapshotPayload {
        let envelope = try envelope(
            daemonRunning: daemonRunning,
            daemonVersion: daemonVersion,
            versionMatch: versionMatch,
            executable: executable,
            executableMatch: executableMatch,
            stalePidfile: stalePidfile,
            accounts: accounts,
            pendingKicks: pendingKicks,
            advisories: advisories,
            resetObservations: resetObservations
        )
        guard let payload = envelope.payload else {
            throw NSError(domain: "ShellFixtures", code: 1)
        }
        return payload
    }
}

extension ShellFixtures {
    static func kickEnvelope(
        ok: Bool = true,
        errorCode: String? = nil,
        message: String? = nil,
        account: String = "codex (a)",
        decision: String = "attempted",
        result: String? = "confirmed",
        reasonCode: String? = nil,
        kicked: Bool? = true
    ) throws -> TKEnvelope<TKKickResultPayload> {
        var payload: [String: Any] = [
            "action": "kick",
            "account": account,
            "dry_run": false,
            "decision": decision,
            "reason_code": reasonCode ?? NSNull(),
            "kicked": kicked ?? NSNull(),
            "result": result ?? NSNull(),
            "kick_type": "kick",
            "event": NSNull(),
        ]
        if decision == "attempted" {
            payload["event"] = [
                "label": account,
                "success": kicked ?? false,
                "confirmed": result == "confirmed",
            ]
        }
        let envelope: [String: Any] = [
            "schema_version": 1,
            "ok": ok,
            "error_code": errorCode ?? NSNull(),
            "message": message ?? NSNull(),
            "warnings": [String](),
            "payload": payload,
        ]
        let data = try JSONSerialization.data(withJSONObject: envelope)
        return try TKJSONDecoding.envelope(TKKickResultPayload.self, from: data)
    }
}

/// Sequential stub provider for SnapshotStore tests.
final class SequenceProvider: SnapshotProviding, @unchecked Sendable {
    private let lock = NSLock()
    private var queue: [Result<TKEnvelope<TKSnapshotPayload>, Error>]

    init(_ results: [Result<TKEnvelope<TKSnapshotPayload>, Error>]) {
        self.queue = results
    }

    func fetchSnapshot() async throws -> TKEnvelope<TKSnapshotPayload> {
        lock.lock()
        let next = queue.isEmpty ? nil : queue.removeFirst()
        lock.unlock()
        guard let next else {
            throw NSError(
                domain: "SequenceProvider",
                code: 99,
                userInfo: [NSLocalizedDescriptionKey: "no more stubbed results"]
            )
        }
        return try next.get()
    }
}

struct StubError: Error, CustomStringConvertible {
    let description: String
}
