// jarvis-audio: microphone capture + streaming playback in ONE AVAudioEngine with Apple's voice
// processing (acoustic echo cancellation, noise suppression). Because Jarvis' own voice is played
// through the same engine, the echo canceller knows the reference signal and removes it from the
// microphone – so Sir can interrupt Jarvis just by talking.
//
// A second, plain engine captures the unprocessed microphone at the same time: wake-word detection,
// endpointing and transcription work best on the raw signal (the wake-word model scored 0.01 on the
// processed and 0.999 on the raw signal in a speaker test), the echo-cancelled one is for barge-in.
//
// Protocol (spawned by the Python voice daemon):
//   stdout: frames [kind: UInt8 'R' raw | 'A' echo-cancelled][length: UInt32 LE][16-bit LE mono PCM @ 16 kHz]
//   stdin:  frames  [type: UInt8][length: UInt32 LE][payload]
//           'P' payload = 16-bit LE mono PCM at 24 kHz → queued for playback immediately
//           'F' flush: stop playback now and drop everything queued (barge-in)
//           'E' payload = 1 byte tag → "drained" event once everything queued before it was heard
//           'Q' quit
//   stderr: one JSON event per line, prefixed "EV ": ready, level (in/out), drained, flushed, error
// Build: scripts/build-audio-helper.sh (swiftc -O). Usage: jarvis-audio [--no-aec]

import AVFoundation
import Foundation

setvbuf(stderr, nil, _IOLBF, 0)

func emit(_ obj: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: obj),
          let text = String(data: data, encoding: .utf8) else { return }
    FileHandle.standardError.write(("EV " + text + "\n").data(using: .utf8)!)
}

let wantAEC = !CommandLine.arguments.contains("--no-aec")
let engine = AVAudioEngine()
let input = engine.inputNode
_ = engine.mainMixerNode  // must exist before voice processing is enabled, else kAUInitialize fails (-10875)
var aecActive = false
if wantAEC {
    do {
        try input.setVoiceProcessingEnabled(true)
        try engine.outputNode.setVoiceProcessingEnabled(true)
        aecActive = true
        if #available(macOS 14.0, *) {
            // Do not duck other apps (music, calls) while Jarvis listens.
            input.voiceProcessingOtherAudioDuckingConfiguration =
                AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
        }
    } catch {
        emit(["event": "warning", "detail": "voice processing unavailable: \(error.localizedDescription)"])
    }
}

let micFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
let playFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!
let player = AVAudioPlayerNode()
engine.attach(player)
engine.connect(player, to: engine.mainMixerNode, format: playFormat)

let stdoutHandle = FileHandle.standardOutput
let outLock = NSLock()
let lock = NSLock()
let rawEngine = AVAudioEngine()

func writeFrame(_ kind: UInt8, _ ptr: UnsafePointer<Int16>, _ samples: Int) {
    var head = Data([kind])
    var len = UInt32(samples * 2).littleEndian
    head.append(Data(bytes: &len, count: 4))
    head.append(Data(bytes: ptr, count: samples * 2))
    outLock.lock(); stdoutHandle.write(head); outLock.unlock()
}

/// Installs a tap that converts any input format to 16 kHz mono int16 and emits frames of ``kind``.
func installConvertingTap(on node: AVAudioInputNode, kind: UInt8, meter: Bool) -> Bool {
    let inFormat = node.outputFormat(forBus: 0)
    guard inFormat.sampleRate > 0, inFormat.channelCount > 0 else { return false }
    let monoIn = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: inFormat.sampleRate, channels: 1,
                               interleaved: false)!
    guard let converter = AVAudioConverter(from: monoIn, to: micFormat) else { return false }
    node.removeTap(onBus: 0)
    node.installTap(onBus: 0, bufferSize: 960, format: inFormat) { buf, _ in
        let frames = buf.frameLength
        guard frames > 0, let src = buf.floatChannelData else { return }
        guard let mono = AVAudioPCMBuffer(pcmFormat: monoIn, frameCapacity: frames) else { return }
        mono.frameLength = frames
        memcpy(mono.floatChannelData![0], src[0], Int(frames) * MemoryLayout<Float>.size)
        if meter {
            var sum: Float = 0
            for i in 0..<Int(frames) { sum += src[0][i] * src[0][i] }
            lock.lock(); inLevel = sqrt(sum / Float(frames)); lock.unlock()
        }
        let cap = AVAudioFrameCount(Double(frames) * micFormat.sampleRate / inFormat.sampleRate) + 64
        guard let out = AVAudioPCMBuffer(pcmFormat: micFormat, frameCapacity: cap) else { return }
        var fed = false
        var err: NSError?
        converter.convert(to: out, error: &err) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return mono
        }
        if out.frameLength > 0, let ch = out.int16ChannelData {
            writeFrame(kind, ch[0], Int(out.frameLength))
        }
    }
    return true
}
var inLevel: Float = 0
var generation = 0
var playing: [(gen: Int, rms: Float)] = []  // FIFO of scheduled buffers (for the output level)

func installTap() {
    if !installConvertingTap(on: input, kind: UInt8(ascii: aecActive ? "A" : "R"), meter: !aecActive) {
        emit(["event": "error", "detail": "no microphone input format"])
    }
}

func startRawEngine() {
    // Only needed when the main engine delivers the processed signal.
    guard aecActive else { return }
    if installConvertingTap(on: rawEngine.inputNode, kind: UInt8(ascii: "R"), meter: true) {
        rawEngine.prepare()
        do { try rawEngine.start() } catch {
            emit(["event": "warning", "detail": "raw microphone unavailable: \(error.localizedDescription)"])
        }
    }
}

func startEngine() -> Bool {
    installTap()
    engine.prepare()
    do {
        try engine.start()
        player.play()
        return true
    } catch {
        emit(["event": "error", "detail": "engine start failed: \(error.localizedDescription)"])
        return false
    }
}

switch AVCaptureDevice.authorizationStatus(for: .audio) {
case .notDetermined:
    let sem = DispatchSemaphore(value: 0)
    AVCaptureDevice.requestAccess(for: .audio) { _ in sem.signal() }
    sem.wait()
case .denied, .restricted:
    emit(["event": "error", "detail": "microphone permission denied"])
default:
    break
}

NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: nil) { _ in
    // Device change (headphones, AirPods): restart with the new hardware format.
    emit(["event": "config_change"])
    lock.lock(); generation += 1; playing.removeAll(); lock.unlock()
    _ = startEngine()
}

guard startEngine() else { exit(2) }
startRawEngine()
NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: rawEngine, queue: nil) { _ in
    startRawEngine()
}
let inFmt = input.outputFormat(forBus: 0)
emit(["event": "ready", "aec": aecActive, "in_rate": inFmt.sampleRate, "in_channels": inFmt.channelCount])

func schedule(pcm: Data) {
    let n = pcm.count / 2
    guard n > 0, let buf = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: AVAudioFrameCount(n)) else { return }
    buf.frameLength = AVAudioFrameCount(n)
    let dst = buf.floatChannelData![0]
    var sum: Float = 0
    pcm.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
        let s = raw.bindMemory(to: Int16.self)
        for i in 0..<n {
            let v = Float(Int16(littleEndian: s[i])) / 32768.0
            dst[i] = v
            sum += v * v
        }
    }
    lock.lock()
    let gen = generation
    playing.append((gen, sqrt(sum / Float(n))))
    lock.unlock()
    player.scheduleBuffer(buf, completionCallbackType: .dataPlayedBack) { _ in
        lock.lock()
        if let i = playing.firstIndex(where: { $0.gen == gen }) { playing.remove(at: i) }
        lock.unlock()
    }
    if !player.isPlaying { player.play() }
}

func marker(tag: UInt8) {
    guard let buf = AVAudioPCMBuffer(pcmFormat: playFormat, frameCapacity: 1) else { return }
    buf.frameLength = 1
    buf.floatChannelData![0][0] = 0
    lock.lock(); let gen = generation; lock.unlock()
    player.scheduleBuffer(buf, completionCallbackType: .dataPlayedBack) { _ in
        lock.lock(); let current = generation; lock.unlock()
        if current == gen { emit(["event": "drained", "tag": Int(tag)]) }
    }
}

func flush() {
    lock.lock(); generation += 1; playing.removeAll(); lock.unlock()
    player.stop()  // drops every scheduled buffer at once
    player.play()
    emit(["event": "flushed"])
}

// Output/input level for the interface (~12 Hz).
let timer = DispatchSource.makeTimerSource(queue: DispatchQueue.global(qos: .utility))
timer.schedule(deadline: .now(), repeating: .milliseconds(80))
timer.setEventHandler {
    lock.lock()
    let out = playing.first?.rms ?? 0
    let lin = inLevel
    let busy = !playing.isEmpty
    lock.unlock()
    emit(["event": "level", "in": Double(min(1, lin * 8)), "out": Double(min(1, out * 4)), "playing": busy])
}
timer.resume()

// stdin reader (blocking) on its own thread.
let reader = Thread {
    let stdin = FileHandle.standardInput
    func readExactly(_ count: Int) -> Data? {
        var data = Data()
        while data.count < count {
            let chunk = stdin.readData(ofLength: count - data.count)
            if chunk.isEmpty { return nil }
            data.append(chunk)
        }
        return data
    }
    while true {
        guard let head = readExactly(5) else { break }
        let type = head[0]
        let len = Int(head[1]) | Int(head[2]) << 8 | Int(head[3]) << 16 | Int(head[4]) << 24
        let payload = len > 0 ? (readExactly(len) ?? Data()) : Data()
        switch type {
        case UInt8(ascii: "P"): schedule(pcm: payload)
        case UInt8(ascii: "F"): flush()
        case UInt8(ascii: "E"): marker(tag: payload.first ?? 0)
        case UInt8(ascii: "Q"): exit(0)
        default: break
        }
    }
    exit(0)  // parent closed stdin
}
reader.start()
dispatchMain()
