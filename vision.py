"""Camera + pose detection for the breathe app.

All detection happens locally. No frames are ever saved or sent anywhere.
"""
import ctypes
import time
from statistics import median

import cv2
import mediapipe as mp

NOSE, L_SHOULDER, R_SHOULDER = 0, 11, 12

# Deep breath = shoulder-rise excursion bigger than this fraction of shoulder width,
# within a rolling 6-second span. Normal breathing barely moves the shoulders.
BREATH_AMPLITUDE_FRAC = 0.06
BREATH_SPAN_SEC = 6.0
# Sitting tall = nose-to-shoulder height ratio at or above this fraction of the
# calibrated baseline, sustained over the last 3 seconds.
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


def watch_window(window_sec, baseline, check_breath, check_posture, log=None):
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
    posture_done = not check_posture or baseline is None
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
                        if median(s[1] for s in recent) >= baseline * POSTURE_OK_FRAC:
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


def calibrate(seconds=10):
    """Capture the sitting-tall posture baseline. Returns ratio or None."""
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None
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
        return None
    return median(ratios)
