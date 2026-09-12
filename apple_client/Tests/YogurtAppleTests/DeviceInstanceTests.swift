import XCTest
@testable import YogurtApple

@MainActor final class DeviceInstanceTests: XCTestCase {
    func url() -> URL { FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString).appendingPathComponent("device.json") }
    func testFirstCreationRestartAndAppearanceIsolation() async throws {
        let path = url()
        let first = try DeviceInstanceStore(url: path).loadOrCreate()
        XCTAssertNotNil(UUID(uuidString: first)); XCTAssertTrue(FileManager.default.fileExists(atPath: path.path))
        XCTAssertEqual(JSONFile<String>(path).load(), first)
        let appearance = AppearanceStore(disk: JSONFile(path.deletingLastPathComponent().appendingPathComponent("appearance.json")))
        appearance.update { $0.userDisplayName = "Changed user"; $0.yogurtDisplayName = "酸奶" }
        XCTAssertEqual(try DeviceInstanceStore(url: path).loadOrCreate(), first)
    }
    func testIntentionalSendsShareDeviceAcrossConversationsAndRestart() async throws {
        let path = url(), api = FakeAPI()
        let disk = JSONFile<ChatSnapshot>(path.deletingLastPathComponent().appendingPathComponent("chat.json"))
        let store = ChatStore(api: api, disk: disk, device: DeviceInstanceStore(url: path), pause: {})
        await store.send("first", surface: .mac)
        await store.stopConsumers(); store.newConversation()
        await store.send("second", surface: .mac); await store.stopConsumers()
        let restarted = ChatStore(api: api, disk: disk, device: DeviceInstanceStore(url: path), pause: {})
        restarted.newConversation(); await restarted.send("third", surface: .ipad); await restarted.stopConsumers()
        XCTAssertEqual(api.sends.count, 3)
        XCTAssertEqual(Set(api.sends.map { $0.request.clientMessageID }).count, 3)
        XCTAssertEqual(Set(api.sends.compactMap { $0.request.deviceInstanceID }), [try DeviceInstanceStore(url: path).loadOrCreate()])
    }
    func testCorruptMetadataRebuildsOpaqueIDAndPersistsIt() async throws {
        let path = url()
        try JSONFile<String>(path).save("secret-user-or-persona-not-a-uuid")
        let rebuilt = try DeviceInstanceStore(url: path).loadOrCreate()
        XCTAssertNotNil(UUID(uuidString: rebuilt)); XCTAssertFalse(rebuilt.contains("secret"))
        XCTAssertEqual(try DeviceInstanceStore(url: path).loadOrCreate(), rebuilt)
        try Data(repeating: 65, count: 1024).write(to: path, options: .atomic)
        let oversized = try DeviceInstanceStore(url: path).loadOrCreate()
        XCTAssertNotEqual(oversized, rebuilt); XCTAssertNotNil(UUID(uuidString: oversized))
        let blocked = url(); try FileManager.default.createDirectory(at: blocked.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data().write(to: blocked)
        XCTAssertThrowsError(try DeviceInstanceStore(url: blocked.appendingPathComponent("id.json")).loadOrCreate()) { error in
            XCTAssertEqual(error as? ClientError, .storage)
        }
    }
}
