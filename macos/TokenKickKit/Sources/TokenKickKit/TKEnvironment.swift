import Foundation

/// Builds the environment for `tk` subprocesses.
///
/// Finder-launched apps inherit a minimal PATH (`/usr/bin:/bin:...`) without
/// the shell profile, so provider CLI discovery (`codex`, `claude`) would
/// fail. The builder appends the standard CLI install locations while
/// preserving whatever PATH the app inherited, and always sets
/// `TK_APP_MODE=1` so stdout stays JSON-only.
public enum TKEnvironment {
    /// Standard locations appended for provider CLI discovery, relative
    /// entries are resolved against the effective home directory.
    public static let defaultPathAdditions = [
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "~/.local/bin",
        "~/bin",
        "~/.bun/bin",
    ]

    public static func subprocessEnvironment(
        base: [String: String] = ProcessInfo.processInfo.environment,
        home: String? = nil,
        pathAdditions: [String] = defaultPathAdditions
    ) -> [String: String] {
        var environment = base
        let effectiveHome = home ?? base["HOME"] ?? NSHomeDirectory()
        if home != nil {
            environment["HOME"] = effectiveHome
        }
        environment["TK_APP_MODE"] = "1"
        environment["PATH"] = augmentedPath(
            basePath: base["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin",
            additions: pathAdditions,
            home: effectiveHome
        )
        return environment
    }

    /// Existing PATH entries keep priority; additions are appended, with
    /// `~` resolved and duplicates removed (first occurrence wins).
    public static func augmentedPath(
        basePath: String,
        additions: [String] = defaultPathAdditions,
        home: String
    ) -> String {
        var seen = Set<String>()
        var entries: [String] = []
        let resolvedAdditions = additions.map { entry -> String in
            if entry == "~" { return home }
            if entry.hasPrefix("~/") { return home + String(entry.dropFirst(1)) }
            return entry
        }
        for entry in basePath.split(separator: ":").map(String.init) + resolvedAdditions {
            guard !entry.isEmpty, seen.insert(entry).inserted else { continue }
            entries.append(entry)
        }
        return entries.joined(separator: ":")
    }
}
