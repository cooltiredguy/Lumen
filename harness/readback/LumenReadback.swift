// LumenReadback: captures the Moonlight client window via ScreenCaptureKit,
// threshold-decodes the binary-block counter from each frame, and writes
// id→t_observe to a JSONL trace.
//
// Env: LUMEN_READBACK_TRACE_FILE=<path>   where to write JSONL
//      LUMEN_READBACK_BITS=<N>             bits in the counter (default 20)
//      LUMEN_READBACK_SECONDS=<N>          how long to run (default 30)

import Cocoa
import ScreenCaptureKit
import CoreGraphics

let BITS    = Int(ProcessInfo.processInfo.environment["LUMEN_READBACK_BITS"] ?? "20") ?? 20
let SECS    = Int(ProcessInfo.processInfo.environment["LUMEN_READBACK_SECONDS"] ?? "30") ?? 30
let TRACE   = ProcessInfo.processInfo.environment["LUMEN_READBACK_TRACE_FILE"]

// ─── Steady clock ────────────────────────────────────────────────────────────
func ns_now() -> UInt64 {
    var info = mach_timebase_info_data_t()
    mach_timebase_info(&info)
    return mach_absolute_time() * UInt64(info.numer) / UInt64(info.denom)
}

// ─── Trace sink ──────────────────────────────────────────────────────────────
class TraceSink {
    private let fh: FileHandle?
    private var seen = Set<UInt32>()

    init(path: String?) {
        guard let p = path else { fh = nil; return }
        FileManager.default.createFile(atPath: p, contents: nil)
        fh = FileHandle(forWritingAtPath: p)
    }
    func emit(id: UInt32, t_ns: UInt64) {
        guard let fh = fh, !seen.contains(id) else { return }
        seen.insert(id)   // de-duplicate: keep first observation
        let line = "{\"id\":\(id),\"t_observe_ns\":\(t_ns)}\n"
        fh.write(line.data(using: .utf8)!)
    }
    deinit { fh?.closeFile() }
}

// ─── Block decoder ───────────────────────────────────────────────────────────
var _diagFrameCount = 0

func decodeCounter(from image: CGImage) -> UInt32? {
    let w = image.width, h = image.height
    guard w > 0, h > 0 else { return nil }

    guard let data = image.dataProvider?.data,
          let ptr = CFDataGetBytePtr(data) else { return nil }

    let bpp = image.bitsPerPixel / 8
    func luma(x: Int, y: Int) -> Double {
        let offset = (y * w + x) * bpp
        let r = Double(ptr[offset])
        let g = Double(ptr[offset + 1])
        let b = Double(ptr[offset + 2])
        return 0.299 * r + 0.587 * g + 0.114 * b
    }

    // Calibration marker check: any corner white (>180 luma) = workload is visible.
    let markerSize = max(w / 64, 20)
    let mc = markerSize / 2
    let tlLuma = luma(x: mc,     y: mc)
    let trLuma = luma(x: w-mc-1, y: mc)
    let blLuma = luma(x: mc,     y: h-mc-1)
    let brLuma = luma(x: w-mc-1, y: h-mc-1)
    let cxLuma = luma(x: w/2,    y: h/2)
    // Scan 16×9 grid to locate stream content; track which cell gives gridMax.
    var gridMax: Double = 0; var gridMaxIx = 0, gridMaxIy = 0
    let gx = 16, gy = 9
    for iy in 0..<gy {
        for ix in 0..<gx {
            let px = (ix * w) / gx + w / (2 * gx)
            let py = (iy * h) / gy + h / (2 * gy)
            let l = luma(x: px, y: py)
            if l > gridMax { gridMax = l; gridMaxIx = ix; gridMaxIy = iy }
        }
    }
    let markerLuma = max(tlLuma, max(trLuma, max(blLuma, brLuma)))

    // Frame 0: coarse full scan (step=4) to find where bright pixels actually live.
    // Calibration markers (luma≈250) may not be at display corners if the Moonlight
    // window is offset within the display.
    if _diagFrameCount == 0 {
        var scanMax: Double = 0; var scanMaxX = 0, scanMaxY = 0
        // Also collect all positions with luma>200 to locate calibration markers.
        var brightPts: [(Int, Int, Int)] = []
        for sy in stride(from: 0, to: h, by: 4) {
            for sx in stride(from: 0, to: w, by: 4) {
                let l = luma(x: sx, y: sy)
                if l > scanMax { scanMax = l; scanMaxX = sx; scanMaxY = sy }
                if l > 200 { brightPts.append((sx, sy, Int(l))) }
            }
        }
        print("[readback] frame0 fullscan: max=\(Int(scanMax)) at (\(scanMaxX),\(scanMaxY)) bright>200: \(brightPts.count) pts")
        // Print up to 20 bright points to show distribution
        for pt in brightPts.prefix(20) { print("[readback]   bright (\(pt.0),\(pt.1)) luma=\(pt.2)") }
    }

    if _diagFrameCount < 5 || _diagFrameCount % 300 == 0 {
        print("[readback] frame \(_diagFrameCount): \(w)x\(h) "
            + "tl=\(Int(tlLuma)) tr=\(Int(trLuma)) bl=\(Int(blLuma)) br=\(Int(brLuma)) cx=\(Int(cxLuma)) "
            + "gridMax=\(Int(gridMax)) at (\(gridMaxIx),\(gridMaxIy))")
    }
    _diagFrameCount += 1
    guard markerLuma > 180 else { return nil }

    // Decode block row at y ≈ 10% of height
    let rowY = Int(Double(h) * 0.1) + markerSize / 2
    let blockSize = max(w / 64, 20)
    let startX = (w - blockSize * BITS) / 2

    var counter: UInt32 = 0
    for bit in 0..<BITS {
        let cx = startX + blockSize * bit + blockSize / 2
        let cy = rowY
        guard cx >= 0, cx < w, cy >= 0, cy < h else { continue }
        if luma(x: cx, y: cy) > 128 {
            counter |= (1 << bit)
        }
    }
    return counter
}

// ─── SCK capture ─────────────────────────────────────────────────────────────
class ReadbackDelegate: NSObject, SCStreamOutput {
    let sink: TraceSink
    init(sink: TraceSink) { self.sink = sink }

    var _callbackCount = 0

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        if _callbackCount < 3 {
            print("[readback] callback #\(_callbackCount) type=\(outputType)")
            _callbackCount += 1
        }
        guard outputType == .screen else { return }
        let t = ns_now()
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            if _callbackCount <= 3 { print("[readback] nil pixelBuffer") }
            return
        }

        // Lock the pixel buffer and build a CGImage directly from its raw bytes
        // (avoids CIContext/CoreGraphics WindowServer dependency that can assert in headless sessions)
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        let w = CVPixelBufferGetWidth(pixelBuffer)
        let h = CVPixelBufferGetHeight(pixelBuffer)
        guard let baseAddr = CVPixelBufferGetBaseAddress(pixelBuffer), w > 0, h > 0 else { return }
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let bitsPerComponent = 8
        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else { return }
        let bitmapInfo = CGBitmapInfo(rawValue:
            CGBitmapInfo.byteOrder32Little.rawValue |
            CGImageAlphaInfo.premultipliedFirst.rawValue)
        guard let ctx = CGContext(
            data: baseAddr, width: w, height: h,
            bitsPerComponent: bitsPerComponent,
            bytesPerRow: bytesPerRow,
            space: colorSpace,
            bitmapInfo: bitmapInfo.rawValue),
              let cgImage = ctx.makeImage() else { return }

        if let id = decodeCounter(from: cgImage) {
            sink.emit(id: id, t_ns: t)
        }
    }
}

// ─── Main ────────────────────────────────────────────────────────────────────
let sink = TraceSink(path: TRACE)
let sema = DispatchSemaphore(value: 0)

Task {
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false,
                                                                           onScreenWindowsOnly: true)

        let mainID = CGMainDisplayID()
        guard let mainDisplay = content.displays.first(where: { $0.displayID == mainID })
                             ?? content.displays.first else {
            print("[readback] ERROR: no display found")
            exit(1)
        }
        print("[readback] capturing display: \(mainDisplay.displayID) \(mainDisplay.width)x\(mainDisplay.height)")

        // Capture the whole display — Moonlight's rendering window doesn't appear in the
        // SCK window list (it renders via an IOSurface layer), but its content IS visible
        // in the display pixels when its Space is active on display 3.
        let filter = SCContentFilter(display: mainDisplay, excludingWindows: [])

        let config = SCStreamConfiguration()
        config.width  = 1920
        config.height = 1080
        config.minimumFrameInterval = CMTime(value: 1, timescale: 60)
        config.pixelFormat = kCVPixelFormatType_32BGRA

        let stream = SCStream(filter: filter, configuration: config, delegate: nil)
        let delegate = ReadbackDelegate(sink: sink)
        try stream.addStreamOutput(delegate, type: .screen,
                                   sampleHandlerQueue: .global(qos: .userInteractive))
        try await stream.startCapture()
        print("[readback] running for \(SECS)s")
        try await Task.sleep(nanoseconds: UInt64(SECS) * 1_000_000_000)
        try await stream.stopCapture()
        print("[readback] done")
    } catch {
        print("[readback] ERROR: \(error)")
        exit(1)
    }
    sema.signal()
}

sema.wait()
