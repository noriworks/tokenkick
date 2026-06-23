import Foundation
import TokenKickKit

/// Prototype proof that the app boundary works end to end:
/// resolve the bundled `tk` (never PATH), run `tk app snapshot` through
/// TKProcessRunner, decode the envelope, and verify the bundled runtime —
/// not an external pipx tk — answered.
///
/// Usage:
///   tkapp-probe [--runtime <path-to-bundled-tk>] [--isolate-home] [--doctor]
///   tkapp-probe --app <path-to-TokenKick.app> [--isolate-home] [--doctor]
/// Without --runtime or --app, the TK_APP_RUNTIME environment override is used.

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data(("FAIL: " + message + "\n").utf8))
    exit(1)
}

var runtimeArgument: String?
var appBundleArgument: String?
var isolateHome = false
var runDoctor = false
var argumentIterator = CommandLine.arguments.dropFirst().makeIterator()
while let argument = argumentIterator.next() {
    switch argument {
    case "--app":
        guard let value = argumentIterator.next() else { fail("--app needs a path") }
        appBundleArgument = value
    case "--runtime":
        guard let value = argumentIterator.next() else { fail("--runtime needs a path") }
        runtimeArgument = value
    case "--isolate-home":
        isolateHome = true
    case "--doctor":
        runDoctor = true
    default:
        fail("unknown argument \(argument)")
    }
}

var environment = ProcessInfo.processInfo.environment
if let runtimeArgument {
    environment[TKRuntimeLocator.environmentOverrideKey] = runtimeArgument
}
if appBundleArgument != nil {
    environment.removeValue(forKey: TKRuntimeLocator.environmentOverrideKey)
}

let runtime: URL
do {
    if let appBundleArgument {
        let appURL = URL(fileURLWithPath: appBundleArgument).standardizedFileURL
        guard let bundle = Bundle(url: appURL) else {
            fail("could not load app bundle at \(appURL.path)")
        }
        runtime = try TKRuntimeLocator.bundledTkURL(bundle: bundle, environment: environment)
    } else {
        runtime = try TKRuntimeLocator.bundledTkURL(environment: environment)
    }
} catch {
    fail("\(error)")
}
print("runtime: \(runtime.path)")

let expectedVersion = TKRuntimeLocator.runtimeVersion(forRuntimeAt: runtime)
print("RUNTIME_VERSION file: \(expectedVersion ?? "<absent>")")

var home: String?
if isolateHome {
    let temporary = FileManager.default.temporaryDirectory
        .appendingPathComponent("tkapp-probe-\(UUID().uuidString)")
    try? FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
    home = temporary.path
    print("isolated HOME: \(temporary.path)")
}

let subprocessEnvironment = TKEnvironment.subprocessEnvironment(home: home)
let client = TKClient(runtime: runtime, environment: subprocessEnvironment, timeout: 120)

let envelope: TKEnvelope<TKSnapshotPayload>
do {
    envelope = try await client.snapshot()
} catch {
    fail("snapshot did not cross the process boundary: \(error)")
}

guard envelope.ok, let payload = envelope.payload else {
    fail("snapshot envelope reported ok=\(envelope.ok) error_code=\(envelope.errorCode ?? "nil")")
}
print("envelope ok: \(envelope.ok) (schema v\(envelope.schemaVersion))")
print("core.version: \(payload.core.version)")
print("core.executable: \(payload.core.executable ?? "<nil>")")
print("daemon.running: \(payload.daemon.running)")
for warning in envelope.warnings {
    print("warning: \(warning)")
}

if let expectedVersion, payload.core.version != expectedVersion {
    fail("bundled runtime answered v\(payload.core.version), expected v\(expectedVersion) — wrong tk executed")
}

if let coreExecutable = payload.core.executable,
   URL(fileURLWithPath: coreExecutable).resolvingSymlinksInPath().path
       != runtime.resolvingSymlinksInPath().path {
    fail("snapshot core.executable (\(coreExecutable)) is not the bundled runtime — wrong tk executed")
}

if let externalTk = payload.runtime.externalTk {
    if externalTk.isCurrentRuntime {
        fail("external tk on PATH (\(externalTk.path)) was the answering runtime — bundling is broken")
    }
    print(
        "external tk on PATH: \(externalTk.path) "
            + "v\(externalTk.version ?? "?") — informational only, not executed"
    )
} else {
    print("external tk on PATH: none detected")
}

if runDoctor {
    let doctorEnvelope: TKEnvelope<TKJSONValue>
    do {
        doctorEnvelope = try await client.appDoctor()
    } catch {
        fail("app doctor did not cross the process boundary: \(error)")
    }
    guard doctorEnvelope.ok, let doctorPayload = doctorEnvelope.payload else {
        fail(
            "doctor envelope reported ok=\(doctorEnvelope.ok) "
                + "error_code=\(doctorEnvelope.errorCode ?? "nil")"
        )
    }
    let providerCLIs = doctorPayload["provider_clis"]?.objectValue ?? [:]
    for name in providerCLIs.keys.sorted() {
        let entry = providerCLIs[name]?.objectValue
        let found = entry?["found"]?.boolValue ?? false
        let path = entry?["path"]?.stringValue ?? "<missing>"
        print("provider cli \(name): \(found ? path : "not found")")
    }
}

print("PASS: bundled tk answered tk app snapshot through the Swift process boundary")
exit(0)
