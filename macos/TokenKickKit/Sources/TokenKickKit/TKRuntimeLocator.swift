import Foundation

public enum TKRuntimeError: Error, CustomStringConvertible {
    case bundledRuntimeMissing(searched: [String])
    case runtimeNotExecutable(path: String)

    public var description: String {
        switch self {
        case .bundledRuntimeMissing(let searched):
            return "Bundled tk runtime not found. Searched: \(searched.joined(separator: ", "))"
        case .runtimeNotExecutable(let path):
            return "Bundled tk runtime is not executable: \(path)"
        }
    }
}

/// Resolves the `tk` executable the app is allowed to run.
///
/// Resolution order:
/// 1. `TK_APP_RUNTIME` environment override (development and tests).
/// 2. `Contents/Resources/tokenkick/tk` inside the given bundle.
///
/// The locator never consults `PATH`: an external `pipx tk` must stay
/// informational only and is never executed by the app.
public enum TKRuntimeLocator {
    public static let environmentOverrideKey = "TK_APP_RUNTIME"
    public static let bundleSubdirectory = "tokenkick"
    public static let executableName = "tk"

    public static func bundledTkURL(
        bundle: Bundle = .main,
        environment: [String: String] = ProcessInfo.processInfo.environment
    ) throws -> URL {
        var searched: [String] = []

        if let override = environment[environmentOverrideKey], !override.isEmpty {
            let url = URL(
                fileURLWithPath: override,
                relativeTo: URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            )
            .standardizedFileURL
            try validateExecutable(at: url)
            return url
        }

        if let resourceURL = bundle.resourceURL {
            let candidate = resourceURL
                .appendingPathComponent(bundleSubdirectory, isDirectory: true)
                .appendingPathComponent(executableName, isDirectory: false)
            searched.append(candidate.path)
            if FileManager.default.fileExists(atPath: candidate.path) {
                try validateExecutable(at: candidate)
                return candidate
            }
        }

        throw TKRuntimeError.bundledRuntimeMissing(searched: searched)
    }

    /// Version recorded by scripts/build-bundled-tk.sh next to the runtime.
    public static func runtimeVersion(forRuntimeAt url: URL) -> String? {
        let versionFile = url.deletingLastPathComponent().appendingPathComponent("RUNTIME_VERSION")
        guard let raw = try? String(contentsOf: versionFile, encoding: .utf8) else { return nil }
        let version = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return version.isEmpty ? nil : version
    }

    private static func validateExecutable(at url: URL) throws {
        var isDirectory: ObjCBool = false
        let exists = FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory)
        guard exists, !isDirectory.boolValue else {
            throw TKRuntimeError.bundledRuntimeMissing(searched: [url.path])
        }
        guard FileManager.default.isExecutableFile(atPath: url.path) else {
            throw TKRuntimeError.runtimeNotExecutable(path: url.path)
        }
    }
}
