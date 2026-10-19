import AppKit

// Render without Finder automation so packaging also works on CI runners.
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 640, pixelsHigh: 420,
    bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
    colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
bitmap.size = NSSize(width: 640, height: 420)
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSColor(srgbRed: 0.95, green: 0.97, blue: 1, alpha: 1).setFill()
NSRect(x: 0, y: 0, width: 640, height: 420).fill()

func label(_ text: String, y: CGFloat, size: CGFloat, bold: Bool = false) {
    let style = NSMutableParagraphStyle()
    style.alignment = .center
    (text as NSString).draw(in: NSRect(x: 20, y: y, width: 600, height: 36), withAttributes: [
        .font: bold ? NSFont.boldSystemFont(ofSize: size) : NSFont.systemFont(ofSize: size),
        .foregroundColor: NSColor(srgbRed: 0.14, green: 0.22, blue: 0.36, alpha: 1),
        .paragraphStyle: style,
    ])
}
label("Telegram Focus", y: 343, size: 28, bold: true)
label("Перетащите приложение в Applications", y: 300, size: 18)

let arrow = NSBezierPath()
arrow.move(to: NSPoint(x: 280, y: 210))
arrow.line(to: NSPoint(x: 360, y: 210))
arrow.move(to: NSPoint(x: 343, y: 227))
arrow.line(to: NSPoint(x: 360, y: 210))
arrow.line(to: NSPoint(x: 343, y: 193))
arrow.lineWidth = 5
arrow.lineCapStyle = .round
arrow.lineJoinStyle = .round
NSColor(srgbRed: 0.25, green: 0.49, blue: 0.82, alpha: 1).setStroke()
arrow.stroke()
NSGraphicsContext.restoreGraphicsState()
try bitmap.representation(using: .png, properties: [:])!
    .write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
