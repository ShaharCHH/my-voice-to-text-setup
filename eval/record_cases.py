#!/usr/bin/env python3
"""Record yourself reading eval/sentences.json, one sentence at a time (about 8 minutes).

  .venv/bin/python eval/record_cases.py            # record the ones you have not done yet
  .venv/bin/python eval/record_cases.py --redo kub03 cloud02
  .venv/bin/python eval/record_cases.py --category edits

Run it in a normal terminal window (it asks questions). Do it the way you really dictate:
your usual microphone, your usual input volume and distance, a natural pace. Do NOT turn the
volume up for this: how the app copes with your real microphone is part of what is measured.
Recordings go to recordings/<id>.wav (gitignored: it is your voice). You can quit with q and
carry on later; sentences already recorded are skipped.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from eval.run_eval import load_cases  # noqa: E402
from src import recorder  # noqa: E402

MIN_SECONDS = 0.5
GATE = 150  # the app's own "this is silence" level


def record_all(cases, audio_dir, make_recorder=recorder.Recorder, ask=input, say=print):
    """Walk through `cases`; returns how many recordings were saved. `ask`/`say` are injectable for tests."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    rec, saved = make_recorder(), 0
    for i, c in enumerate(cases, 1):
        while True:
            say("\n[%d/%d]  %s\n\n    %s\n" % (i, len(cases), c["category"], c["say"]))
            ask("  Press Enter, then read it aloud ... ")
            rec.start()
            ask("  recording ... press Enter when you have finished ")
            audio = rec.stop()
            secs, level = recorder.seconds(audio), recorder.speech_level(audio)
            if secs < MIN_SECONDS or level < GATE:
                say("  that recording is %s; let's try again." % ("too short" if secs < MIN_SECONDS else "silent (level %.0f)" % level))
                continue
            choice = ask("  %.1f s, level %.0f.  Enter = keep, r = redo, s = skip, q = quit: " % (secs, level)).strip().lower()
            if choice == "r":
                continue
            if choice == "q":
                return saved
            if choice != "s":
                recorder.save_wav(audio, audio_dir / (c["id"] + ".wav"))
                saved += 1
            break
    return saved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio-dir", default=str(ROOT / "recordings"))
    ap.add_argument("--category", nargs="*")
    ap.add_argument("--redo", nargs="*", default=[], help="ids to record again even if they exist")
    args = ap.parse_args()
    audio_dir = Path(args.audio_dir)
    cases = load_cases(audio_dir, args.category)
    todo = [c for c in cases if c["id"] in args.redo or not c["wav"].exists()]
    if not todo:
        print("everything is recorded (%d clips in %s). Use --redo ID to redo one." % (len(cases), audio_dir))
        return 0
    print("%d sentences to record (%d already done). Read each aloud, naturally, at your normal setup." % (len(todo), len(cases) - len(todo)))
    saved = record_all(todo, audio_dir)
    print("\nsaved %d recording(s) in %s" % (saved, audio_dir))
    print("next:  .venv/bin/python eval/run_eval.py compare --stt turbo-en turbo-en-noprompt turbo-en-context turbo-en-norm large-en")
    return 0


if __name__ == "__main__":
    sys.exit(main())
