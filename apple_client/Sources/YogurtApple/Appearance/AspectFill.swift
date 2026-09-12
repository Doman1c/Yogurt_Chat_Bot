import Foundation
import CoreGraphics

public enum AspectFill {
    public static func frame(image: CGSize, viewport: CGSize, x: Double, y: Double, zoom: Double) -> CGRect {
        guard image.width > 0, image.height > 0, viewport.width > 0, viewport.height > 0 else { return .zero }
        let scale = max(viewport.width / image.width, viewport.height / image.height) * bounded(zoom, 1...4, 1)
        let size = CGSize(width: image.width * scale, height: image.height * scale)
        return CGRect(x: -(size.width - viewport.width) * bounded(x, 0...1, 0.5),
                      y: -(size.height - viewport.height) * bounded(y, 0...1, 0.5), width: size.width, height: size.height)
    }
}
