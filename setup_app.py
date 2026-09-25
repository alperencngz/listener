"""py2app build script for Listener.app (alias mode).

Build it with the wrapper:

    ./mac/build_app.sh

Alias mode (``-A``) makes a lightweight bundle that references this repo's
virtualenv in place: torch, ctranslate2, portaudio and friends are NOT frozen,
so the build takes seconds and source edits go live on the next launch. The
trade-off is that the .app only works on the machine where the repo and its
.venv live, which is exactly the "clone it, build it" distribution model.
"""

import os

from setuptools import setup

_HERE = os.path.dirname(os.path.abspath(__file__))
APP = [os.path.join(_HERE, "mac", "listener_launcher.py")]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": os.path.join(_HERE, "mac", "Listener.icns"),
    "plist": {
        "CFBundleName": "Listener",
        "CFBundleDisplayName": "Listener",
        "CFBundleIdentifier": "io.github.alperencngz.listener",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        # WebKit downloads (exports) need macOS 11.3+.
        "LSMinimumSystemVersion": "11.3",
        # Required, or macOS kills the app the moment it opens the microphone.
        "NSMicrophoneUsageDescription":
            "Listener records the microphone when you press Record.",
        # A Finder-launched process has no locale; without this Python would read
        # Turkish transcripts as ASCII and fail.
        "LSEnvironment": {"PYTHONUTF8": "1", "LANG": "en_US.UTF-8", "PYTHONIOENCODING": "utf-8"},
    },
}

setup(
    app=APP,
    options={"py2app": OPTIONS},
)
