import SwiftUI

struct FocalImage: View {
    let image: CGImage
    var x: Double = 0.5, y: Double = 0.5, zoom: Double = 1
    var body: some View {
        GeometryReader { proxy in
            let frame = AspectFill.frame(image: CGSize(width: image.width, height: image.height), viewport: proxy.size, x: x, y: y, zoom: zoom)
            Image(decorative: image, scale: 1).resizable().interpolation(.high)
                .frame(width: frame.width, height: frame.height).offset(x: frame.minX, y: frame.minY)
        }.clipped()
    }
}
struct LocalAvatar: View {
    let reference: AssetReference?
    let symbol: String
    let assets: ImageAssets
    @State private var image: CGImage?
    var body: some View {
        ZStack {
            Circle().fill(Color.secondary.opacity(0.15))
            if let image { Image(decorative: image, scale: 1).resizable().scaledToFill() }
            else { Image(systemName: symbol).foregroundStyle(.secondary) }
        }.frame(width: 36, height: 36).clipShape(Circle())
            .task(id: reference?.avatar) {
                if let reference { image = await assets.display(reference, avatar: true, maxPixels: 256) } else { image = nil }
            }
    }
}
struct ChatBackground: View {
    let settings: AppearanceSettings
    let assets: ImageAssets
    @Environment(\.displayScale) private var displayScale
    @State private var image: CGImage?
    var body: some View {
        GeometryReader { geometry in
            let pixels = Int(min(4096, max(256, max(geometry.size.width, geometry.size.height) * displayScale)))
            ZStack {
                Color.primary.opacity(0.035)
                if let image {
                    FocalImage(image: image, x: settings.backgroundFocalPointX, y: settings.backgroundFocalPointY, zoom: settings.backgroundScale)
                        .blur(radius: settings.backgroundBlur)
                    Color.black.opacity(settings.backgroundDim)
                }
            }.clipped().task(id: "\(settings.chatBackground?.original ?? "none"):\((pixels + 255) / 256)") {
                if let ref = settings.chatBackground { image = await assets.display(ref, avatar: false, maxPixels: pixels) }
                else { image = nil }
            }
        }.allowsHitTesting(false)
    }
}
struct CropEditor: View {
    let image: CGImage, avatar: Bool
    let confirm: (Double, Double, Double) -> Void
    @Environment(\.dismiss) private var dismiss
    @State var x: Double = 0.5
    @State var y: Double = 0.5
    @State var zoom: Double = 1
    @State private var dragStart: CGPoint?
    @State private var zoomStart: Double?
    @State private var previewRatio = 0
    private let ratios: [Double] = [0.6, 1.7, 1.33, 2.1]
    var body: some View {
        VStack(spacing: 16) {
            Text(avatar ? "Crop avatar" : "Position background").font(.headline)
            GeometryReader { proxy in
                FocalImage(image: image, x: x, y: y, zoom: zoom)
                    .overlay { if avatar { Circle().stroke(.white, lineWidth: 2).padding(2) } }
                    .contentShape(Rectangle())
                    .gesture(DragGesture().onChanged { value in
                        if dragStart == nil { dragStart = CGPoint(x: x, y: y) }
                        let frame = AspectFill.frame(image: CGSize(width: image.width, height: image.height), viewport: proxy.size, x: x, y: y, zoom: zoom)
                        x = bounded(Double(dragStart!.x) - value.translation.width / max(1, frame.width - proxy.size.width), 0...1, 0.5)
                        y = bounded(Double(dragStart!.y) - value.translation.height / max(1, frame.height - proxy.size.height), 0...1, 0.5)
                    }.onEnded { _ in dragStart = nil })
                    .simultaneousGesture(MagnificationGesture().onChanged { value in
                        if zoomStart == nil { zoomStart = zoom }
                        zoom = bounded(zoomStart! * value, 1...4, 1)
                    }.onEnded { _ in zoomStart = nil })
            }.aspectRatio(avatar ? 1 : ratios[previewRatio], contentMode: .fit).frame(maxHeight: 320)
            if !avatar {
                Picker("Preview", selection: $previewRatio) {
                    Text("Portrait").tag(0); Text("Landscape").tag(1); Text("iPad").tag(2); Text("Wide").tag(3)
                }.pickerStyle(.segmented)
            }
            HStack { Text("Zoom"); Slider(value: $zoom, in: 1...4) }
            HStack { Text("Horizontal"); Slider(value: $x, in: 0...1) }
            HStack { Text("Vertical"); Slider(value: $y, in: 0...1) }
            HStack {
                Button("Cancel") { dismiss() }
                Button("Reset") { x = 0.5; y = 0.5; zoom = 1 }
                Spacer()
                Button("Use image") { confirm(x, y, zoom); dismiss() }.buttonStyle(.borderedProminent)
            }
        }.padding().frame(minWidth: 280, idealWidth: 520)
    }
}
