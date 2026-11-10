"""Start-at-login toggle, done with a per-user LaunchAgent (no admin rights needed).

macOS runs every plist in ~/Library/LaunchAgents at login. Enabling writes one that opens
the app; disabling deletes it. Takes effect at the next login (we do not start a second copy now).
"""
import os
import plistlib
from pathlib import Path

LABEL = "com.voiceprompt.app"


def agents_dir():
    return Path(os.environ.get("VOICEPROMPT_AGENTS_DIR", str(Path.home() / "Library" / "LaunchAgents")))


def plist_path():
    return agents_dir() / (LABEL + ".plist")


def bundle_path():
    """Path of the running .app, or None when running from source (no bundle to launch)."""
    try:
        from Foundation import NSBundle
        p = str(NSBundle.mainBundle().bundlePath())
    except ImportError:
        return None
    return p if p.endswith(".app") else None


def is_enabled():
    return plist_path().exists()


def enable(app_path):
    agents_dir().mkdir(parents=True, exist_ok=True)
    data = {"Label": LABEL, "ProgramArguments": ["/usr/bin/open", "-a", str(app_path)], "RunAtLoad": True}
    with open(plist_path(), "wb") as f:
        plistlib.dump(data, f)


def disable():
    try:
        plist_path().unlink()
    except FileNotFoundError:
        pass
