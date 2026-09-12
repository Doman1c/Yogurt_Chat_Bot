import XCTest
import ImageIO
import UniformTypeIdentifiers
@testable import YogurtApple

final class AppearanceTests: XCTestCase {
    func imageData(orientation: Int = 1) throws -> Data {
        let ctx = try XCTUnwrap(CGContext(data: nil, width: 80, height: 40, bitsPerComponent: 8, bytesPerRow: 0,
            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue))
        ctx.setFillColor(CGColor(red: 1, green: 0, blue: 0, alpha: 1)); ctx.fill(CGRect(x: 0, y: 0, width: 80, height: 40))
        let image = try XCTUnwrap(ctx.makeImage()), data = NSMutableData()
        let dest = try XCTUnwrap(CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil))
        CGImageDestinationAddImage(dest, image, [kCGImagePropertyOrientation: orientation] as CFDictionary)
        XCTAssertTrue(CGImageDestinationFinalize(dest)); return data as Data
    }
    func testAvatarSquareOriginalPreservedAndOrientationNormalized() async throws {
        let assets = ImageAssets(root: FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString))
        let original = try imageData(orientation: 6)
        let normalized = try ImageAssets.thumbnail(original, maxPixels: 512)
        XCTAssertLessThan(normalized.width, normalized.height)
        let ref = try await assets.save(original, avatar: true, x: 0.3, y: 0.7, zoom: 1.2)
        let source = try await assets.original(ref); XCTAssertEqual(source, original)
        let avatar = await assets.display(ref, avatar: true, maxPixels: 512)
        XCTAssertEqual(avatar?.width, 512); XCTAssertEqual(avatar?.height, 512)
    }
    func testAspectFillAllViewportsNoStretchAndNormalizedFocalPoint() {
        let source = CGSize(width: 4000, height: 3000)
        for viewport in [CGSize(width: 390, height: 844), CGSize(width: 844, height: 390), CGSize(width: 1024, height: 1366), CGSize(width: 1600, height: 700)] {
            let frame = AspectFill.frame(image: source, viewport: viewport, x: 0.42, y: 0.3, zoom: 1.2)
            XCTAssertEqual(frame.width / frame.height, source.width / source.height, accuracy: 0.00001)
            XCTAssertGreaterThanOrEqual(frame.width, viewport.width); XCTAssertGreaterThanOrEqual(frame.height, viewport.height)
            XCTAssertLessThanOrEqual(frame.minX, 0); XCTAssertGreaterThanOrEqual(frame.maxX, viewport.width - 0.0001)
        }
        var settings = AppearanceSettings(); settings.backgroundFocalPointX = -99; settings.backgroundFocalPointY = 90
        settings.backgroundScale = .infinity; settings.normalize()
        XCTAssertEqual(settings.backgroundFocalPointX, 0); XCTAssertEqual(settings.backgroundFocalPointY, 1); XCTAssertEqual(settings.backgroundScale, 1)
    }
    func testMissingCorruptAndEscapingAssetsFailSafely() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let assets = ImageAssets(root: directory)
        let reference = try await assets.save(imageData(), avatar: true)
        let url = try await assets.managedURL(reference.avatar!, folder: "avatars")
        try Data("corrupt".utf8).write(to: url, options: .atomic)
        let corrupt = await assets.display(reference, avatar: true, maxPixels: 512); XCTAssertNil(corrupt)
        try FileManager.default.removeItem(at: url)
        let missing = await assets.display(reference, avatar: true, maxPixels: 512); XCTAssertNil(missing)
        for name in ["../secret", "/tmp/escape", "../../" + reference.original] {
            do { _ = try await assets.managedURL(name, folder: "originals"); XCTFail() } catch {}
        }
        let escaped = ImageAssets(root: directory.appendingPathComponent("escape"))
        try FileManager.default.createDirectory(at: directory.appendingPathComponent("escape"), withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: directory.appendingPathComponent("escape/originals"), withDestinationURL: FileManager.default.temporaryDirectory)
        do { _ = try await escaped.managedURL(reference.original, folder: "originals"); XCTFail() } catch {}
    }
    @MainActor func testSettingsRoundTripNeverChangesWireIdentity() throws {
        let request = try PendingSend(TurnRequest(conversationID: "c1", text: "hello", surface: .mac))
        let store = AppearanceStore(disk: JSONFile(FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("appearance.json")))
        store.update { $0.yogurtDisplayName = "酸奶"; $0.userDisplayName = "My display name"; $0.chatBackground = AssetReference(original: UUID().uuidString + ".source") }
        let roundtrip = try JSONDecoder().decode(AppearanceSettings.self, from: JSONEncoder().encode(store.settings))
        XCTAssertEqual(roundtrip, store.settings)
        XCTAssertEqual(try PendingSend(request.request).body, request.body)
        let body = String(decoding: request.body, as: UTF8.self)
        for forbidden in ["酸奶", "My display name", "source", "persona_id", "user_id", "avatar", "background", "token"] { XCTAssertFalse(body.contains(forbidden)) }
    }
}
