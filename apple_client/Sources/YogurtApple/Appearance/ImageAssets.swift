import Foundation
import ImageIO
import CoreGraphics
import UniformTypeIdentifiers

public actor ImageAssets {
    public let root: URL
    private let cache = NSCache<NSString, CGImage>()
    public init(root: URL = AppPaths.root.appendingPathComponent("AppearanceAssets")) {
        self.root = root.standardizedFileURL.resolvingSymlinksInPath()
        cache.totalCostLimit = 64 * 1024 * 1024; cache.countLimit = 16
    }
    public func managedURL(_ name: String, folder: String) throws -> URL {
        guard ["originals", "avatars", "cache"].contains(folder),
              name.range(of: "^[A-Fa-f0-9-]{36}\\.(source|jpg)$", options: .regularExpression) != nil,
              UUID(uuidString: String(name.prefix(36))) != nil else { throw ClientError.image }
        // Resolve the existing parent separately: Foundation may leave symlinks
        // unresolved when resolving a path whose final file does not yet exist.
        let parent = root.appendingPathComponent(folder).standardizedFileURL.resolvingSymlinksInPath()
        guard parent.path.hasPrefix(root.path + "/") else { throw ClientError.image }
        let url = parent.appendingPathComponent(name).standardizedFileURL.resolvingSymlinksInPath()
        guard url.path.hasPrefix(root.path + "/") else { throw ClientError.image }
        return url
    }
    public nonisolated static func thumbnail(_ data: Data, maxPixels: Int) throws -> CGImage {
        guard data.count <= 32_000_000, let source = CGImageSourceCreateWithData(data as CFData, nil),
              let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = props[kCGImagePropertyPixelWidth] as? Int, let height = props[kCGImagePropertyPixelHeight] as? Int,
              width > 0, height > 0, Double(width) * Double(height) <= 100_000_000 else { throw ClientError.image }
        let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true, kCGImageSourceThumbnailMaxPixelSize: min(4096, max(64, maxPixels)),
            kCGImageSourceShouldCacheImmediately: true]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { throw ClientError.image }
        return image
    }
    public func preview(_ data: Data) throws -> CGImage { try Self.thumbnail(data, maxPixels: 1600) }
    private func write(_ data: Data, to url: URL) throws {
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            var directory = root; var flags = URLResourceValues(); flags.isExcludedFromBackup = true; try directory.setResourceValues(flags)
            try data.write(to: url, options: .atomic)
        } catch { throw ClientError.image }
    }
    public func save(_ data: Data, avatar: Bool, x: Double = 0.5, y: Double = 0.5, zoom: Double = 1) throws -> AssetReference {
        let normalized = try Self.thumbnail(data, maxPixels: avatar ? 2048 : 1600)
        let originals = root.appendingPathComponent("originals")
        let existing = (try? FileManager.default.contentsOfDirectory(at: originals, includingPropertiesForKeys: [.fileSizeKey])) ?? []
        let used = existing.reduce(0) { $0 + ((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
        guard used + data.count <= 200_000_000 else { throw ClientError.image }
        let id = UUID().uuidString
        let original = id + ".source"
        if avatar {
            guard let context = CGContext(data: nil, width: 512, height: 512, bitsPerComponent: 8, bytesPerRow: 0,
                space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { throw ClientError.image }
            let frame = AspectFill.frame(image: CGSize(width: normalized.width, height: normalized.height),
                                         viewport: CGSize(width: 512, height: 512), x: x, y: y, zoom: zoom)
            context.interpolationQuality = .high
            context.draw(normalized, in: CGRect(x: frame.minX, y: 512 - frame.maxY, width: frame.width, height: frame.height))
            guard let square = context.makeImage() else { throw ClientError.image }
            let encoded = NSMutableData()
            guard let destination = CGImageDestinationCreateWithData(encoded, UTType.jpeg.identifier as CFString, 1, nil) else { throw ClientError.image }
            CGImageDestinationAddImage(destination, square, [kCGImageDestinationLossyCompressionQuality: 0.9] as CFDictionary)
            guard CGImageDestinationFinalize(destination) else { throw ClientError.image }
            try write(encoded as Data, to: managedURL(id + ".jpg", folder: "avatars"))
        }
        try write(data, to: managedURL(original, folder: "originals")) // Original bytes stay untouched.
        return AssetReference(original: original, avatar: avatar ? id + ".jpg" : nil)
    }
    public func original(_ ref: AssetReference) throws -> Data {
        let url = try managedURL(ref.original, folder: "originals")
        guard let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize, size <= 32_000_000,
              let data = try? Data(contentsOf: url) else { throw ClientError.image }
        return data
    }
    public func display(_ ref: AssetReference, avatar: Bool, maxPixels: Int) -> CGImage? {
        let pixels = min(4096, max(256, ((maxPixels + 255) / 256) * 256))
        let name = avatar ? ref.avatar : ref.original
        guard let name else { return nil }
        guard let url = try? managedURL(name, folder: avatar ? "avatars" : "originals"),
              let values = try? url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey]),
              let size = values.fileSize, size <= 32_000_000 else { return nil }
        let key = "\(name):\(pixels):\(size):\(values.contentModificationDate?.timeIntervalSince1970 ?? 0)" as NSString
        if let cached = cache.object(forKey: key) { return cached }
        guard let data = try? Data(contentsOf: url), let image = try? Self.thumbnail(data, maxPixels: pixels) else { return nil }
        cache.setObject(image, forKey: key, cost: image.bytesPerRow * image.height)
        return image
    }
}
