import AppKit
import Foundation

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)
func render(_ size: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: rep)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.cgContext.clear(CGRect(x: 0, y: 0, width: CGFloat(size), height: CGFloat(size)))
    context.cgContext.scaleBy(x: CGFloat(size) / 1024, y: CGFloat(size) / 1024)
    let shape = NSBezierPath(roundedRect: NSRect(x: 78, y: 78, width: 868, height: 868), xRadius: 198, yRadius: 198)
    let gradient = NSGradient(starting: NSColor(srgbRed: 0.07, green: 0.36, blue: 0.28, alpha: 1),
        ending: NSColor(srgbRed: 0.13, green: 0.56, blue: 0.43, alpha: 1))!
    gradient.draw(in: shape, angle: 90)
    NSColor.white.withAlphaComponent(0.13).setStroke()
    shape.lineWidth = 3
    shape.stroke()
    let points: [NSPoint] = [NSPoint(x:512,y:230),NSPoint(x:590,y:434),NSPoint(x:794,y:512),
        NSPoint(x:590,y:590),NSPoint(x:512,y:794),NSPoint(x:434,y:590),NSPoint(x:230,y:512),NSPoint(x:434,y:434)]
    let mark = NSBezierPath()
    mark.move(to: points[0])
    for point in points.dropFirst() { mark.line(to: point) }
    mark.close()
    mark.lineWidth = 28
    mark.lineJoinStyle = .round
    mark.lineCapStyle = .round
    NSColor.white.setStroke()
    mark.stroke()
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}
for n in [16,32,128,256,512] {
    try render(n).write(to: destination.appendingPathComponent("icon_\(n)x\(n).png"))
    try render(n*2).write(to: destination.appendingPathComponent("icon_\(n)x\(n)@2x.png"))
}
