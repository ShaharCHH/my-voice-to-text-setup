#!/usr/bin/env python3
"""Push-to-talk Hebrew/English speech -> English prompt pasted at the cursor (terminal version).

Hold the hotkey (default: Right Option), speak, release. Esc while holding = discard.
Double-tap the hotkey while it is working = cancel. The menubar app (menubar.py) is the same
thing with a UI. Needs Microphone, Accessibility and Input Monitoring for the host app.

  .venv/bin/python src/app.py                       # live
  .venv/bin/python src/app.py --mode english        # faster, English only
  .venv/bin/python src/app.py --wav some.wav        # dry run: no mic, no paste
  .venv/bin/python src/app.py --stt fake --llm fake --wav some.wav   # wiring test only
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.core import Dictation, check_accessibility, process_wav  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="hebrew", help="hebrew or english (see modes in eval/setups.json)")
    ap.add_argument("--stt", help="override the mode's STT setup (name in eval/setups.json)")
    ap.add_argument("--llm", help="override the mode's LLM setup")
    ap.add_argument("--prompt", help="override the mode's prompt file")
    ap.add_argument("--hotkey", default="alt_r", help="pynput Key name: alt_r, alt_l, cmd_r, ctrl_r, f13...")
    ap.add_argument("--wav", help="dry run on this 16 kHz wav: print the result, no mic, no paste")
    args = ap.parse_args()

    setups = json.loads((ROOT / "eval" / "setups.json").read_text(encoding="utf-8"))
    mode = setups["modes"][args.mode]
    stt_cfg = setups["stt"][args.stt or mode["stt"]]
    llm_cfg = setups["llm"][args.llm or mode["llm"]]
    system = (ROOT / (args.prompt or mode["prompt"])).read_text(encoding="utf-8")

    if args.wav:
        heard, out, ts, tl = process_wav(args.wav, stt_cfg, llm_cfg, system)
        print("heard : %s\noutput: %s\n(stt %.1fs, llm %.1fs)" % (heard, out, ts, tl))
        return 0

    from pynput import keyboard
    if not check_accessibility():
        print("WARNING: this terminal is NOT trusted for Accessibility, so pasting will not work.\n"
              "  System Settings > Privacy & Security > Accessibility: remove and re-add your terminal app,\n"
              "  then quit it completely (Cmd+Q) and reopen. Results will still be copied to the clipboard.",
              file=sys.stderr)
    d = Dictation(stt_cfg, llm_cfg, system, getattr(keyboard.Key, args.hotkey),
                  debug=print, mode=args.mode)
    print("Ready. Hold %s to talk (Esc = discard, double-tap = cancel). Ctrl+C to quit." % args.hotkey)
    with keyboard.Listener(on_press=d.on_press, on_release=d.on_release) as listener:
        listener.join()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
