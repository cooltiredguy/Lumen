// LumenWorkload: paints a binary-block monotonic counter on the virtual display.
// Reads LUMEN_WORKLOAD_TRACE_FILE for id→t_paint JSONL output.
// Reads LUMEN_VIRTUAL_DISPLAY_ID for the CGDirectDisplayID to target.
// Usage: LumenWorkload [fps] [bits] [seconds]

import Cocoa
import Metal
import QuartzCore
import CoreVideo

let args = CommandLine.arguments.dropFirst()
let FPS:  Int = Int(args.first ?? "60") ?? 60
let BITS: Int = args.count > 1 ? (Int(args[args.index(args.startIndex, offsetBy: 1)]) ?? 20) : 20
let SECS: Int = args.count > 2 ? (Int(args[args.index(args.startIndex, offsetBy: 2)]) ?? 30) : 30

// ─── Trace sink ──────────────────────────────────────────────────────────────
class TraceSink {
    private let file: FileHandle?
    init() {
        guard let path = ProcessInfo.processInfo.environment["LUMEN_WORKLOAD_TRACE_FILE"] else {
            file = nil; return
        }
        FileManager.default.createFile(atPath: path, contents: nil)
        file = FileHandle(forWritingAtPath: path)
    }
    func emit(id: UInt32, t_ns: UInt64) {
        guard let fh = file else { return }
        let line = "{\"id\":\(id),\"t_paint_ns\":\(t_ns)}\n"
        fh.write(line.data(using: .utf8)!)
    }
    deinit { file?.closeFile() }
}

// ─── Steady clock ────────────────────────────────────────────────────────────
func ns_now() -> UInt64 {
    var info = mach_timebase_info_data_t()
    mach_timebase_info(&info)
    let raw = mach_absolute_time()
    return raw * UInt64(info.numer) / UInt64(info.denom)
}

// ─── Find target NSScreen ────────────────────────────────────────────────────
func findVirtualScreen() -> NSScreen? {
    guard let idStr = ProcessInfo.processInfo.environment["LUMEN_VIRTUAL_DISPLAY_ID"],
          let targetID = UInt32(idStr) else {
        print("[workload] LUMEN_VIRTUAL_DISPLAY_ID not set; using main screen")
        return NSScreen.main
    }
    for screen in NSScreen.screens {
        if let num = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber,
           num.uint32Value == targetID {
            return screen
        }
    }
    print("[workload] WARNING: display \(targetID) not found; using main screen")
    return NSScreen.main
}

// ─── Draw binary blocks ──────────────────────────────────────────────────────
func drawFrame(in context: CGContext, counter: UInt32, bounds: CGRect) {
    let w = bounds.width, h = bounds.height
    let blockSize: CGFloat = max(w / 64, 20)
    let markerSize: CGFloat = blockSize * 1.5

    // Background: mid-grey
    context.setFillColor(CGColor(gray: 0.5, alpha: 1))
    context.fill(bounds)

    // Motion region: alternating stripes that change each frame
    let stripe = Int(counter) % 8
    for i in 0..<8 {
        let x = w * CGFloat(i) / 8.0
        context.setFillColor((i + stripe) % 2 == 0
            ? CGColor(gray: 0.3, alpha: 1) : CGColor(gray: 0.7, alpha: 1))
        context.fill(CGRect(x: x, y: h * 0.25, width: w / 8, height: h * 0.5))
    }

    // Corner calibration markers (always white squares at fixed positions)
    let corners: [CGPoint] = [
        CGPoint(x: 0, y: 0),
        CGPoint(x: w - markerSize, y: 0),
        CGPoint(x: 0, y: h - markerSize),
        CGPoint(x: w - markerSize, y: h - markerSize),
    ]
    context.setFillColor(CGColor(gray: 1, alpha: 1))
    for c in corners {
        context.fill(CGRect(origin: c, size: CGSize(width: markerSize, height: markerSize)))
    }

    // Binary block row: BITS blocks encoding `counter`
    let rowY: CGFloat = h * 0.1
    for bit in 0..<BITS {
        let bitVal = (counter >> bit) & 1
        let x = (w - blockSize * CGFloat(BITS)) / 2 + blockSize * CGFloat(bit)
        context.setFillColor(bitVal == 1
            ? CGColor(gray: 1, alpha: 1) : CGColor(gray: 0, alpha: 1))
        context.fill(CGRect(x: x, y: rowY, width: blockSize - 2, height: blockSize))
    }
}

// ─── Main ────────────────────────────────────────────────────────────────────
let app  = NSApplication.shared
let sink = TraceSink()

guard let screen = findVirtualScreen() else {
    print("[workload] No screen found, exiting")
    exit(1)
}
let frame = screen.frame
print("[workload] target screen: \(frame.width)x\(frame.height)")

let win = NSWindow(
    contentRect: frame,
    styleMask: [.borderless],
    backing: .buffered,
    defer: false,
    screen: screen
)
win.level = .normal
win.backgroundColor = .black
win.setFrame(frame, display: true)
win.makeKeyAndOrderFront(nil)

let view = NSView(frame: CGRect(origin: .zero, size: frame.size))
win.contentView = view
let layer = CALayer()
layer.frame = view.bounds
view.layer = layer
view.wantsLayer = true

var counter: UInt32 = 0
let interval = 1.0 / Double(FPS)
let deadline = Date().addingTimeInterval(Double(SECS))

let timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { _ in
    guard Date() < deadline else {
        print("[workload] done after \(counter) frames")
        app.stop(nil)
        return
    }
    let t = ns_now()
    sink.emit(id: counter, t_ns: t)

    // Draw into a CGContext and set as layer content
    let size = CGSize(width: frame.width, height: frame.height)
    let bitmapInfo = CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue)
    guard let ctx = CGContext(
        data: nil, width: Int(size.width), height: Int(size.height),
        bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: bitmapInfo.rawValue)
    else { return }
    drawFrame(in: ctx, counter: counter, bounds: CGRect(origin: .zero, size: size))
    layer.contents = ctx.makeImage()

    counter += 1
}

RunLoop.main.add(timer, forMode: .default)
app.run()
