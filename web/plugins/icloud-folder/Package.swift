// swift-tools-version: 5.9
import PackageDescription

// The name follows Capacitor's rule for local plugins: the npm name,
// "tastydesk-icloud-folder", in PascalCase.
let package = Package(
  name: "TastydeskIcloudFolder",
  platforms: [.iOS(.v15)],
  products: [
    .library(
      name: "TastydeskIcloudFolder",
      targets: ["ICloudFolderPlugin"]
    )
  ],
  dependencies: [
    .package(url: "https://github.com/ionic-team/capacitor-swift-pm.git", from: "8.0.0")
  ],
  targets: [
    .target(
      name: "ICloudFolderPlugin",
      dependencies: [
        .product(name: "Capacitor", package: "capacitor-swift-pm"),
        .product(name: "Cordova", package: "capacitor-swift-pm")
      ],
      path: "ios/Sources/ICloudFolderPlugin"
    )
  ]
)
