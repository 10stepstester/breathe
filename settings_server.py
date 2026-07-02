"""Local settings page for Breathe. Runs as its own process (--settings).

Serves http://127.0.0.1:5052 — live camera with the real judging math, pose
capture with countdown, and plain-English tuning. Localhost only; auto-exits
~90 s after the page is closed so the camera is released.
"""
import json
import os
import threading
import time
import socket
import webbrowser
from datetime import datetime
from statistics import median

import cv2
import mediapipe as mp
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

import vision

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CAPTURE_DIR = os.path.join(APP_DIR, "captures")
PORT = 5052
IDLE_EXIT_SEC = 90

app = Flask(__name__, template_folder="templates")
last_seen = time.time()
config_lock = threading.Lock()

CONFIG_KEYS = {"interval_min", "window_sec", "remind_breath", "remind_posture",
               "pass_score", "posture_tall", "posture_slouch",
               "posture_tall_at", "posture_slouch_at"}


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def update_config(changes):
    with config_lock:
        cfg = load_config()
        cfg.update(changes)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f, indent=2)
    return cfg


class CameraWorker(threading.Thread):
    """Owns the camera. Produces annotated JPEG frames + live features,
    and runs capture jobs (3 s get-ready countdown, then 8 s hold)."""

    def __init__(self):
        super().__init__(daemon=True)
        self.jpeg = None
        self.feats = None
        self.error = None
        self.job = None
        self.last_result = None
        self.lock = threading.Lock()

    def begin_capture(self, kind):
        with self.lock:
            if self.job:
                return False
            label = "sitting-tall" if kind == "tall" else "slouch"
            self.job = {"kind": kind, "label": label, "phase": "countdown",
                        "until": time.monotonic() + 3, "samples": []}
            return True

    def _banner(self, frame, text):
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 54), (30, 30, 30), -1)
        cv2.putText(frame, text, (16, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (80, 220, 160), 2, cv2.LINE_AA)

    def _finish(self, job, clean_frame):
        feats = {}
        for key in set().union(*job["samples"]) if job["samples"] else set():
            vals = [s[key] for s in job["samples"] if key in s]
            if len(vals) >= 30:
                feats[key] = median(vals)
        if "height" not in feats:
            self.last_result = {"kind": job["kind"], "ok": False,
                                "msg": "Couldn't see you clearly — face the camera and try again."}
            return
        os.makedirs(CAPTURE_DIR, exist_ok=True)
        cv2.imwrite(os.path.join(CAPTURE_DIR, f"{job['kind']}.jpg"), clean_frame)
        stamp = datetime.now().astimezone().strftime("%-I:%M %p, %b %-d")
        update_config({f"posture_{job['kind']}": feats,
                       f"posture_{job['kind']}_at": stamp})
        self.last_result = {"kind": job["kind"], "ok": True, "msg": "Captured."}

    def run(self):
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            self.error = "camera"
            return
        pose = mp.solutions.pose.Pose(
            model_complexity=0,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        drawer = mp.solutions.drawing_utils
        try:
            while True:
                ok, frame = cap.read()
                if not ok:
                    time.sleep(0.05)
                    continue
                results = pose.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                if results.pose_landmarks:
                    drawer.draw_landmarks(frame, results.pose_landmarks,
                                          mp.solutions.pose.POSE_CONNECTIONS)
                frame = cv2.flip(frame, 1)
                clean = frame.copy()
                lms = vision._landmarks(results)
                self.feats = lms[0] if lms else None

                job = self.job
                now = time.monotonic()
                if job:
                    remaining = max(0, int(job["until"] - now) + 1)
                    if job["phase"] == "countdown":
                        self._banner(frame, f"Get into your {job['label']} pose... {remaining}")
                        if now >= job["until"]:
                            job["phase"] = "capturing"
                            job["until"] = now + 8
                    else:
                        if self.feats:
                            job["samples"].append(self.feats)
                        self._banner(frame, f"Hold it... {remaining}")
                        if now >= job["until"]:
                            self._finish(job, clean)
                            self.job = None

                okj, buf = cv2.imencode(".jpg", frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, 70])
                if okj:
                    self.jpeg = buf.tobytes()
                time.sleep(1.0 / 15)
        finally:
            cap.release()
            pose.close()


worker = CameraWorker()


@app.route("/")
def index():
    return render_template("settings.html")


@app.route("/stream.mjpg")
def stream():
    def gen():
        while True:
            if worker.jpeg:
                yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                       + worker.jpeg + b"\r\n")
            time.sleep(1.0 / 15)
    return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/state")
def state():
    global last_seen
    last_seen = time.time()
    cfg = load_config()
    tall, slouch = cfg.get("posture_tall"), cfg.get("posture_slouch")
    feats = worker.feats
    score = None
    if feats and tall:
        score = vision._pose_score(feats, tall, slouch)
    job = worker.job
    return jsonify({
        "config": cfg,
        "feats": feats,
        "score": score,
        "pass_line": vision._pass_line(slouch, cfg.get("pass_score")) if tall else None,
        "gaps": vision.feature_gaps(tall, slouch),
        "capturing": {"kind": job["kind"], "phase": job["phase"]} if job else None,
        "last_result": worker.last_result,
        "camera_error": worker.error,
        "photos": {k: os.path.exists(os.path.join(CAPTURE_DIR, f"{k}.jpg"))
                   for k in ("tall", "slouch")},
    })


@app.route("/capture/<kind>", methods=["POST"])
def capture(kind):
    if kind not in ("tall", "slouch"):
        return jsonify({"success": False, "error": "bad kind"}), 400
    started = worker.begin_capture(kind)
    return jsonify({"success": started})


@app.route("/config", methods=["POST"])
def set_config():
    changes = {k: v for k, v in (request.get_json(force=True) or {}).items()
               if k in CONFIG_KEYS}
    if not changes:
        return jsonify({"success": False, "error": "no valid keys"}), 400
    update_config(changes)
    return jsonify({"success": True})


@app.route("/captures/<path:name>")
def captures(name):
    return send_from_directory(CAPTURE_DIR, name)


def idle_watchdog():
    while True:
        time.sleep(10)
        if time.time() - last_seen > IDLE_EXIT_SEC:
            os._exit(0)


def main():
    # Already running? Just bring up the page.
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            webbrowser.open(f"http://127.0.0.1:{PORT}")
            return
    except OSError:
        pass
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    worker.start()
    threading.Thread(target=idle_watchdog, daemon=True).start()
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
