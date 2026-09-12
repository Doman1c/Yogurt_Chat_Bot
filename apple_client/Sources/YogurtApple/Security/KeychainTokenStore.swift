import Foundation
import Security

public protocol TokenStore { func read() throws -> String; func save(_ token: String) throws }
public struct KeychainTokenStore: TokenStore {
    private let service = "org.yogurt.native.gateway"
    public init() {}
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
         kSecAttrAccount as String: "local-owner", kSecAttrSynchronizable as String: false]
    }
    public func read() throws -> String {
        var q = query; q[kSecReturnData as String] = true; q[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data, let token = String(data: data, encoding: .utf8), !token.isEmpty
        else { throw ClientError.credentials }
        return token
    }
    public func save(_ token: String) throws {
        guard token.count >= 32, !token.contains("\n"), !token.contains("\r") else { throw ClientError.credentials }
        let attributes: [String: Any] = [kSecValueData as String: Data(token.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            guard SecItemAdd(query.merging(attributes) { _, new in new } as CFDictionary, nil) == errSecSuccess
            else { throw ClientError.credentials }
        } else if status != errSecSuccess { throw ClientError.credentials }
    }
}
