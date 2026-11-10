"""Build 'Voice Prompt.app' (personal use). From the repo root:

  .venv/bin/python setup.py py2app -A      # alias mode: the app runs this folder's source live

Alias mode means edits to src/ and glossary/ apply on next launch with no rebuild.
The app needs whisper-cli (brew install whisper.cpp); it does not bundle it.
"""
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parent

setup(
    name="Voice Prompt",
    app=["src/menubar.py"],
    options={"py2app": {
        "argv_emulation": False,
        "packages": ["src"],
        "plist": {
            "CFBundleName": "Voice Prompt",
            "CFBundleDisplayName": "Voice Prompt",
            "CFBundleIdentifier": "com.voiceprompt.app",
            "LSUIElement": True,  # menubar only: no Dock icon
            "NSMicrophoneUsageDescription": "Voice Prompt records your voice to turn it into text.",
            "LSEnvironment": {
                "VOICEPROMPT_ROOT": str(ROOT),
                # GUI apps get a tiny PATH; whisper-cli lives in Homebrew's bin
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                "LC_CTYPE": "UTF-8",
            },
        },
    }},
    setup_requires=["py2app"],
)
