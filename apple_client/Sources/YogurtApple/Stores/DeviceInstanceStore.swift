import Foundation

/// Installation metadata only. Never used for authentication or Core identity.
public final class DeviceInstanceStore {
    private static let lock = NSLock()
    private let url: URL
    public init(url: URL = AppPaths.root.appendingPathComponent("device-instance.json")) { self.url = url }
    public func loadOrCreate() throws -> String {
        Self.lock.lock(); defer { Self.lock.unlock() }
        let file = JSONFile<String>(url)
        if let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize, size <= 128,
           let stored = file.load(), let uuid = UUID(uuidString: stored), uuid.uuidString == stored {
            return stored
        }
        // Missing/corrupt metadata gets a fresh opaque value, never derived from
        // profile names, device hardware, account identifiers, or corrupt bytes.
        let value = UUID().uuidString
        try file.save(value) // Do not return an ID that failed to persist.
        return value
    }
}
