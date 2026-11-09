"""Per-dictation metrics and an app log, written to ~/Library/Logs/VoicePrompt/.

  dictations.jsonl   one JSON line per dictation: timings, sizes, outcome. NO spoken text.
  app.log            status messages with timestamps. No spoken text either.
  transcripts.jsonl  what Whisper heard and what the LLM produced, side by side (spoken text!).
                     Local only; turn off with "log_text": false in config.json.

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


def record_text(heard, output, mode=""):
    """Append the Whisper transcript and the LLM output so the two can be compared later."""
    try:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": mode, "heard": heard, "output": output}
        with open(log_dir() / "transcripts.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def log_line(msg):
    try:
        with open(log_dir() / "app.log", "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass
