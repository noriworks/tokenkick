import XCTest
@testable import TokenKickKit

final class ProcessOwnershipTests: XCTestCase {
    func testTimeoutIncludesDescendantPipesAfterLeaderExits() async throws {
        let started = Date()
        do {
            _ = try await TKProcessRunner(timeout: 0.15).run(
                executable: URL(fileURLWithPath: "/bin/sh"),
                arguments: ["-c", "sleep 30 & echo ready"], environment: [:])
            XCTFail("Inherited pipes must count toward the deadline")
        } catch TKProcessError.timedOut { }
        XCTAssertLessThan(Date().timeIntervalSince(started), 4)
    }

    func testCancellationEscalatesWhenGroupIgnoresTerm() async throws {
        let task = Task {
            try await TKProcessRunner(timeout: 60).run(
                executable: URL(fileURLWithPath: "/bin/sh"),
                arguments: ["-c", "trap '' TERM; sleep 30 & wait"], environment: [:])
        }
        try await Task.sleep(nanoseconds: 100_000_000)
        let started = Date()
        task.cancel()
        do { _ = try await task.value; XCTFail("Expected cancellation") }
        catch TKProcessError.cancelled { }
        XCTAssertLessThan(Date().timeIntervalSince(started), 4)
    }

    func testStreamInterruptEscalatesAndClosesInheritedPipes() async throws {
        let stream = try TKProcessRunner().streamLines(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "trap '' INT TERM; echo started; sleep 30 & wait"], environment: [:])
        let started = Date()
        for try await line in stream.lines {
            if line == "started" { stream.interrupt() }
        }
        let exit = await stream.waitForExit()
        XCTAssertNotEqual(exit.exitCode, 0)
        XCTAssertLessThan(Date().timeIntervalSince(started), 4)
    }

    func testCancelledStreamConsumerStopsProcess() async throws {
        let stream = try TKProcessRunner().streamLines(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "trap '' TERM; sleep 30 & wait"], environment: [:])
        let reader = Task { for try await _ in stream.lines {} }
        try await Task.sleep(nanoseconds: 100_000_000)
        let started = Date()
        reader.cancel()
        _ = try? await reader.value
        _ = await stream.waitForExit()
        XCTAssertLessThan(Date().timeIntervalSince(started), 4)
    }

    func testWorkingDirectoryAndBothLargePipes() async throws {
        let result = try await TKProcessRunner(timeout: 10).run(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "pwd; i=0; while [ $i -lt 10000 ]; do echo output; echo error >&2; i=$((i+1)); done"],
            environment: [:], currentDirectory: URL(fileURLWithPath: "/private/tmp"))
        XCTAssertEqual(result.exitCode, 0)
        XCTAssertTrue(result.stdoutText.hasPrefix("/private/tmp\n"))
        XCTAssertEqual(result.stderrText.count, 60000)
        XCTAssertEqual(result.stdoutText.components(separatedBy: "output").count, 10001)
    }
}
