import Foundation

public enum AppPaths {
    public static var root: URL {
        (FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
         ?? FileManager.default.temporaryDirectory).appendingPathComponent("Yogurt", isDirectory: true)
    }
}
public final class JSONFile<Value: Codable> {
    let url: URL
    public init(_ url: URL) { self.url = url }
    public func load() -> Value? {
        guard let data = try? Data(contentsOf: url), data.count <= 16_000_000 else { return nil }
        return try? JSONDecoder().decode(Value.self, from: data)
    }
    public func save(_ value: Value) throws {
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            var directory = url.deletingLastPathComponent(); var flags = URLResourceValues(); flags.isExcludedFromBackup = true
            try directory.setResourceValues(flags)
            let data = try JSONEncoder().encode(value)
            guard data.count <= 16_000_000 else { throw ClientError.storage }
            try data.write(to: url, options: .atomic)
        } catch { throw ClientError.storage }
    }
}
