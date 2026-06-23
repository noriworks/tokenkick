// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "TokenKickKit",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "TokenKickKit", targets: ["TokenKickKit"]),
        .library(name: "TokenKickShell", targets: ["TokenKickShell"]),
        .executable(name: "tkapp-probe", targets: ["tkapp-probe"]),
        .executable(name: "TokenKick", targets: ["TokenKick"]),
    ],
    targets: [
        .target(name: "TokenKickKit"),
        .target(name: "TokenKickShell", dependencies: ["TokenKickKit"]),
        .executableTarget(name: "tkapp-probe", dependencies: ["TokenKickKit"]),
        .executableTarget(name: "TokenKick", dependencies: ["TokenKickShell"]),
        .testTarget(
            name: "TokenKickKitTests",
            dependencies: ["TokenKickKit"],
            resources: [.copy("Fixtures")]
        ),
        .testTarget(
            name: "TokenKickShellTests",
            dependencies: ["TokenKickShell"]
        ),
    ]
)
