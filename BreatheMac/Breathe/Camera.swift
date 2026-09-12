import AVFoundation
import CoreMediaIO

/// Owns the capture session. Step one only turns the camera on and off;
/// frames are delivered to `frameHandler` for step two (pose detection).
final class Camera: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "com.laddcarlston.breathe.camera")
    private var configured = false

    var frameHandler: ((CMSampleBuffer) -> Void)?

    /// Asks for permission if needed, then starts the session. Completion on main.
    func start(completion: @escaping (Bool) -> Void) {
        AVCaptureDevice.requestAccess(for: .video) { granted in
            guard granted else {
                DispatchQueue.main.async { completion(false) }
                return
            }
            self.queue.async {
                if !self.configured { self.configure() }
                guard self.configured else {
                    DispatchQueue.main.async { completion(false) }
                    return
                }
                if !self.session.isRunning { self.session.startRunning() }
                DispatchQueue.main.async { completion(true) }
            }
        }
    }

    func stop() {
        queue.async {
            if self.session.isRunning { self.session.stopRunning() }
        }
    }

    private func configure() {
        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device) else { return }
        session.beginConfiguration()
        session.sessionPreset = .vga640x480
        if session.canAddInput(input) { session.addInput(input) }
        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.setSampleBufferDelegate(self, queue: queue)
        if session.canAddOutput(output) { session.addOutput(output) }
        session.commitConfiguration()
        configured = !session.inputs.isEmpty
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        frameHandler?(sampleBuffer)
    }

    // MARK: - Is another app (Zoom, FaceTime) already using the camera?

    /// Reads CoreMediaIO's "device is running somewhere" flag for the default camera.
    /// Same check the Python version does; read-only, works inside the sandbox.
    static func isBusyElsewhere() -> Bool {
        guard let device = AVCaptureDevice.default(for: .video) else { return false }

        func address(_ selector: Int) -> CMIOObjectPropertyAddress {
            CMIOObjectPropertyAddress(
                mSelector: CMIOObjectPropertySelector(selector),
                mScope: CMIOObjectPropertyScope(kCMIOObjectPropertyScopeGlobal),
                mElement: CMIOObjectPropertyElement(kCMIOObjectPropertyElementMain))
        }

        var devicesAddress = address(kCMIOHardwarePropertyDevices)
        let system = CMIOObjectID(kCMIOObjectSystemObject)
        var dataSize: UInt32 = 0
        guard CMIOObjectGetPropertyDataSize(system, &devicesAddress, 0, nil, &dataSize) == noErr,
              dataSize > 0 else { return false }

        let count = Int(dataSize) / MemoryLayout<CMIOObjectID>.size
        var ids = [CMIOObjectID](repeating: 0, count: count)
        var used: UInt32 = 0
        guard CMIOObjectGetPropertyData(system, &devicesAddress, 0, nil, dataSize, &used, &ids) == noErr else { return false }

        for id in ids {
            var uidAddress = address(kCMIODevicePropertyDeviceUID)
            var uid: Unmanaged<CFString>? = nil
            var uidUsed: UInt32 = 0
            let uidSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
            guard CMIOObjectGetPropertyData(id, &uidAddress, 0, nil, uidSize, &uidUsed, &uid) == noErr,
                  let uidString = uid?.takeRetainedValue() as String? else { continue }
            guard uidString == device.uniqueID else { continue }

            var runningAddress = address(kCMIODevicePropertyDeviceIsRunningSomewhere)
            var running: UInt32 = 0
            var runningUsed: UInt32 = 0
            let runningSize = UInt32(MemoryLayout<UInt32>.size)
            if CMIOObjectGetPropertyData(id, &runningAddress, 0, nil, runningSize, &runningUsed, &running) == noErr {
                return running != 0
            }
        }
        return false
    }
}
