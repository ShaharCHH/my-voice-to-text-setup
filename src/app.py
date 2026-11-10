#!/usr/bin/env python3
"""Push-to-talk dictation (terminal version): hold the hotkey, speak English, release.

Hold the hotkey (default: Right Option), speak, release. Esc while holding = discard.
Double-tap the hotkey while it is working = cancel. The menubar app (menubar.py) is the same
thing with a UI. Needs Microphone, Accessibility and Input Monitoring for the host app.

  .venv/bin/python src/app.py                          # live
  .venv/bin/python src/app.py --wav some.wav           # dry run: no mic, no paste
  .venv/bin/python src/app.py --stt fake --cleanup fake --wav some.wav   # wiring test only
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import diagnostics, metrics  # noqa: E402
from src.core import Dictation, check_accessibility, process_wav  # noqa: E402


def main():
    settings = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stt", default=settings["default"]["stt"], help="speech setup in settings.json")
    ap.add_argument("--cleanup", default=settings["default"]["cleanup"], help="cleanup setup in settings.json")
    ap.add_argument("--hotkey", default="alt_r", help="pynput Key name: alt_r, alt_l, cmd_r, ctrl_r, f13...")
    ap.add_argument("--wav", help="dry run on this 16 kHz wav: print the result, no mic, no paste")
    args = ap.parse_args()
    stt_cfg, cleanup_cfg = settings["stt"][args.stt], settings["cleanup"][args.cleanup]

    if args.wav:
        heard, out, ts, tc = process_wav(args.wav, stt_cfg, cleanup_cfg)
        print("heard : %s\noutput: %s\n(stt %.1fs, cleanup %.2fs)" % (heard, out, ts, tc))
        return 0

    from pynput import keyboard
    diagnostics.install()
    if not check_accessibility():
        print("WARNING: this terminal is NOT trusted for Accessibility, so pasting will not work.\n"
              "  System Settings > Privacy & Security > Accessibility: remove and re-add your terminal app,\n"
              "  then quit it completely (Cmd+Q) and reopen. Results will still be copied to the clipboard.",
              file=sys.stderr)
    d = Dictation(stt_cfg, cleanup_cfg, getattr(keyboard.Key, args.hotkey),
                  debug=print, stt_name=args.stt, on_text=metrics.record_text)
    print("Ready. Hold %s to talk (Esc = discard, double-tap = cancel). Ctrl+C to quit." % args.hotkey)
    with keyboard.Listener(on_press=d.on_press, on_release=d.on_release) as listener:
        listener.join()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
