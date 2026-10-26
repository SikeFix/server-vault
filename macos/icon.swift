// 生成应用图标 PNG:渐变圆角底 + 盾牌锁 SF Symbol。用法:swift icon.swift out.png
import AppKit

let size: CGFloat = 1024
let img = NSImage(size: NSSize(width: size, height: size))
img.lockFocus()
let rect = NSRect(x: 80, y: 80, width: size - 160, height: size - 160)
let path = NSBezierPath(roundedRect: rect, xRadius: 190, yRadius: 190)
NSGradient(colors: [NSColor(red: 0.39, green: 0.40, blue: 0.95, alpha: 1),
                    NSColor(red: 0.55, green: 0.36, blue: 0.96, alpha: 1),
                    NSColor(red: 0.13, green: 0.83, blue: 0.93, alpha: 1)])!.draw(in: path, angle: -45)
let cfg = NSImage.SymbolConfiguration(pointSize: 520, weight: .semibold)
if let sym = NSImage(systemSymbolName: "lock.shield.fill", accessibilityDescription: nil)?.withSymbolConfiguration(cfg) {
    let tinted = NSImage(size: sym.size)
    tinted.lockFocus()
    sym.draw(at: .zero, from: .zero, operation: .sourceOver, fraction: 1)
    NSColor.white.set()
    NSRect(origin: .zero, size: sym.size).fill(using: .sourceAtop)
    tinted.unlockFocus()
    tinted.draw(in: NSRect(x: (size - sym.size.width) / 2, y: (size - sym.size.height) / 2, width: sym.size.width, height: sym.size.height))
}
img.unlockFocus()
let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
