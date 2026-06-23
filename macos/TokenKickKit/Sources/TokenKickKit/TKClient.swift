import Foundation

/// Typed entry point for the app: one bundled runtime, one environment,
/// envelope decoding for every call. Error envelopes (nonzero exit) decode
/// the same way — `envelope.ok` carries the outcome.
public struct TKClient: Sendable {
    public let runtime: URL
    public let runner: TKProcessRunner
    public let environment: [String: String]

    public init(
        runtime: URL,
        environment: [String: String]? = nil,
        timeout: TimeInterval = 60
    ) {
        self.runtime = runtime
        self.runner = TKProcessRunner(timeout: timeout)
        self.environment = environment ?? TKEnvironment.subprocessEnvironment()
    }

    public func snapshot() async throws -> TKEnvelope<TKSnapshotPayload> {
        try await envelope(TKSnapshotPayload.self, arguments: ["app", "snapshot"])
    }

    public func daemonStatus() async throws -> TKEnvelope<TKDaemonEnvelopePayload> {
        try await envelope(
            TKDaemonEnvelopePayload.self,
            arguments: ["daemon", "--status", "--json-output"]
        )
    }

    public func stopDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        try await envelope(
            TKDaemonActionPayload.self,
            arguments: ["daemon", "--stop", "--json-output"]
        )
    }

    public func startDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        try await envelope(
            TKDaemonActionPayload.self,
            arguments: ["daemon", "--background", "--json-output"]
        )
    }

    public func restartDaemon() async throws -> TKEnvelope<TKDaemonActionPayload> {
        try await envelope(
            TKDaemonActionPayload.self,
            arguments: ["daemon", "--restart", "--json-output"]
        )
    }

    public func accountsPlanning() async throws -> TKEnvelope<TKAccountsPlanningPayload> {
        try await envelope(
            TKAccountsPlanningPayload.self,
            arguments: ["accounts", "planning", "--json-output"]
        )
    }

    public func accountsNotifications() async throws -> TKEnvelope<TKAccountNotificationsPayload> {
        try await envelope(
            TKAccountNotificationsPayload.self,
            arguments: ["accounts", "notifications", "--json-output"]
        )
    }

    public func accountsList() async throws -> TKEnvelope<TKAccountsListPayload> {
        try await envelope(
            TKAccountsListPayload.self,
            arguments: ["accounts", "list", "--json-output"]
        )
    }

    /// Recent kick history. Bare-array payload (legacy schema); newest
    /// events come last in file order.
    public func history(limit: Int = 200) async throws -> [TKJSONValue] {
        let value = try await bareValue(
            arguments: ["history", "--limit", String(limit), "--json-output"]
        )
        return value.arrayValue ?? []
    }

    /// Reset event log; bare `{"events": [...]}` payload (legacy schema).
    public func resetLog() async throws -> [TKJSONValue] {
        let value = try await bareValue(arguments: ["reset-log", "--json-output"])
        return value["events"]?.arrayValue ?? []
    }

    public func appDoctor() async throws -> TKEnvelope<TKJSONValue> {
        try await envelope(TKJSONValue.self, arguments: ["app", "doctor"])
    }

    public func envelope<Payload: Decodable & Sendable>(
        _ payloadType: Payload.Type,
        arguments: [String]
    ) async throws -> TKEnvelope<Payload> {
        let result = try await runner.run(
            executable: runtime,
            arguments: arguments,
            environment: environment
        )
        return try TKJSONDecoding.envelope(payloadType, from: result.stdout)
    }

    public func bareValue(arguments: [String]) async throws -> TKJSONValue {
        let result = try await runner.run(
            executable: runtime,
            arguments: arguments,
            environment: environment
        )
        return try TKJSONDecoding.bareValue(from: result.stdout)
    }
}
