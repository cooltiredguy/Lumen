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

    // Calibration marker check: top-left corner should be white (>180 luma)
    let markerSize = max(w / 64, 20)
    let markerCenter = markerSize / 2
    guard luma(x: markerCenter, y: markerCenter) > 180 else { return nil }

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

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        guard outputType == .screen else { return }
        let t = ns_now()
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let context = CIContext()
        guard let cgImage = context.createCGImage(ciImage, from: ciImage.extent) else { return }
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
        guard let moonlightWindow = content.windows.first(where: { w in
            w.owningApplication?.applicationName.contains("Moonlight") == true ||
            w.title?.contains("Moonlight") == true
        }) else {
            print("[readback] ERROR: Moonlight window not found in SCShareableContent")
            print("[readback] Available: \(content.windows.map { $0.owningApplication?.applicationName ?? "?" })")
            exit(1)
        }
        print("[readback] capturing: \(moonlightWindow.title ?? "Moonlight")")

        let filter = SCContentFilter(desktopIndependentWindow: moonlightWindow)
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
