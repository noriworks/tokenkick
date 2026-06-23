import AppKit
import Foundation

guard CommandLine.arguments.count >= 3 else {
    fputs("usage: prepare_tkicons_icon.swift input.png output.png\n", stderr)
    exit(2)
}

let input = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2])

guard let image = NSImage(contentsOf: input) else {
    fputs("error: could not read icon source at \(input.path)\n", stderr)
    exit(1)
}

let pixelsWide = 1024
let pixelsHigh = 1024
guard let bitmap = NSBitmapImageRep(
    bitmapDataPlanes: nil,
    pixelsWide: pixelsWide,
    pixelsHigh: pixelsHigh,
    bitsPerSample: 8,
    samplesPerPixel: 4,
    hasAlpha: true,
    isPlanar: false,
    colorSpaceName: .deviceRGB,
    bytesPerRow: 0,
    bitsPerPixel: 0
) else {
    fputs("error: could not allocate icon bitmap\n", stderr)
    exit(1)
}

NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
NSColor.clear.setFill()
NSRect(x: 0, y: 0, width: pixelsWide, height: pixelsHigh).fill()
image.draw(
    in: NSRect(x: 0, y: 0, width: pixelsWide, height: pixelsHigh),
    from: .zero,
    operation: .sourceOver,
    fraction: 1.0
)
NSGraphicsContext.restoreGraphicsState()

guard let bitmapData = bitmap.bitmapData else {
    fputs("error: could not access icon bitmap data\n", stderr)
    exit(1)
}
let bytesPerRow = bitmap.bytesPerRow
let samplesPerPixel = bitmap.samplesPerPixel
guard samplesPerPixel >= 4 else {
    fputs("error: expected RGBA icon bitmap\n", stderr)
    exit(1)
}

for y in 0..<pixelsHigh {
    for x in 0..<pixelsWide {
        let offset = y * bytesPerRow + x * samplesPerPixel
        let red = CGFloat(bitmapData[offset]) / 255.0
        let green = CGFloat(bitmapData[offset + 1]) / 255.0
        let blue = CGFloat(bitmapData[offset + 2]) / 255.0
        let alpha = CGFloat(bitmapData[offset + 3]) / 255.0
        let maxChannel = max(red, green, blue)
        let minChannel = min(red, green, blue)
        let saturation = maxChannel == 0 ? 0 : (maxChannel - minChannel) / maxChannel

        // Some exported app-icon packs flatten transparent corners onto a
        // white/gray checker preview. Treat only near-neutral bright pixels
        // as background, preserving the dark plate and green mark.
        if alpha > 0.95, maxChannel > 0.68, saturation < 0.18 {
            bitmapData[offset] = 0
            bitmapData[offset + 1] = 0
            bitmapData[offset + 2] = 0
            bitmapData[offset + 3] = 0
        }
    }
}

guard let png = bitmap.representation(using: .png, properties: [:]) else {
    fputs("error: could not encode cleaned icon PNG\n", stderr)
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
