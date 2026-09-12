import SwiftUI
import YogurtApple

@main struct YogurtApp: App {
    @StateObject private var chat: ChatStore
    @StateObject private var appearance = AppearanceStore()
    private let tokens = KeychainTokenStore()
    private let assets = ImageAssets()
    init() { _chat = StateObject(wrappedValue: ChatStore(api: NativeGateway(token: KeychainTokenStore()))) }
    var body: some Scene { WindowGroup { YogurtRootView(chat: chat, appearance: appearance, assets: assets, tokens: tokens) } }
}
