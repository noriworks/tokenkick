import AppKit
import Foundation

let output = URL(fileURLWithPath: CommandLine.arguments.count > 1
    ? CommandLine.arguments[1]
    : "packaging/macos/TokenKickIcon-source.png")

let size: CGFloat = 2048
let unit = size / 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()

NSColor.clear.setFill()
NSRect(x: 0, y: 0, width: size, height: size).fill()

// macOS adaptation of the landing-page header mark: the dark rounded plate is
// the intended icon shape, and the outer canvas remains transparent.
let outerPlate = NSBezierPath(
    roundedRect: NSRect(x: 110 * unit, y: 110 * unit, width: 804 * unit, height: 804 * unit),
    xRadius: 190 * unit,
    yRadius: 190 * unit
)
NSColor(red: 39.0 / 255.0, green: 67.0 / 255.0, blue: 52.0 / 255.0, alpha: 1).setFill()
outerPlate.fill()

let innerPlate = NSBezierPath(
    roundedRect: NSRect(x: 126 * unit, y: 126 * unit, width: 772 * unit, height: 772 * unit),
    xRadius: 174 * unit,
    yRadius: 174 * unit
)
NSColor(red: 11.0 / 255.0, green: 16.0 / 255.0, blue: 14.0 / 255.0, alpha: 1).setFill()
innerPlate.fill()

let green = NSColor(red: 91.0 / 255.0, green: 1.0, blue: 154.0 / 255.0, alpha: 1)
green.setStroke()

let viewScale: CGFloat = 33 * unit
let originX: CGFloat = 512 * unit - 12 * viewScale
let originY: CGFloat = 512 * unit - 12 * viewScale

func point(_ x: CGFloat, _ y: CGFloat) -> NSPoint {
    NSPoint(x: originX + x * viewScale, y: originY + (24 - y) * viewScale)
}

let mark = NSBezierPath()
mark.lineWidth = 2.4 * viewScale
mark.lineCapStyle = .square
mark.lineJoinStyle = .miter
mark.move(to: point(5, 12))
mark.line(to: point(14, 12))
mark.move(to: point(10, 8))
mark.line(to: point(14, 12))
mark.line(to: point(10, 16))
mark.move(to: point(16.5, 7))
mark.line(to: point(16.5, 17))
mark.stroke()

image.unlockFocus()

guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else {
    fputs("error: could not encode icon PNG\n", stderr)
    exit(1)
}

do {
    try FileManager.default.createDirectory(
        at: output.deletingLastPathComponent(),
        withIntermediateDirectories: true
    )
    try png.write(to: output)
} catch {
    fputs("error: could not write \(output.path): \(error)\n", stderr)
    exit(1)
}
