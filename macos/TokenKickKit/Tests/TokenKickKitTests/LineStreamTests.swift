import XCTest
@testable import TokenKickKit

final class LineStreamTests: XCTestCase {
    private func collect(_ stream: TKLineStream) async throws -> [String] {
        var lines: [String] = []
        for try await line in stream.lines {
            lines.append(line)
        }
        return lines
    }

    func testStreamsLinesAndExitCode() async throws {
        let stream = try TKProcessRunner().streamLines(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "echo one; echo two; echo three"],
            environment: [:]
        )
        let lines = try await collect(stream)
        XCTAssertEqual(lines, ["one", "two", "three"])
        let exit = await stream.waitForExit()
        XCTAssertEqual(exit.exitCode, 0)
    }

    func testSkipsBlankLines() async throws {
        let stream = try TKProcessRunner().streamLines(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: ["-c", "echo a; echo; echo '  '; echo b"],
            environment: [:]
        )
        let lines = try await collect(stream)
        XCTAssertEqual(lines, ["a", "b"])
    }

    func testInterruptLetsProcessFinishGracefully() async throws {
        // The trap mirrors tk app setup: on SIGINT it emits a final record
        // and exits cleanly; reading continues to EOF so it is not lost.
        // (sleep runs in the background — sh defers traps while a foreground
        // command runs, but `wait` is interruptible.)
        let stream = try TKProcessRunner().streamLines(
            executable: URL(fileURLWithPath: "/bin/sh"),
            arguments: [
                "-c",
                // sleep's stdout is detached from the pipe so the orphaned
                // child can't delay EOF after the shell exits.
                "trap 'echo terminal-record; exit 0' INT; echo started; sleep 30 >/dev/null 2>&1 & wait $!",
            ],
            environment: [:]
        )
        var lines: [String] = []
        for try await line in stream.lines {
            lines.append(line)
            if line == "started" {
                stream.interrupt()
            }
        }
        XCTAssertEqual(lines, ["started", "terminal-record"])
        let exit = await stream.waitForExit()
        XCTAssertEqual(exit.exitCode, 0)
    }

    func testLaunchFailureThrows() {
        XCTAssertThrowsError(
            try TKProcessRunner().streamLines(
                executable: URL(fileURLWithPath: "/nonexistent/binary"),
                arguments: [],
                environment: [:]
            )
        )
    }
}
