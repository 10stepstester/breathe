"""Breathe — menu bar reminder to take a deep breath and sit tall.

Every N minutes the camera comes on for a short window (the green light IS the
cue). Respond in time and it stays silent; miss it and you get a notification.
"""
import json
import os
import subprocess
import sys
import threading
import math
from datetime import datetime, timedelta

import rumps
from AVFoundation import AVCaptureDevice, AVMediaTypeVideo

import vision

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
PID_PATH = os.path.join(APP_DIR, "breathe.pid")
PLIST_PATH = os.path.expanduser("~/Library/LaunchAgents/com.ladd.breathe.plist")
APP_EXECUTABLE = os.path.join(APP_DIR, "dist", "Breathe.app", "Contents",
                              "MacOS", "Breathe")

INTERVAL_CHOICES = [5, 10, 15, 20, 30]
WINDOW_CHOICES = [30, 45, 60, 90]

DEFAULTS = {
    "interval_min": 10,
    "window_sec": 45,
    "remind_breath": True,
    "remind_posture": True,
    "baseline_posture": None,
    "stats": {"date": "", "caught": 0, "pinged": 0},
}


def log(msg):
    print(f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {msg}",
          flush=True)


def notify(text):
    text = text.replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e", f'display notification "{text}" with title "Breathe"'],
        capture_output=True,
    )


def ensure_camera_permission():
    """Request camera access on the main thread at startup.

    Status codes: 0 = not asked yet, 1 = restricted, 2 = denied, 3 = granted.
    """
    status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeVideo)
    if status == 3:
        return
    if status == 0:
        def done(granted):
            log(f"camera permission granted: {granted}")
            if not granted:
                notify("Camera permission denied — enable Breathe in "
                       "System Settings → Privacy & Security → Camera.")
        AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVMediaTypeVideo, done)
    else:
        notify("Camera permission needed — enable Breathe in "
               "System Settings → Privacy & Security → Camera.")


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg


def already_running():
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True).stdout
        return "breathe" in out.lower()
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


class BreatheApp(rumps.App):
    def __init__(self):
        super().__init__("Breathe", title="🫁", quit_button=None)
        ensure_camera_permission()
        self.cfg = load_config()
        self.watching = False
        self.calibrating = False
        self.paused_until = None
        self.next_check = datetime.now().astimezone() + timedelta(
            minutes=self.cfg["interval_min"]
        )

        self.status_item = rumps.MenuItem("Starting up")
        self.stats_item = rumps.MenuItem("")
        self.check_now = rumps.MenuItem("Check now", callback=self.on_check_now)
        self.pause_hour = rumps.MenuItem("Pause for 1 hour", callback=self.on_pause_hour)
        self.pause_day = rumps.MenuItem("Pause until tomorrow", callback=self.on_pause_day)
        self.resume_item = rumps.MenuItem("Resume now", callback=self.on_resume)

        self.interval_menu = rumps.MenuItem("Check every")
        for m in INTERVAL_CHOICES:
            self.interval_menu.add(rumps.MenuItem(f"{m} min", callback=self.on_interval))
        self.window_menu = rumps.MenuItem("Watch window")
        for s in WINDOW_CHOICES:
            self.window_menu.add(rumps.MenuItem(f"{s} sec", callback=self.on_window))

        self.breath_item = rumps.MenuItem("Deep breaths", callback=self.on_toggle_breath)
        self.posture_item = rumps.MenuItem("Sitting tall", callback=self.on_toggle_posture)
        self.calibrate_item = rumps.MenuItem("Recalibrate posture…", callback=self.on_calibrate)
        self.login_item = rumps.MenuItem("Start at login", callback=self.on_toggle_login)

        self.menu = [
            self.status_item,
            self.stats_item,
            None,
            self.check_now,
            self.pause_hour,
            self.pause_day,
            self.resume_item,
            None,
            self.interval_menu,
            self.window_menu,
            None,
            self.breath_item,
            self.posture_item,
            None,
            self.calibrate_item,
            self.login_item,
            None,
            rumps.MenuItem("Quit", callback=self.on_quit),
        ]
        self.sync_menu_state()

        if self.cfg["baseline_posture"] is None:
            notify("Welcome! Click the lungs icon and choose Recalibrate posture to get started.")

        self.timer = rumps.Timer(self.tick, 15)
        self.timer.start()

    # ---- config ----

    def save(self):
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(self.cfg, f, indent=2)
        except OSError as e:
            log(f"config save failed: {e}")

    def roll_stats(self):
        today = datetime.now().astimezone().date().isoformat()
        if self.cfg["stats"].get("date") != today:
            self.cfg["stats"] = {"date": today, "caught": 0, "pinged": 0}
            self.save()

    # ---- menu state ----

    def sync_menu_state(self):
        for item in self.interval_menu.values():
            item.state = 1 if item.title == f"{self.cfg['interval_min']} min" else 0
        for item in self.window_menu.values():
            item.state = 1 if item.title == f"{self.cfg['window_sec']} sec" else 0
        self.breath_item.state = 1 if self.cfg["remind_breath"] else 0
        self.posture_item.state = 1 if self.cfg["remind_posture"] else 0
        self.login_item.state = 1 if os.path.exists(PLIST_PATH) else 0
        self.resume_item.set_callback(self.on_resume if self.paused_until else None)

    def tick(self, _timer=None):
        self.roll_stats()
        now = datetime.now().astimezone()
        s = self.cfg["stats"]
        self.stats_item.title = f"Today: caught {s['caught']} · pinged {s['pinged']}"

        if self.paused_until and now >= self.paused_until:
            self.paused_until = None
            self.next_check = now + timedelta(minutes=self.cfg["interval_min"])
            self.sync_menu_state()

        if self.calibrating:
            self.status_item.title = "Calibrating — sit tall!"
            self.title = "🫁"
        elif self.watching:
            self.status_item.title = "Watching now — breathe deep, sit tall"
            self.title = "👀"
        elif self.paused_until:
            self.status_item.title = f"Paused until {self.paused_until.strftime('%-I:%M %p')}"
            self.title = "💤"
        elif self.cfg["baseline_posture"] is None:
            self.status_item.title = "Not calibrated yet — run Recalibrate posture"
            self.title = "🫁"
        else:
            mins = max(0, math.ceil((self.next_check - now).total_seconds() / 60))
            self.status_item.title = f"Watching in {mins} min"
            self.title = "🫁"
            if now >= self.next_check:
                self.start_window()

    # ---- the check window ----

    def start_window(self):
        if self.watching or self.calibrating:
            return
        self.watching = True
        threading.Thread(target=self.run_window, daemon=True).start()

    def run_window(self):
        try:
            if vision.camera_in_use_elsewhere():
                log("camera busy elsewhere — skipping this window")
                return
            log("window open")
            result = vision.watch_window(
                self.cfg["window_sec"],
                self.cfg["baseline_posture"],
                self.cfg["remind_breath"],
                self.cfg["remind_posture"],
                log=log,
            )
            log(f"window result: {result}")
            if result["error"] == "camera":
                notify("Camera unavailable. Check System Settings → Privacy & Security → Camera.")
                return
            if not result["present"]:
                return  # away from desk — no ping, no stats
            misses = []
            if self.cfg["remind_breath"] and not result["breath"]:
                misses.append("take a deep breath")
            if self.cfg["remind_posture"] and not result["posture"]:
                misses.append("straighten up")
            if misses:
                self.cfg["stats"]["pinged"] += 1
                notify(" and ".join(misses).capitalize() + ".")
            else:
                self.cfg["stats"]["caught"] += 1
            self.save()
        except Exception as e:
            log(f"window error: {e}")
        finally:
            self.next_check = datetime.now().astimezone() + timedelta(
                minutes=self.cfg["interval_min"]
            )
            self.watching = False

    # ---- callbacks ----

    def on_check_now(self, _):
        if self.watching or self.calibrating:
            return
        if self.cfg["baseline_posture"] is None:
            notify("Calibrate first — click Recalibrate posture in the menu.")
            return
        self.paused_until = None
        self.sync_menu_state()
        self.start_window()
        self.tick()

    def on_pause_hour(self, _):
        self.paused_until = datetime.now().astimezone() + timedelta(hours=1)
        self.sync_menu_state()
        self.tick()

    def on_pause_day(self, _):
        tomorrow = datetime.now().astimezone() + timedelta(days=1)
        self.paused_until = tomorrow.replace(hour=7, minute=0, second=0, microsecond=0)
        self.sync_menu_state()
        self.tick()

    def on_resume(self, _):
        self.paused_until = None
        self.next_check = datetime.now().astimezone() + timedelta(
            minutes=self.cfg["interval_min"]
        )
        self.sync_menu_state()
        self.tick()

    def on_interval(self, item):
        self.cfg["interval_min"] = int(item.title.split()[0])
        self.next_check = datetime.now().astimezone() + timedelta(
            minutes=self.cfg["interval_min"]
        )
        self.save()
        self.sync_menu_state()
        self.tick()

    def on_window(self, item):
        self.cfg["window_sec"] = int(item.title.split()[0])
        self.save()
        self.sync_menu_state()

    def on_toggle_breath(self, _):
        self.cfg["remind_breath"] = not self.cfg["remind_breath"]
        self.save()
        self.sync_menu_state()

    def on_toggle_posture(self, _):
        self.cfg["remind_posture"] = not self.cfg["remind_posture"]
        self.save()
        self.sync_menu_state()

    def on_calibrate(self, _):
        if self.watching or self.calibrating:
            return
        if vision.camera_in_use_elsewhere():
            notify("Camera is busy — try calibrating after your call.")
            return
        self.calibrating = True
        self.tick()
        notify("Calibrating for 10 seconds — sit the way you want to sit.")
        threading.Thread(target=self.run_calibration, daemon=True).start()

    def run_calibration(self):
        try:
            baseline, error = vision.calibrate(10)
            if error == "camera":
                notify("Camera unavailable. Check System Settings → "
                       "Privacy & Security → Camera, then try again.")
                return
            if error == "not_visible":
                notify("Couldn't see you clearly. Face the camera and try again.")
                return
            first_time = self.cfg["baseline_posture"] is None
            self.cfg["baseline_posture"] = baseline
            self.save()
            log(f"calibrated baseline {baseline:.3f}")
            notify("Calibration saved. You're all set." if first_time
                   else "Calibration updated.")
            if first_time:
                self.next_check = datetime.now().astimezone() + timedelta(
                    minutes=self.cfg["interval_min"]
                )
        except Exception as e:
            log(f"calibration error: {e}")
            notify("Calibration failed — see breathe.log.")
        finally:
            self.calibrating = False

    def on_toggle_login(self, _):
        if os.path.exists(PLIST_PATH):
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/com.ladd.breathe"],
                           capture_output=True)
            os.remove(PLIST_PATH)
        else:
            self.write_plist()
        self.sync_menu_state()

    def write_plist(self):
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.ladd.breathe</string>
    <key>ProgramArguments</key>
    <array>
        <string>{APP_EXECUTABLE}</string>
    </array>
    <key>WorkingDirectory</key><string>{APP_DIR}</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>StandardOutPath</key><string>{os.path.join(APP_DIR, "breathe.log")}</string>
    <key>StandardErrorPath</key><string>{os.path.join(APP_DIR, "breathe.log")}</string>
</dict>
</plist>
"""
        with open(PLIST_PATH, "w") as f:
            f.write(plist)

    def on_quit(self, _):
        try:
            os.remove(PID_PATH)
        except FileNotFoundError:
            pass
        rumps.quit_application()


if __name__ == "__main__":
    if already_running():
        log("another instance is already running — exiting")
        sys.exit(0)
    with open(PID_PATH, "w") as f:
        f.write(str(os.getpid()))
    log("breathe starting")
    BreatheApp().run()
