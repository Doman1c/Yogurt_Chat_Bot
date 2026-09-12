// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "YogurtApple", platforms: [.macOS(.v13), .iOS(.v16)],
    products: [.library(name: "YogurtApple", targets: ["YogurtApple"])],
    targets: [.target(name: "YogurtApple"), .testTarget(name: "YogurtAppleTests", dependencies: ["YogurtApple"])])
