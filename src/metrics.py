"""Per-dictation metrics and an app log, written to ~/Library/Logs/VoicePrompt/.

  dictations.jsonl   one JSON line per dictation: timings, sizes, outcome. NO spoken text.
  app.log            status messages with timestamps. No spoken text either.

Override the folder with VOICEPROMPT_LOG_DIR (used by the tests).
"""
import json
import os
import time
from pathlib import Path


def log_dir():
    d = Path(os.environ.get("VOICEPROMPT_LOG_DIR") or (Path.home() / "Library" / "Logs" / "VoicePrompt"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def record(rec):
    """Append one dictation record. Never raises: metrics must not break dictation."""
    try:
        with open(log_dir() / "dictations.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_line(msg):
    try:
        with open(log_dir() / "app.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass
