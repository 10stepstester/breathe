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
L_EYE_OUTER, R_EYE_OUTER = 3, 6

# Deep breath = shoulder-rise excursion bigger than this fraction of shoulder width,
# within a rolling 6-second span. Normal breathing barely moves the shoulders.
BREATH_AMPLITUDE_FRAC = 0.06
BREATH_SPAN_SEC = 6.0
# Posture score: 0 = looks like your captured slouch, 1 = like your captured
# tall pose, judged on whichever features actually differ between the two.
# Pass = sustained score above PASS_SCORE over the last 3 seconds.
PASS_SCORE = 0.55
# Features whose tall/slouch gap is under 3% are noise — ignored.
MIN_FEATURE_GAP = 0.03
# Fallback when only a tall pose is calibrated (no slouch reference):
# sitting tall = height ratio >= 88% of the tall baseline.
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
    """Return (features, shoulder_mid_y, shoulder_width) or None if not visible.

    Features (all scaled by shoulder width so camera distance cancels out):
      height — how high the nose rides above the shoulder line (drops on slouch)
      face   — apparent eye-to-eye size (grows when the head juts toward the
               screen — the dominant slouch signal when the camera is low,
               e.g. laptop on lap)
    """
    if not results.pose_landmarks:
        return None
    lm = results.pose_landmarks.landmark
    nose, ls, rs = lm[NOSE], lm[L_SHOULDER], lm[R_SHOULDER]
    if min(nose.visibility, ls.visibility, rs.visibility) < 0.5:
        return None
    width = abs(ls.x - rs.x)
    if width < 0.05:
        return None
    shoulder_y = (ls.y + rs.y) / 2.0
    feats = {"height": (shoulder_y - nose.y) / width}
    le, re = lm[L_EYE_OUTER], lm[R_EYE_OUTER]
    if min(le.visibility, re.visibility) >= 0.5:
        eye_dist = ((le.x - re.x) ** 2 + (le.y - re.y) ** 2) ** 0.5
        feats["face"] = eye_dist / width
    return feats, shoulder_y, width


def posture_score(feats, tall, slouch):
    """Where the current pose sits between the two references.

    0 = exactly your slouch, 1 = exactly your tall pose (can overshoot either
    way). Each feature votes, weighted by how far apart the two references
    are on it; features that barely differ are ignored. None = the references
    don't differ enough on anything to judge.
    """
    votes, weights = [], []
    for key, value in feats.items():
        t, s = tall.get(key), slouch.get(key)
        if t is None or s is None:
            continue
        ref = (abs(t) + abs(s)) / 2.0
        gap = t - s
        if ref == 0 or abs(gap) < MIN_FEATURE_GAP * ref:
            continue
        votes.append((value - s) / gap)
        weights.append(abs(gap) / ref)
    if not votes:
        return None
    return sum(v * w for v, w in zip(votes, weights)) / sum(weights)


def _pose_score(feats, tall, slouch):
    """Score against available references; legacy height-only if no slouch."""
    if slouch:
        return posture_score(feats, tall, slouch)
    t = tall.get("height")
    h = feats.get("height")
    if t and h:
        return h / t
    return None


def _pass_line(slouch, pass_score=None):
    if slouch:
        return pass_score if pass_score else PASS_SCORE
    return POSTURE_OK_FRAC


FEATURE_LABELS = {
    "height": "head height above shoulders",
    "face": "head-to-screen distance",
}


def feature_gaps(tall, slouch):
    """Plain-English view of how the two reference poses differ.

    Returns [{key, label, pct, used}] — pct is the relative gap in percent,
    used is whether the feature is big enough to count toward the score.
    """
    out = []
    if not tall or not slouch:
        return out
    for key in sorted(set(tall) & set(slouch)):
        ref = (abs(tall[key]) + abs(slouch[key])) / 2.0
        if ref == 0:
            continue
        rel = abs(tall[key] - slouch[key]) / ref
        out.append({
            "key": key,
            "label": FEATURE_LABELS.get(key, key),
            "pct": round(rel * 100, 1),
            "used": rel >= MIN_FEATURE_GAP,
        })
    out.sort(key=lambda g: -g["pct"])
    return out


def _breath_in(recent):
    """True if the shoulder trace shows a rise-and-return — a breath's round
    trip — rather than a posture step (move and stay) or a lean.

    recent: [(t, smoothed_y, shoulder_width), ...] spanning at least 2 s.
    """
    ys = [s[1] for s in recent]
    widths = [s[2] for s in recent]
    scale = median(widths)
    # Leaning toward/away from the camera changes apparent shoulder width;
    # a real breath doesn't.
    if (max(widths) - min(widths)) / scale > 0.12:
        return False
    amp = max(ys) - min(ys)
    if amp <= BREATH_AMPLITUDE_FRAC * scale:
        return False
    i_min = ys.index(min(ys))
    if i_min == 0 or i_min == len(ys) - 1:
        return False  # highest shoulder point is at the edge — still mid-move
    before = max(ys[:i_min])
    after = max(ys[i_min + 1:])
    low = ys[i_min]
    # Shoulders rose from a settled level AND came most of the way back down.
    return (before - low) >= 0.6 * amp and (after - low) >= 0.4 * amp


def watch_window(window_sec, tall, slouch, check_breath, check_posture,
                 pass_score=None, log=None, on_breath=None):
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
    posture_samples = []   # (t, posture score)
    ema = None
    breath_done = not check_breath
    posture_done = not check_posture or not tall
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
                feats, shoulder_y, width = lms
                ema = shoulder_y if ema is None else 0.3 * shoulder_y + 0.7 * ema
                shoulder_samples.append((now, ema, width))
                if tall:
                    score = _pose_score(feats, tall, slouch)
                    if score is not None:
                        posture_samples.append((now, score))

                if not breath_done:
                    recent = [s for s in shoulder_samples if now - s[0] <= BREATH_SPAN_SEC]
                    if (len(recent) >= 10
                            and recent[-1][0] - recent[0][0] >= 2.0
                            and _breath_in(recent)):
                        breath_done = True
                        if log:
                            log("deep breath detected")
                        if on_breath:
                            on_breath()

                if not posture_done:
                    recent = [s for s in posture_samples if now - s[0] <= POSTURE_SPAN_SEC]
                    if len(recent) >= 10:
                        if median(s[1] for s in recent) >= _pass_line(slouch, pass_score):
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


def preview(tall, slouch, pass_score=None, max_sec=120):
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
                feats, shoulder_y, width = lms
                ema = shoulder_y if ema is None else 0.3 * shoulder_y + 0.7 * ema
                shoulder_samples.append((now, ema, width))
                shoulder_samples = [
                    s for s in shoulder_samples if now - s[0] <= BREATH_SPAN_SEC
                ]

                if tall:
                    score = _pose_score(feats, tall, slouch)
                    need = _pass_line(slouch, pass_score)
                    if score is None:
                        put(frame,
                            "Poses too similar to judge - recapture both",
                            40, (0, 200, 255))
                    else:
                        ok = score >= need
                        label = (f"posture score {score:.2f}  (pass = {need:.2f}+"
                                 + (", 0 = your slouch, 1 = your tall)" if slouch
                                    else ")"))
                        put(frame, label, 40, (0, 200, 0) if ok else (0, 0, 255))
                        put(frame, "SITTING TALL" if ok else "SLOUCHED",
                            80, (0, 200, 0) if ok else (0, 0, 255), 1.0)
                else:
                    put(frame, "not calibrated yet", 40, (0, 200, 255))
                detail = f"raw: height {feats['height']:.2f}"
                if "face" in feats:
                    detail += f"   face {feats['face']:.2f}"
                put(frame, detail, frame.shape[0] - 20, (200, 200, 200), 0.55)

                if len(shoulder_samples) >= 10:
                    ys = [s[1] for s in shoulder_samples]
                    scale = median(s[2] for s in shoulder_samples)
                    amp = max(ys) - min(ys)
                    need_amp = BREATH_AMPLITUDE_FRAC * scale
                    span_ok = shoulder_samples[-1][0] - shoulder_samples[0][0] >= 2.0
                    if span_ok and _breath_in(shoulder_samples):
                        breath_flash_until = now + 3.0
                    put(frame,
                        f"shoulder movement {amp:.3f}  (deep breath = {need_amp:.3f}+ "
                        f"rise-and-return)",
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


def calibrate(seconds=10, snapshot_path=None):
    """Capture a posture reference (hold the pose while it runs).

    Returns (features dict, None) on success, (None, "camera") if the camera
    couldn't open, or (None, "not_visible") if no clear view of the body.
    Saves a mirrored snapshot of the pose to snapshot_path if given.
    """
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        return None, "camera"
    pose = mp.solutions.pose.Pose(model_complexity=0, min_detection_confidence=0.5)
    samples = []
    snapshot = None
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            ok, frame = cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            lms = _landmarks(pose.process(rgb))
            if lms is not None:
                samples.append(lms[0])
                snapshot = frame
            time.sleep(1.0 / TARGET_FPS)
    finally:
        cap.release()
        pose.close()
    if len(samples) < 30:
        return None, "not_visible"
    feats = {}
    for key in set().union(*samples):
        vals = [s[key] for s in samples if key in s]
        if len(vals) >= 30:
            feats[key] = median(vals)
    if "height" not in feats:
        return None, "not_visible"
    if snapshot_path and snapshot is not None:
        try:
            cv2.imwrite(snapshot_path, cv2.flip(snapshot, 1))
        except cv2.error:
            pass
    return feats, None
