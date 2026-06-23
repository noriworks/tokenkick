import Foundation

public struct TKCodexStrategyPayload: Decodable, Equatable, Sendable {
    public struct AutoDemotion: Decodable, Equatable, Sendable {
        public let state: String
        public let summary: String
        public let enabledCount: Int
        public let disabledCount: Int
        public let totalCodexAccounts: Int
        public let enabledLabels: [String]
        public let disabledLabels: [String]

        enum CodingKeys: String, CodingKey {
            case state
            case summary
            case enabledCount = "enabled_count"
            case disabledCount = "disabled_count"
            case totalCodexAccounts = "total_codex_accounts"
            case enabledLabels = "enabled_labels"
            case disabledLabels = "disabled_labels"
        }
    }

    public let schemaVersion: Int
    public let strategy: String
    public let enabled: Bool
    public let configEnabled: Bool
    public let activeOrder: [String]
    public let effectiveKickingOrder: [String]
    public let effectiveKickingOrderSummary: String
    public let effectiveKickingOrderByAccount: [String: [String]]
    public let effectiveKickingOrderErrors: [String: String]
    public let configuredOrder: [String]
    public let defaultOrder: [String]
    public let activeGapSeconds: Double
    public let configuredGapSeconds: Int
    public let autoDemotion: AutoDemotion
    public let appliesTo: String
    public let enabledBehavior: String
    public let disabledBehavior: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case strategy
        case enabled
        case configEnabled = "config_enabled"
        case activeOrder = "active_order"
        case effectiveKickingOrder = "effective_kicking_order"
        case effectiveKickingOrderSummary = "effective_kicking_order_summary"
        case effectiveKickingOrderByAccount = "effective_kicking_order_by_account"
        case effectiveKickingOrderErrors = "effective_kicking_order_errors"
        case configuredOrder = "configured_order"
        case defaultOrder = "default_order"
        case activeGapSeconds = "active_gap_seconds"
        case configuredGapSeconds = "configured_gap_seconds"
        case autoDemotion = "auto_demotion"
        case appliesTo = "applies_to"
        case enabledBehavior = "enabled_behavior"
        case disabledBehavior = "disabled_behavior"
    }
}

public struct TKCodexSurfacesPayload: Decodable, Equatable, Sendable {
    public struct Demotion: Decodable, Equatable, Sendable {
        public let enabled: Bool
        public let afterStrongClusters: Int
        public let minActiveSurfaces: Int
        public let minKeptAnchorRate: Double
        public let measurementClusters: Int
        public let rescueCooldownStrongClusters: Int
        public let forceKeep: [String]
        public let forcePrune: [String]
        public let strongClusterCount: Int
        public let demoted: [String: TKJSONValue]
        public let rescues: [String: TKJSONValue]
        public let lastReintroduction: TKJSONValue?

        enum CodingKeys: String, CodingKey {
            case enabled
            case afterStrongClusters = "after_strong_clusters"
            case minActiveSurfaces = "min_active_surfaces"
            case minKeptAnchorRate = "min_kept_anchor_rate"
            case measurementClusters = "measurement_clusters"
            case rescueCooldownStrongClusters = "rescue_cooldown_strong_clusters"
            case forceKeep = "force_keep"
            case forcePrune = "force_prune"
            case strongClusterCount = "strong_cluster_count"
            case demoted
            case rescues
            case lastReintroduction = "last_reintroduction"
        }
    }

    public struct Surface: Decodable, Equatable, Identifiable, Sendable {
        public let surface: String
        public let rank: Int?
        public let state: String
        public let demotionReason: String?
        public let rescueCooldownRemainingStrongClusters: Int?
        public let score: Double
        public let attempts: Int
        public let confirmed: Int
        public let timingMatches: Int
        public let externalPossible: Int
        public let noGeneration: Int
        public let failures: Int
        public let lastAttemptAt: Double?
        public let lastConfirmedAt: Double?

        public var id: String { surface }

        enum CodingKeys: String, CodingKey {
            case surface
            case rank
            case state
            case demotionReason = "demotion_reason"
            case rescueCooldownRemainingStrongClusters = "rescue_cooldown_remaining_strong_clusters"
            case score
            case attempts
            case confirmed
            case timingMatches = "timing_matches"
            case externalPossible = "external_possible"
            case noGeneration = "no_generation"
            case failures
            case lastAttemptAt = "last_attempt_at"
            case lastConfirmedAt = "last_confirmed_at"
        }
    }

    public let schemaVersion: Int
    public let readOnly: Bool
    public let label: String
    public let accountKey: String
    public let providerHome: String?
    public let order: [String]
    public let demotion: Demotion
    public let surfaces: [Surface]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case readOnly = "read_only"
        case label
        case accountKey = "account_key"
        case providerHome = "provider_home"
        case order
        case demotion
        case surfaces
    }
}

public struct TKCodexSurfacePatternsPayload: Decodable, Equatable, Sendable {
    public struct Metrics: Decodable, Equatable, Sendable {
        public let samples: Int
        public let top1Hits: Int?
        public let top2Hits: Int?
        public let top1Rate: Double?
        public let top2Rate: Double?
        public let top1LiftHits: Int?
        public let top2LiftHits: Int?
        public let top1LiftRate: Double?
        public let top2LiftRate: Double?

        enum CodingKeys: String, CodingKey {
            case samples
            case top1Hits = "top1_hits"
            case top2Hits = "top2_hits"
            case top1Rate = "top1_rate"
            case top2Rate = "top2_rate"
            case top1LiftHits = "top1_lift_hits"
            case top2LiftHits = "top2_lift_hits"
            case top1LiftRate = "top1_lift_rate"
            case top2LiftRate = "top2_lift_rate"
        }
    }

    public struct Verdict: Decodable, Equatable, Sendable {
        public let message: String?
    }

    public let scopeLabel: String?
    public let eligibleClusters: Int
    public let evaluatedSamples: Int
    public let baseline: Metrics
    public let candidates: [String: Metrics]
    public let ignored: [String: Int]
    public let verdict: Verdict
    public let sequenceHints: [TKJSONValue]

    enum CodingKeys: String, CodingKey {
        case scopeLabel = "scope_label"
        case eligibleClusters = "eligible_clusters"
        case evaluatedSamples = "evaluated_samples"
        case baseline
        case candidates
        case ignored
        case verdict
        case sequenceHints = "sequence_hints"
    }
}

public struct TKCodexMutationPayload: Decodable, Equatable, Sendable {
    public let action: String
    public let account: String?
    public let count: Int?
    public let codexStrategy: TKCodexStrategyPayload?
    public let codexSurfaces: TKCodexSurfacesPayload?

    enum CodingKeys: String, CodingKey {
        case action
        case account
        case count
        case codexStrategy = "codex_strategy"
        case codexSurfaces = "codex_surfaces"
    }
}

extension TKClient {
    public func bare<Payload: Decodable & Sendable>(
        _ payloadType: Payload.Type,
        arguments: [String]
    ) async throws -> Payload {
        let result = try await runner.run(
            executable: runtime,
            arguments: arguments,
            environment: environment
        )
        return try TKJSONDecoding.bare(payloadType, from: result.stdout)
    }

    public func codexStrategyStatus() async throws -> TKCodexStrategyPayload {
        try await bare(TKCodexStrategyPayload.self, arguments: ["codex-strategy", "status", "--json-output"])
    }

    public func codexSurfaces(label: String) async throws -> TKCodexSurfacesPayload {
        try await bare(TKCodexSurfacesPayload.self, arguments: ["codex-surfaces", label, "--json-output"])
    }

    public func codexSurfacePatterns(label: String? = nil) async throws -> TKCodexSurfacePatternsPayload {
        var arguments = ["codex-surface-patterns"]
        if let label, !label.isEmpty {
            arguments.append(label)
        }
        arguments.append("--json-output")
        return try await bare(TKCodexSurfacePatternsPayload.self, arguments: arguments)
    }

    public func codexMutation(arguments: [String]) async throws -> TKEnvelope<TKCodexMutationPayload> {
        try await envelope(TKCodexMutationPayload.self, arguments: arguments)
    }
}
