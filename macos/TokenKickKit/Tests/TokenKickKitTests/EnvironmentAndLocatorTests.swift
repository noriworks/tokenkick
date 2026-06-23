import XCTest
@testable import TokenKickKit

final class EnvironmentTests: XCTestCase {
    func testAugmentedPathKeepsBaseOrderAndAppendsAdditions() {
        let path = TKEnvironment.augmentedPath(
            basePath: "/usr/bin:/bin",
            additions: ["/opt/homebrew/bin", "~/.local/bin"],
            home: "/Users/fixture"
        )
        XCTAssertEqual(path, "/usr/bin:/bin:/opt/homebrew/bin:/Users/fixture/.local/bin")
    }

    func testAugmentedPathDeduplicatesPreservingFirstOccurrence() {
        let path = TKEnvironment.augmentedPath(
            basePath: "/opt/homebrew/bin:/usr/bin",
            additions: ["/opt/homebrew/bin", "/usr/local/bin"],
            home: "/Users/fixture"
        )
        XCTAssertEqual(path, "/opt/homebrew/bin:/usr/bin:/usr/local/bin")
    }

    func testSubprocessEnvironmentSetsAppModeAndPath() {
        let environment = TKEnvironment.subprocessEnvironment(
            base: ["PATH": "/usr/bin:/bin", "HOME": "/Users/fixture"],
            home: nil
        )
        XCTAssertEqual(environment["TK_APP_MODE"], "1")
        XCTAssertEqual(environment["HOME"], "/Users/fixture")
        let path = try! XCTUnwrap(environment["PATH"])
        XCTAssertTrue(path.hasPrefix("/usr/bin:/bin:"))
        XCTAssertTrue(path.contains("/opt/homebrew/bin"))
        XCTAssertTrue(path.contains("/Users/fixture/.local/bin"))
    }

    func testSubprocessEnvironmentHomeOverride() {
        let environment = TKEnvironment.subprocessEnvironment(
            base: ["PATH": "/usr/bin", "HOME": "/Users/fixture"],
            home: "/tmp/isolated"
        )
        XCTAssertEqual(environment["HOME"], "/tmp/isolated")
        XCTAssertTrue(try! XCTUnwrap(environment["PATH"]).contains("/tmp/isolated/.local/bin"))
    }
}

final class RuntimeLocatorTests: XCTestCase {
    private func makeExecutable(named name: String = "tk") throws -> URL {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("tk-locator-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(name)
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
        return url
    }

    func testEnvironmentOverrideWins() throws {
        let executable = try makeExecutable()
        let resolved = try TKRuntimeLocator.bundledTkURL(
            environment: [TKRuntimeLocator.environmentOverrideKey: executable.path]
        )
        XCTAssertEqual(resolved.path, executable.path)
    }

    func testRelativeEnvironmentOverrideResolvesToAbsoluteURL() throws {
        let base = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let directory = base
            .appendingPathComponent("tk-locator-relative-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let executable = directory.appendingPathComponent("tk")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)

        let resolved = try TKRuntimeLocator.bundledTkURL(
            environment: [TKRuntimeLocator.environmentOverrideKey: directory.lastPathComponent + "/tk"]
        )

        XCTAssertEqual(resolved.path, executable.path)
    }

    func testMissingOverrideTargetThrows() {
        XCTAssertThrowsError(
            try TKRuntimeLocator.bundledTkURL(
                environment: [TKRuntimeLocator.environmentOverrideKey: "/nonexistent/tk"]
            )
        )
    }

    func testNonExecutableRuntimeThrows() throws {
        let executable = try makeExecutable()
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o644],
            ofItemAtPath: executable.path
        )
        XCTAssertThrowsError(
            try TKRuntimeLocator.bundledTkURL(
                environment: [TKRuntimeLocator.environmentOverrideKey: executable.path]
            )
        ) { error in
            guard case TKRuntimeError.runtimeNotExecutable = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }

    func testNeverFallsBackToPathTk() {
        // The test bundle has no Resources/tokenkick/tk; even when a `tk`
        // exists on PATH (pipx install), the locator must refuse rather
        // than fall back to it.
        XCTAssertThrowsError(
            try TKRuntimeLocator.bundledTkURL(bundle: Bundle.module, environment: [:])
        ) { error in
            guard case TKRuntimeError.bundledRuntimeMissing = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }

    func testFindsRuntimeInsideAppBundleResourcesWithoutEnvironmentOverride() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("tk-app-bundle-\(UUID().uuidString)", isDirectory: true)
        let appURL = directory.appendingPathComponent("TokenKick.app", isDirectory: true)
        let contentsURL = appURL.appendingPathComponent("Contents", isDirectory: true)
        let runtimeDirectoryURL = contentsURL
            .appendingPathComponent("Resources", isDirectory: true)
            .appendingPathComponent("tokenkick", isDirectory: true)
        try FileManager.default.createDirectory(
            at: runtimeDirectoryURL,
            withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: directory) }

        let executable = runtimeDirectoryURL.appendingPathComponent("tk")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: executable.path)
        try Data("8.7.6\n".utf8)
            .write(to: runtimeDirectoryURL.appendingPathComponent("RUNTIME_VERSION"))

        let plist: [String: Any] = [
            "CFBundleExecutable": "TokenKick",
            "CFBundleIdentifier": "com.tokenkick.test",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "8.7.6",
            "CFBundleVersion": "8.7.6",
        ]
        let plistData = try PropertyListSerialization.data(
            fromPropertyList: plist,
            format: .xml,
            options: 0
        )
        try plistData.write(to: contentsURL.appendingPathComponent("Info.plist"))

        let bundle = try XCTUnwrap(Bundle(url: appURL))
        let resolved = try TKRuntimeLocator.bundledTkURL(
            bundle: bundle,
            environment: ["PATH": "/tmp/path-tk-must-not-win"]
        )

        XCTAssertEqual(resolved.path, executable.path)
        XCTAssertEqual(TKRuntimeLocator.runtimeVersion(forRuntimeAt: resolved), "8.7.6")
    }

    func testRuntimeVersionReadsSiblingFile() throws {
        let executable = try makeExecutable()
        let versionFile = executable.deletingLastPathComponent()
            .appendingPathComponent("RUNTIME_VERSION")
        try Data("9.9.9\n".utf8).write(to: versionFile)
        XCTAssertEqual(TKRuntimeLocator.runtimeVersion(forRuntimeAt: executable), "9.9.9")
    }

    func testRuntimeVersionAbsentIsNil() throws {
        let executable = try makeExecutable()
        XCTAssertNil(TKRuntimeLocator.runtimeVersion(forRuntimeAt: executable))
    }
}

final class ProcessRunnerTests: XCTestCase {
    func testCapturesStdoutAndExitCode() async throws {
        let runner = TKProcessRunner(timeout: 10)
        let result = try await runner.run(
            executable: URL(fileURLWithPath: "/bin/echo"),
            arguments: ["hello"],
            environment: [:]
        )
        XCTAssertEqual(result.exitCode, 0)
        XCTAssertEqual(result.stdoutText, "hello\n")
        XCTAssertEqual(result.stderrText, "")
    }

    func testNonZeroExitCodeIsReturnedNotThrown() async throws {
        let runner = TKProcessRunner(timeout: 10)
        let result = try await runner.run(
            executable: URL(fileURLWithPath: "/usr/bin/false"),
            arguments: [],
            environment: [:]
        )
        XCTAssertEqual(result.exitCode, 1)
    }

    func testTimeoutTerminatesProcess() async {
        let runner = TKProcessRunner(timeout: 0.3)
        let started = Date()
        do {
            _ = try await runner.run(
                executable: URL(fileURLWithPath: "/bin/sleep"),
                arguments: ["30"],
                environment: [:]
            )
            XCTFail("expected timeout")
        } catch {
            guard case TKProcessError.timedOut = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
        XCTAssertLessThan(Date().timeIntervalSince(started), 10)
    }

    func testLaunchFailureThrows() async {
        let runner = TKProcessRunner(timeout: 5)
        do {
            _ = try await runner.run(
                executable: URL(fileURLWithPath: "/nonexistent/binary"),
                arguments: [],
                environment: [:]
            )
            XCTFail("expected launch failure")
        } catch {
            guard case TKProcessError.launchFailed = error else {
                return XCTFail("unexpected error \(error)")
            }
        }
    }
}
