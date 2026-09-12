import SwiftUI
import PhotosUI

private struct ImageDraft: Identifiable {
    let id = UUID()
    let data: Data, image: CGImage, kind: ImageKind
}
private enum ImageKind { case user, yogurt, background }
public struct AppearanceView: View {
    @ObservedObject var appearance: AppearanceStore
    let assets: ImageAssets
    let tokens: TokenStore
    @State private var userPhoto: PhotosPickerItem?, yogurtPhoto: PhotosPickerItem?, backgroundPhoto: PhotosPickerItem?
    @State private var draft: ImageDraft?
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss
    @State private var token = ""
    @State private var savedToken = false
    @State private var importing = false
    public init(appearance: AppearanceStore, assets: ImageAssets, tokens: TokenStore) {
        self.appearance = appearance; self.assets = assets; self.tokens = tokens
    }
    private func binding<T>(_ path: WritableKeyPath<AppearanceSettings, T>) -> Binding<T> {
        Binding(get: { appearance.settings[keyPath: path] }, set: { value in appearance.update { $0[keyPath: path] = value } })
    }
    public var body: some View {
        NavigationStack {
            Form {
                Section("Profile") {
                    TextField("My Display Name", text: binding(\.userDisplayName))
                    HStack { LocalAvatar(reference: appearance.settings.userAvatar, symbol: "person.fill", assets: assets)
                        PhotosPicker("My Avatar", selection: $userPhoto, matching: .images) }
                    Button("Re-crop original") { edit(appearance.settings.userAvatar, .user) }.disabled(appearance.settings.userAvatar == nil)
                    Button("Use default avatar") { appearance.update { $0.userAvatar = nil } }
                }
                Section("Yogurt") {
                    TextField("Display Name", text: binding(\.yogurtDisplayName))
                    HStack { LocalAvatar(reference: appearance.settings.yogurtAvatar, symbol: "sparkle", assets: assets)
                        PhotosPicker("Avatar", selection: $yogurtPhoto, matching: .images) }
                    Button("Re-crop original") { edit(appearance.settings.yogurtAvatar, .yogurt) }.disabled(appearance.settings.yogurtAvatar == nil)
                    Button("Use default avatar") { appearance.update { $0.yogurtAvatar = nil } }
                }
                Section("Appearance") {
                    PhotosPicker("Chat Background", selection: $backgroundPhoto, matching: .images)
                    Button("Background Position") { edit(appearance.settings.chatBackground, .background) }.disabled(appearance.settings.chatBackground == nil)
                    Button("Use default background") { appearance.update { $0.chatBackground = nil } }
                    labelledSlider("Blur", binding(\.backgroundBlur), 0...24)
                    labelledSlider("Dim", binding(\.backgroundDim), 0...0.8)
                    Picker("Bubble Style", selection: binding(\.bubbleStyle)) {
                        ForEach(AppearanceSettings.BubbleStyle.allCases, id: \.self) { Text($0.rawValue.capitalized).tag($0) }
                    }
                    labelledSlider("Bubble Opacity", binding(\.bubbleOpacity), 0.2...1)
                    labelledSlider("Corner Radius", binding(\.bubbleCornerRadius), 0...32)
                    labelledSlider("Font Size", binding(\.fontScale), 0.8...1.6)
                    Picker("Theme", selection: binding(\.theme)) {
                        ForEach(AppearanceSettings.Theme.allCases, id: \.self) { Text($0.rawValue.capitalized).tag($0) }
                    }
                    ZStack {
                        ChatBackground(settings: appearance.settings, assets: assets)
                        VStack(alignment: .leading) {
                            Text(appearance.settings.yogurtDisplayName).font(.caption)
                            Text("A local preview of your conversation.").padding(12)
                                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: appearance.settings.bubbleCornerRadius))
                        }.font(.system(size: 16 * appearance.settings.fontScale)).padding()
                    }.frame(height: 150)
                }
                Section("Gateway credential") {
                    SecureField("Bearer token", text: $token)
                    Button("Save to Keychain") {
                        do { try tokens.save(token); token = ""; savedToken = true; error = nil }
                        catch { error = ClientError.credentials.localizedDescription }
                    }
                    if savedToken { Text("Saved locally. Return to chat and reconnect.").font(.caption) }
                    Text("Development: 127.0.0.1:17622 · Mac and simulators only").font(.caption)
                }
                if importing { ProgressView("Preparing image") }
                if let error = error ?? appearance.error { Text(error).foregroundStyle(.red) }
            }.formStyle(.grouped).navigationTitle("Settings")
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
                .onChange(of: userPhoto) { value in importPhoto(value, .user) }
                .onChange(of: yogurtPhoto) { value in importPhoto(value, .yogurt) }
                .onChange(of: backgroundPhoto) { value in importPhoto(value, .background) }
                .sheet(item: $draft) { draft in
                    CropEditor(image: draft.image, avatar: draft.kind != .background,
                        confirm: { x, y, zoom in
                            Task {
                                do {
                                    let asset = try await assets.save(draft.data, avatar: draft.kind != .background, x: x, y: y, zoom: zoom)
                                    appearance.update {
                                        switch draft.kind {
                                        case .user: $0.userAvatar = asset
                                        case .yogurt: $0.yogurtAvatar = asset
                                        case .background:
                                            $0.chatBackground = asset; $0.backgroundFocalPointX = x; $0.backgroundFocalPointY = y; $0.backgroundScale = zoom
                                        }
                                    }
                                } catch { self.error = ClientError.image.localizedDescription }
                            }
                        }, x: draft.kind == .background ? appearance.settings.backgroundFocalPointX : 0.5,
                        y: draft.kind == .background ? appearance.settings.backgroundFocalPointY : 0.5,
                        zoom: draft.kind == .background ? appearance.settings.backgroundScale : 1)
                }
        }
    }
    private func labelledSlider(_ title: String, _ value: Binding<Double>, _ range: ClosedRange<Double>) -> some View {
        HStack { Text(title); Slider(value: value, in: range).accessibilityLabel(title) }
    }
    private func importPhoto(_ item: PhotosPickerItem?, _ kind: ImageKind) {
        guard let item else { return }; importing = true
        Task {
            defer { importing = false }
            do {
                guard let data = try await item.loadTransferable(type: Data.self) else { throw ClientError.image }
                draft = ImageDraft(data: data, image: try await assets.preview(data), kind: kind); error = nil
            } catch { error = ClientError.image.localizedDescription }
        }
    }
    private func edit(_ reference: AssetReference?, _ kind: ImageKind) {
        guard let reference else { return }
        Task {
            do { let data = try await assets.original(reference); draft = ImageDraft(data: data, image: try await assets.preview(data), kind: kind) }
            catch { error = ClientError.image.localizedDescription }
        }
    }
}
