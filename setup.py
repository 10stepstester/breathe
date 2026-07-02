"""py2app recipe — build with: ./venv/bin/python setup.py py2app -A

Alias mode (-A) keeps the .app pointing at these source files and the venv,
so code edits apply on next launch without rebuilding.
"""
from setuptools import setup

setup(
    name="Breathe",
    app=["breathe.py"],
    options={
        "py2app": {
            "argv_emulation": False,
            "plist": {
                "CFBundleName": "Breathe",
                "CFBundleDisplayName": "Breathe",
                "CFBundleIdentifier": "com.ladd.breathe",
                "LSUIElement": True,
                "NSCameraUsageDescription": (
                    "Breathe watches your shoulders and posture for a few "
                    "seconds at a time to know when you've taken a deep "
                    "breath. Nothing is recorded or saved."
                ),
            },
        }
    },
    setup_requires=["py2app"],
)
