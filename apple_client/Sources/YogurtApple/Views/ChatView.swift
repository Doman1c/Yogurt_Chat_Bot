import SwiftUI
#if os(iOS)
import UIKit
#endif

public struct YogurtRootView: View {
    @ObservedObject private var chat: ChatStore
    @ObservedObject private var appearance: AppearanceStore
    private let assets: ImageAssets, tokens: TokenStore
    @State private var settingsOpen = false
    @State private var composer = ""
    @State private var discardConfirm = false
    @State private var navigationSelection: String?
    @Environment(\.scenePhase) private var scenePhase
    public init(chat: ChatStore, appearance: AppearanceStore, assets: ImageAssets, tokens: TokenStore) {
        self.chat = chat; self.appearance = appearance; self.assets = assets; self.tokens = tokens
    }
    public var body: some View {
        NavigationSplitView {
            List(selection: Binding<String?>(get: { navigationSelection }, set: { value in
                navigationSelection = value
                if value == "new" { chat.newConversation() }
                else if let value { Task { await chat.select(value) } }
            })) {
                NavigationLink(value: "new") { Label("New conversation", systemImage: "square.and.pencil") }
                ForEach(chat.snapshot.conversations) { conversation in
                    NavigationLink(value: conversation.id) {
                        HStack { Text(conversation.title).lineLimit(2); if chat.snapshot.selectedID == conversation.id { Image(systemName: "checkmark") } }
                    }
                }
            }.navigationTitle("Conversations")
                .toolbar { ToolbarItem { Button { settingsOpen = true } label: { Image(systemName: "gearshape") }.accessibilityLabel("Settings") } }
        } detail: {
            VStack(spacing: 0) {
                ZStack {
                    ChatBackground(settings: appearance.settings, assets: assets)
                    ScrollViewReader { scroll in
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 20) {
                                if chat.selected?.turns.isEmpty ?? true {
                                    Text("Start a conversation with \(appearance.settings.yogurtDisplayName).")
                                        .foregroundStyle(.secondary).padding(.vertical, 40)
                                }
                                ForEach(chat.selected?.turns ?? []) { turn in
                                    bubble(turn.userText, user: true)
                                    bubble(turn.assistantText.isEmpty && !turn.terminal ? "…" : turn.assistantText, user: false)
                                    if !turn.terminal { Text("Receiving response").font(.caption).foregroundStyle(.secondary) }
                                    else if ["aborted", "cancelled"].contains(turn.state) || turn.response?.phase == .failed || turn.response?.phase == .cancelled {
                                        Text("Response ended before completion").font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                                if let pending = chat.snapshot.pending { bubble(pending.request.text, user: true); Text("Send awaiting confirmation").font(.caption) }
                                Color.clear.frame(height: 1).id("bottom")
                            }.padding()
                        }.onChange(of: chat.selected?.turns.last?.assistantText) { _ in scroll.scrollTo("bottom", anchor: .bottom) }
                    }
                }
                if let notice = chat.notice {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(notice).font(.caption)
                        HStack {
                            if chat.snapshot.pending != nil {
                                Button("Retry same send") { Task { await chat.retrySend() } }.disabled(chat.sending)
                                Button("Dismiss pending send") { discardConfirm = true }.disabled(chat.sending)
                            } else {
                                Button("Reconnect") { chat.reconnect() }
                                Button("Reload history") { Task { await chat.launch() } }
                            }
                        }
                    }.padding(10)
                }
                HStack(alignment: .bottom) {
                    TextField("Message", text: $composer, axis: .vertical).lineLimit(1...6).textFieldStyle(.roundedBorder)
                    Button("Send") {
                        let text = composer
                        Task { if await chat.send(text, surface: surface), composer == text { composer = "" } }
                    }.buttonStyle(.borderedProminent)
                        .disabled(!chat.canSend || composer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || composer.unicodeScalars.count > 16000)
                }.padding()
            }.navigationTitle(appearance.settings.yogurtDisplayName)
        }
        .preferredColorScheme(appearance.settings.theme == .system ? nil : appearance.settings.theme == .dark ? .dark : .light)
        .sheet(isPresented: $settingsOpen) { AppearanceView(appearance: appearance, assets: assets, tokens: tokens).frame(minWidth: 320, idealWidth: 560, minHeight: 500) }
        .task { await chat.launch() }
        .onChange(of: scenePhase) { phase in
            if phase == .active { chat.reconnect() }
            else if phase == .background { Task { await chat.stopConsumers() } }
        }
        .confirmationDialog("Stop retrying this send? The server may already have accepted it. Reload history before sending it again.", isPresented: $discardConfirm) {
            Button("Dismiss locally", role: .destructive) { chat.discardPending() }
        }
    }
    private var surface: SurfaceType {
        #if os(macOS)
        return .mac
        #else
        return UIDevice.current.userInterfaceIdiom == .pad ? .ipad : .ios
        #endif
    }
    private func bubble(_ text: String, user: Bool) -> some View {
        HStack(alignment: .top, spacing: 10) {
            if user { Spacer(minLength: 24) }
            if !user { LocalAvatar(reference: appearance.settings.yogurtAvatar, symbol: "sparkle", assets: assets) }
            VStack(alignment: .leading, spacing: 4) {
                Text(user ? appearance.settings.userDisplayName : appearance.settings.yogurtDisplayName).font(.caption).foregroundStyle(.secondary)
                Text(text).textSelection(.enabled).font(.system(size: 16 * appearance.settings.fontScale))
                    .padding(12)
                    .background((user ? Color.accentColor : Color.secondary).opacity(appearance.settings.bubbleStyle == .plain ? 0 : 0.14 * appearance.settings.bubbleOpacity),
                                in: RoundedRectangle(cornerRadius: appearance.settings.bubbleCornerRadius))
                    .overlay { if appearance.settings.bubbleStyle == .outlined { RoundedRectangle(cornerRadius: appearance.settings.bubbleCornerRadius).stroke(Color.secondary.opacity(appearance.settings.bubbleOpacity)) } }
            }
            if user { LocalAvatar(reference: appearance.settings.userAvatar, symbol: "person.fill", assets: assets) }
            if !user { Spacer(minLength: 24) }
        }
    }
}
