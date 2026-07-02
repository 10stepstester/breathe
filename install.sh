#!/bin/bash
# One-command install for Breathe. Run from the project folder:
#   bash install.sh
set -e
cd "$(dirname "$0")"

PY=$(command -v python3.12 || command -v python3.11 || command -v python3.10 || command -v python3)
if [ -z "$PY" ]; then
    echo "No python3 found. Install Python 3.12 from https://www.python.org/downloads/ and rerun."
    exit 1
fi
echo "Using $PY"

"$PY" -m venv venv
./venv/bin/pip install --quiet --upgrade pip
if ! ./venv/bin/pip install --quiet mediapipe opencv-python rumps py2app pyobjc-framework-AVFoundation; then
    echo ""
    echo "Install failed — most likely your Python version isn't supported by MediaPipe."
    echo "Install Python 3.12 from https://www.python.org/downloads/, delete the venv folder, and rerun this."
    exit 1
fi

./venv/bin/python setup.py py2app -A >/dev/null
./venv/bin/python - <<'EOF'
import breathe
class Stub:
    pass
breathe.BreatheApp.write_plist(Stub())
print("LaunchAgent installed")
EOF

launchctl bootout gui/$UID/com.ladd.breathe 2>/dev/null || true
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.ladd.breathe.plist

echo ""
echo "Done — look for the lungs icon in your menu bar."
echo "1. Click Allow when macOS asks about the camera."
echo "2. Click the icon and capture your good posture, then your slouch."
