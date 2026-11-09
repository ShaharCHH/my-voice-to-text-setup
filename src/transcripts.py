#!/usr/bin/env python3
"""Show what Whisper heard next to what the LLM produced (from transcripts.jsonl).

  .venv/bin/python src/transcripts.py            # last 5
  .venv/bin/python src/transcripts.py --last 20
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import metrics  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--last", type=int, default=5)
    args = ap.parse_args()
    path = metrics.log_dir() / "transcripts.jsonl"
    if not path.exists():
        sys.exit("no transcripts yet: %s" % path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in rows[-args.last:]:
        print("[%s] %s\n  HEARD : %s\n  OUTPUT: %s\n" % (r["ts"], r.get("mode") or "?", r["heard"], r["output"]))


if __name__ == "__main__":
    main()
