import Foundation
import Combine

public struct AssetReference: Codable, Equatable {
    public let original: String
    public var avatar: String?
    public init(original: String, avatar: String? = nil) { self.original = original; self.avatar = avatar }
}
public struct AppearanceSettings: Codable, Equatable {
    public enum BubbleStyle: String, Codable, CaseIterable { case soft, outlined, plain }
    public enum Theme: String, Codable, CaseIterable { case system, light, dark }
    public var userDisplayName = "You", yogurtDisplayName = "Yogurt"
    public var userAvatar: AssetReference?, yogurtAvatar: AssetReference?, chatBackground: AssetReference?
    public var backgroundFocalPointX = 0.5, backgroundFocalPointY = 0.5, backgroundScale = 1.0
    public var backgroundBlur = 0.0, backgroundDim = 0.15
    public var bubbleStyle: BubbleStyle = .soft
    public var bubbleOpacity = 0.9, bubbleCornerRadius = 16.0, fontScale = 1.0
    public var theme: Theme = .system
    public init() {}
    public mutating func normalize() {
        userDisplayName = String(userDisplayName.prefix(80)); yogurtDisplayName = String(yogurtDisplayName.prefix(80))
        backgroundFocalPointX = bounded(backgroundFocalPointX, 0...1, 0.5)
        backgroundFocalPointY = bounded(backgroundFocalPointY, 0...1, 0.5)
        backgroundScale = bounded(backgroundScale, 1...4, 1)
        backgroundBlur = bounded(backgroundBlur, 0...24, 0)
        backgroundDim = bounded(backgroundDim, 0...0.8, 0.15)
        bubbleOpacity = bounded(bubbleOpacity, 0.2...1, 0.9)
        bubbleCornerRadius = bounded(bubbleCornerRadius, 0...32, 16)
        fontScale = bounded(fontScale, 0.8...1.6, 1)
    }
}
func bounded(_ value: Double, _ range: ClosedRange<Double>, _ fallback: Double) -> Double {
    value.isFinite ? min(range.upperBound, max(range.lowerBound, value)) : fallback
}
@MainActor public final class AppearanceStore: ObservableObject {
    @Published public private(set) var settings: AppearanceSettings
    @Published public private(set) var error: String?
    private let disk: JSONFile<AppearanceSettings>
    public init(disk: JSONFile<AppearanceSettings> = JSONFile(AppPaths.root.appendingPathComponent("appearance.json"))) {
        self.disk = disk; var value = disk.load() ?? AppearanceSettings(); value.normalize(); settings = value
    }
    public func update(_ edit: (inout AppearanceSettings) -> Void) {
        var value = settings; edit(&value); value.normalize(); settings = value
        do { try disk.save(value); error = nil } catch { self.error = ClientError.storage.localizedDescription }
    }
}
