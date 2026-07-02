# Breathe

Menu bar app (just for Ladd) that conditions deep breathing and upright posture.

## How it works
Every N minutes (default 10) the camera turns on for a short window (default 45 s).
The green camera light IS the cue: sit tall and take a deep breath and the light
turns off early and the app stays silent ("caught"). Miss it and you get a macOS
notification ("pinged"). The menu shows the daily caught/pinged score.

- Deep breath = shoulder-rise excursion > 6% of shoulder width within ~6 s (MediaPipe pose).
- Sitting tall = nose-to-shoulder height ratio ≥ 88% of the calibrated baseline.
- Away from desk → window is skipped silently, no stats.
- Another app using the camera (Zoom, FaceTime) → window skipped silently
  (CoreMediaIO "device is running somewhere" check).
- All local. No frames saved, nothing leaves the Mac.

## Files
- `breathe.py` — rumps menu bar app, scheduling, notifications, LaunchAgent management
- `vision.py` — camera capture, pose detection, breath/posture logic, camera-busy check
- `config.json` — settings + posture baseline + daily stats (created on first run)
- `breathe.log` — app log (via LaunchAgent)

## Install / run
Runs as LaunchAgent `com.ladd.breathe` (starts at login). Toggle from the menu, or:

```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.ladd.breathe.plist   # start
launchctl bootout gui/$UID/com.ladd.breathe                                  # stop
```

First run: click the 🫁 icon → "Recalibrate posture…" → sit tall for 10 s.
macOS will ask for camera permission on that first calibration — click Allow.

## Tuning
Interval and window length are in the menu. Detection thresholds live at the top
of `vision.py` (`BREATH_AMPLITUDE_FRAC`, `POSTURE_OK_FRAC`).
