import AppKit

let folder = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
            bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
            colorSpaceName: .deviceRGB, bytesPerRow: pixels * 4, bitsPerPixel: 32)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        let context = NSGraphicsContext.current!.cgContext
        context.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
        let frame = NSRect(x: 80, y: 80, width: 864, height: 864)
        let shape = NSBezierPath(roundedRect: frame, xRadius: 195, yRadius: 195)
        let gradient = NSGradient(starting: NSColor(srgbRed: 0.18, green: 0.62, blue: 0.98, alpha: 1),
                                  ending: NSColor(srgbRed: 0.24, green: 0.30, blue: 0.76, alpha: 1))!
        gradient.draw(in: shape, angle: -55)
        let plane = NSBezierPath()
        plane.move(to: NSPoint(x: 250, y: 540))
        plane.line(to: NSPoint(x: 783, y: 766))
        plane.curve(to: NSPoint(x: 801, y: 742), controlPoint1: NSPoint(x: 802, y: 775), controlPoint2: NSPoint(x: 810, y: 766))
        plane.line(to: NSPoint(x: 707, y: 312))
        plane.curve(to: NSPoint(x: 671, y: 300), controlPoint1: NSPoint(x: 703, y: 289), controlPoint2: NSPoint(x: 686, y: 287))
        plane.line(to: NSPoint(x: 526, y: 411))
        plane.line(to: NSPoint(x: 455, y: 342))
        plane.line(to: NSPoint(x: 463, y: 464))
        plane.line(to: NSPoint(x: 700, y: 679))
        plane.line(to: NSPoint(x: 409, y: 502))
        plane.line(to: NSPoint(x: 263, y: 549))
        plane.close()
        NSColor.white.setFill(); plane.fill()
        let badge = NSBezierPath(ovalIn: NSRect(x: 666, y: 138, width: 220, height: 220))
        NSColor(srgbRed: 0.23, green: 0.77, blue: 0.65, alpha: 1).setFill(); badge.fill()
        let check = NSBezierPath()
        check.move(to: NSPoint(x: 725, y: 247))
        check.line(to: NSPoint(x: 761, y: 211))
        check.line(to: NSPoint(x: 825, y: 282))
        check.lineWidth = 19; check.lineCapStyle = .round; check.lineJoinStyle = .round
        NSColor.white.setStroke(); check.stroke()
        NSGraphicsContext.restoreGraphicsState()
        let name = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent(name))
    }
}
