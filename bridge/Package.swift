// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "JarvisBridge",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "JarvisBridge", targets: ["JarvisBridge"]),
        .library(name: "JarvisBridgeCore", targets: ["JarvisBridgeCore"]),
    ],
    targets: [
        .target(
            name: "JarvisBridgeCore",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("ApplicationServices"),
                .linkedFramework("Carbon"),
                .linkedFramework("Network"),
                .linkedFramework("ScreenCaptureKit"),
                .linkedFramework("Security"),
                .linkedFramework("UniformTypeIdentifiers"),
            ]
        ),
        .executableTarget(
            name: "JarvisBridge",
            dependencies: ["JarvisBridgeCore"]
        ),
        .testTarget(
            name: "JarvisBridgeCoreTests",
            dependencies: ["JarvisBridgeCore"]
        ),
    ],
    swiftLanguageModes: [.v6]
)
