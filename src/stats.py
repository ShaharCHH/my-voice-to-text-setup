#!/usr/bin/env python3
"""Summarise ~/Library/Logs/VoicePrompt/dictations.jsonl (timings per speech setup).

  .venv/bin/python src/stats.py            # all dictations
  .venv/bin/python src/stats.py --last 20  # only the most recent 20
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import metrics  # noqa: E402

FIELDS = ("audio_s", "stt_s", "clean_s", "paste_s", "total_s")


def pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(p * (len(vals) - 1))))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--last", type=int, help="only the most recent N dictations")
    args = ap.parse_args()
    path = metrics.log_dir() / "dictations.jsonl"
    if not path.exists():
        sys.exit("no metrics yet: %s" % path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.last:
        rows = rows[-args.last:]
    by_mode = defaultdict(list)
    for r in rows:
        if "clean_s" not in r:
            r["clean_s"] = r.get("llm_s")  # records written before the LLM was removed
        by_mode[r.get("stt") or r.get("mode") or "?"].append(r)
    for mode, rs in sorted(by_mode.items()):
        print("== %s: %d dictations   outcomes: %s" % (mode, len(rs), dict(Counter(r["outcome"] for r in rs))))
        done = [r for r in rs if r["outcome"] == "done"]
        if not done:
            print("   (no finished dictations)\n")
            continue
        print("   %-8s %7s %7s %7s" % ("seconds", "median", "p90", "max"))
        for f in FIELDS:
            v = [r[f] for r in done if r.get(f) is not None]
            if v:
                print("   %-8s %7.1f %7.1f %7.1f" % (f.replace("_s", ""), statistics.median(v), pct(v, 0.9), max(v)))
        ratio = [r["total_s"] / r["audio_s"] for r in done if r.get("audio_s")]
        if ratio:
            print("   total/audio: median %.2fx (goal.md target: <= 0.5x)" % statistics.median(ratio))
        print()


if __name__ == "__main__":
    main()
