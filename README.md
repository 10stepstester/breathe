# Breathe

Menu bar app for macOS that conditions deep breathing and upright posture.

## How it works
Every N minutes (default 10) the camera turns on for a short window (default 45 s).
The green camera light IS the cue: sit tall and take a deep breath and the light
turns off early and the app stays silent ("caught"). Miss it and you get a macOS
notification ("pinged"). The menu shows the daily caught/pinged score.

- Deep breath = shoulder-rise excursion > 6% of shoulder width within ~6 s (MediaPipe pose).
- Sitting tall = posture score above the pass line. You capture two reference
  poses from the menu (your slouch and your sitting-tall); each check scores the
  live pose 0 (like your slouch) to 1 (like your tall) using whichever features
  actually differ between them — head height above shoulders, and apparent face
  size (head jutting toward the screen; the dominant signal when the camera is
  low, e.g. laptop on lap). Pass = score ≥ 0.55, sustained 3 s.
- "Show what it sees…" opens a live window with the skeleton overlay and the
  exact numbers being judged.
- Away from desk → window is skipped silently, no stats.
- Another app using the camera (Zoom, FaceTime) → window skipped silently
  (CoreMediaIO "device is running somewhere" check).
- All local. No frames saved, nothing leaves the Mac.

## Files
- `breathe.py` — rumps menu bar app, scheduling, notifications, LaunchAgent management
- `vision.py` — camera capture, pose detection, breath/posture logic, camera-busy check
- `config.json` — settings + posture baseline + daily stats (created on first run)
- `breathe.log` — app log (via LaunchAgent)

## Install (any Mac)
Open Terminal, paste this, press Enter:

```bash
git clone https://github.com/10stepstester/breathe.git ~/breathe && cd ~/breathe && bash install.sh
```

Then:
1. Click **Allow** when macOS asks for camera access.
2. Click the 🫁 menu bar icon → **"I'm sitting how I want — capture it"** → hold it 8 s.
3. Click **"I'm slouching — capture it"** → hold your honest slouch 8 s.

That's it. It starts at login from then on. Everything runs locally — no
account, no server, nothing recorded or uploaded, ever.

Requires macOS + Python 3.10–3.12 (install from python.org if the script says so).

## Start / stop manually
Runs as LaunchAgent `com.ladd.breathe`. Toggle "Start at login" from the menu, or:

```bash
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.ladd.breathe.plist   # start
launchctl bootout gui/$UID/com.ladd.breathe                                  # stop
```

## Tuning
Interval and window length are in the menu. Detection thresholds live at the top
of `vision.py` (`BREATH_AMPLITUDE_FRAC`, `POSTURE_OK_FRAC`).
