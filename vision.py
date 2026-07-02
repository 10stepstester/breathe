"""Camera + pose detection for the breathe app.

All detection happens locally. No frames are ever saved or sent anywhere.
"""
import ctypes
import os
import time
from statistics import median

# Permission is requested properly on the main thread at app startup (see
# breathe.py); without this flag OpenCV tries to re-request from the capture
# thread and aborts.
os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")

import cv2
import mediapipe as mp

NOSE, L_SHOULDER, R_SHOULDER = 0, 11, 12

# Deep breath = shoulder-rise excursion bigger than this fraction of shoulder width,
# within a rolling 6-second span. Normal breathing barely moves the shoulders.
BREATH_AMPLITUDE_FRAC = 0.06
BREATH_SPAN_SEC = 6.0
# Fallback when only a tall pose is calibrated (no slouch reference):
# sitting tall = ratio >= 88% of the tall baseline, sustained over 3 seconds.
POSTURE_OK_FRAC = 0.88
POSTURE_SPAN_SEC = 3.0
TARGET_FPS = 15


def _fourcc(s):
    return int.from_bytes(s.encode(), "big")


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def camera_in_use_elsewhere():
    """True if another app (Zoom, FaceTime, Meet) is actively using any camera.

    Asks CoreMediaIO for kCMIODevicePropertyDeviceIsRunningSomewhere on every
    video device. Fails open (False) so a broken check never blocks the app.
    """
    try:
        cmio = ctypes.CDLL(
            "/System/Library/Frameworks/CoreMediaIO.framework/CoreMediaIO"
        )
        glob = _fourcc("glob")
        addr = _PropertyAddress(_fourcc("dev#"), glob, 0)
        size = ctypes.c_uint32(0)
        if cmio.CMIOObjectGetPropertyDataSize(
            1, ctypes.byref(addr), 0, None, ctypes.byref(size)
        ) != 0 or size.value == 0:
            return False
        n = size.value // 4
        devices = (ctypes.c_uint32 * n)()
        used = ctypes.c_uint32(0)
        if cmio.CMIOObjectGetPropertyData(
            1, ctypes.byref(addr), 0, None, size, ctypes.byref(used), devices
        ) != 0:
            return False
        running = _PropertyAddress(_fourcc("goin"), glob, 0)
        for dev in devices:
            val = ctypes.c_uint32(0)
            vused = ctypes.c_uint32(0)
            if cmio.CMIOObjectGetPropertyData(
                dev, ctypes.byref(running), 0, None,
                ctypes.c_uint32(4), ctypes.byref(vused), ctypes.byref(val),
            ) == 0 and val.value:
                return True
        return False
    except Exception:
        return False


def _landmarks(results):
    """Return (nose_y, shoulder_mid_y, shoulder_width) or None if not visible."""
    if not results.pose_landmarks:
        return None
    lm = results.pose_landmarks.landmark
    nose, ls, rs = lm[NOSE], lm[L_SHOULDER], lm[R_SHOULDER]
    if min(nose.visibility, ls.visibility, rs.visibility) < 0.5:
        return None
    width = abs(ls.x - rs.x)
    if width < 0.05:
        return None
    return nose.y, (ls.y + rs.y) / 2.0, width


def watch_window(window_sec, posture_threshold, check_breath, check_posture,
                 log=None):
    """Open the camera for up to window_sec seconds and watch for the good stuff.

    Returns {"present": bool, "breath": bool, "posture": bool, "error": str|None}.
    Ends early (camera light goes off) once everything asked for is satisfied —
    that early shutoff is the reward signal.
    """
    result = {"present": False, "breath": False, "posture": False, "error": None}
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        result["error"] = "camera"
        return result

    pose = mp.solutions.pose.Pose(
        model_complexity=0,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    shoulder_samples = []  # (t, smoothed shoulder_mid_y, shoulder_width)
    posture_samples = []   # (t, nose-to-shoulder ratio)
    ema = None
    breath_done = not check_breath
    posture_done = not check_posture or posture_threshold is None
    deadline = time.monotonic() + window_sec

    try:
        while time.monotonic() < deadline:
            frame_start = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            lms = _landmarks(pose.process(frame))
            now = time.monotonic()
            if lms is not None:
                result["present"] = True
                nose_y, shoulder_y, width = lms
                ema = shoulder_y if ema is None else 0.3 * shoulder_y + 0.7 * ema
                shoulder_samples.append((now, ema, width))
                posture_samples.append((now, (shoulder_y - nose_y) / width))

                if not breath_done:
                    recent = [s for s in shoulder_samples if now - s[0] <= BREATH_SPAN_SEC]
                    if len(recent) >= 10 and recent[-1][0] - recent[0][0] >= 2.0:
                        ys = [s[1] for s in recent]
                        scale = median(s[2] for s in recent)
                        if max(ys) - min(ys) > BREATH_AMPLITUDE_FRAC * scale:
                            breath_done = True
                            if log:
                                log("deep breath detected")

                if not posture_done:
                    recent = [s for s in posture_samples if now - s[0] <= POSTURE_SPAN_SEC]
                    if len(recent) >= 10:
                        if median(s[1] for s in recent) >= posture_threshold:
                            posture_done = True
                            if log:
                                log("sitting tall detected")

            if breath_done and posture_done and result["present"]:
                break
            elapsed = time.monotonic() - frame_start
            time.sleep(max(0.0, 1.0 / TARGET_FPS - elapsed))
    finally:
        cap.release()
        pose.close()

    result["breath"] = breath_done
    result["posture"] = posture_done
    return result


def preview(posture_threshold, max_sec=120):
    """Live debug window: skeleton dots + the exact numbers being judged.

    Runs as its own process (--preview) because macOS GUI windows must own
    the main thread. Press Q or Esc to close; auto-closes after max_sec.
    """
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return
    pose = mp.solutions.pose.Pose(
        model_complexity=0,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    drawer = mp.solutions.drawing_utils
    shoulder_samples = []
    ema = None
    breath_flash_until = 0.0
    win = "Breathe - what it sees (press Q to close)"

    def put(frame, text, y, color, scale=0.7):
        cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, color, 2, cv2.LINE_AA)

    deadline = time.monotonic() + max_sec
    try:
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            results = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            if results.pose_landmarks:
                drawer.draw_landmarks(
                    frame, results.pose_landmarks,
                    mp.solutions.pose.POSE_CONNECTIONS)
            frame = cv2.flip(frame, 1)
            lms = _landmarks(results)
            now = time.monotonic()

            if lms is None:
                put(frame, "Can't see nose + both shoulders", 40, (0, 0, 255))
            else:
                nose_y, shoulder_y, width = lms
                ema = shoulder_y if ema is None else 0.3 * shoulder_y + 0.7 * ema
                shoulder_samples.append((now, ema, width))
                shoulder_samples = [
                    s for s in shoulder_samples if now - s[0] <= BREATH_SPAN_SEC
                ]

                ratio = (shoulder_y - nose_y) / width
                if posture_threshold:
                    need = posture_threshold
                    tall = ratio >= need
                    put(frame,
                        f"posture {ratio:.2f}  (tall = {need:.2f}+)",
                        40, (0, 200, 0) if tall else (0, 0, 255))
                    put(frame, "SITTING TALL" if tall else "SLOUCHED",
                        80, (0, 200, 0) if tall else (0, 0, 255), 1.0)
                else:
                    put(frame, f"posture {ratio:.2f} (not calibrated yet)",
                        40, (0, 200, 255))

                if len(shoulder_samples) >= 10:
                    ys = [s[1] for s in shoulder_samples]
                    scale = median(s[2] for s in shoulder_samples)
                    amp = max(ys) - min(ys)
                    need_amp = BREATH_AMPLITUDE_FRAC * scale
                    if amp > need_amp:
                        breath_flash_until = now + 3.0
                    put(frame,
                        f"shoulder movement {amp:.3f}  (deep breath = {need_amp:.3f}+)",
                        120, (0, 200, 0) if amp > need_amp else (200, 200, 200))
                if now < breath_flash_until:
                    put(frame, "DEEP BREATH DETECTED", 160, (0, 200, 0), 1.0)

            cv2.imshow(win, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            try:
                if cv2.getWindowProperty(win, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except cv2.error:
                break
    finally:
        cap.release()
        pose.close()
        cv2.destroyAllWindows()


def calibrate(seconds=10):
    """Capture the sitting-tall posture baseline.

    Returns (ratio, None) on success, (None, "camera") if the camera couldn't
    open, or (None, "not_visible") if no clear view of nose + shoulders.
    """
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None, "camera"
    pose = mp.solutions.pose.Pose(model_complexity=0, min_detection_confidence=0.5)
    ratios = []
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            lms = _landmarks(pose.process(frame))
            if lms is not None:
                nose_y, shoulder_y, width = lms
                ratios.append((shoulder_y - nose_y) / width)
            time.sleep(1.0 / TARGET_FPS)
    finally:
        cap.release()
        pose.close()
    if len(ratios) < 30:
        return None, "not_visible"
    return median(ratios), None
