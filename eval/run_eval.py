#!/usr/bin/env python3
"""Run a speech-to-text setup over recorded clips and score how well tool names survive.

Stdlib only (macOS system Python 3.9 is enough). Two stages, so you can tell whether a term
was lost by Whisper or by the cleanup rules:

  list                         show cases, audio/reference presence, duration
  stt   --setup NAME           audio -> text                   (results/stt-NAME/)
  clean --stt NAME             saved text -> pasted text       (results/NAME__clean/)
  score RUN                    score a results dir (term check, latency, review sheet)

Setups live in settings.json, cases in eval/cases.json (audio path, must_contain terms).
Recordings and results stay on your machine (gitignored): they are your voice.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.backends import clean_text, run_stt  # noqa: E402  (shared with the live app)

EVAL = ROOT / "eval"
CACHE = EVAL / ".cache"
RESULTS = EVAL / "results"
REFERENCE = EVAL / "reference"


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def select_cases(only):
    cases = load_json(EVAL / "cases.json")
    if only:
        cases = [c for c in cases if c["id"] in only]
        if not cases:
            sys.exit("no cases match --only")
    return cases


def wav_for(case):
    """16 kHz mono WAV (what whisper.cpp wants). afconvert ships with macOS."""
    CACHE.mkdir(parents=True, exist_ok=True)
    src = ROOT / case["audio"]
    wav = CACHE / (case["id"] + ".wav")
    if not wav.exists() or wav.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(src), str(wav)],
            check=True,
        )
    return wav


def duration(wav):
    # afinfo, not Python's `wave`: afconvert writes "extensible" WAV headers that `wave` rejects.
    out = subprocess.run(["afinfo", str(wav)], capture_output=True, text=True, check=True).stdout
    m = re.search(r"estimated duration:\s*([\d.]+)", out)
    if not m:
        raise RuntimeError("could not read duration of " + str(wav))
    return float(m.group(1))


def read_meta(run_dir):
    p = run_dir / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def write_meta(run_dir, meta):
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def cmd_list(args):
    cases = select_cases(args.only)
    if not cases:
        print("no cases yet: add recordings to eval/cases.json (see README, Evaluation)")
    for c in cases:
        audio = ROOT / c["audio"]
        dur = "%.1fs" % duration(wav_for(c)) if audio.exists() else "MISSING"
        ref = "ref" if (REFERENCE / (c["id"] + ".txt")).exists() else "no-ref"
        print("%-6s %-14s %-8s %-7s terms=%s" % (c["id"], c.get("category", "?"), dur, ref, c.get("must_contain", [])))
    return 0


def cmd_stt(args):
    cfg = load_json(ROOT / "settings.json")["stt"][args.setup]
    out = RESULTS / ("stt-" + args.setup)
    out.mkdir(parents=True, exist_ok=True)
    meta, failed = {}, 0
    for c in select_cases(args.only):
        try:
            wav = wav_for(c)
            t0 = time.perf_counter()
            text = run_stt(cfg, wav)
            dt = time.perf_counter() - t0
            (out / (c["id"] + ".txt")).write_text(text + "\n", encoding="utf-8")
            meta[c["id"]] = {"stt_seconds": round(dt, 2), "audio_seconds": round(duration(wav), 2)}
            print("%-6s %5.1fs  %s" % (c["id"], dt, text[:90]))
        except Exception as e:  # keep going, report at the end
            failed += 1
            meta[c["id"]] = {"error": str(e)}
            print("%-6s ERROR %s" % (c["id"], e), file=sys.stderr)
    write_meta(out, meta)
    return 1 if failed else 0


def cmd_clean(args):
    cfg = load_json(ROOT / "settings.json")["cleanup"][args.cleanup]
    stt_dir = RESULTS / ("stt-" + args.stt)
    stt_meta = read_meta(stt_dir)
    out = RESULTS / (args.stt + "__clean")
    out.mkdir(parents=True, exist_ok=True)
    meta, failed = {}, 0
    for c in select_cases(args.only):
        src = stt_dir / (c["id"] + ".txt")
        if not src.exists():
            print("%-6s skipped (no stt transcript)" % c["id"], file=sys.stderr)
            failed += 1
            continue
        t0 = time.perf_counter()
        text = clean_text(cfg, src.read_text(encoding="utf-8").strip())
        (out / (c["id"] + ".txt")).write_text(text + "\n", encoding="utf-8")
        m = dict(stt_meta.get(c["id"], {}))
        m["clean_seconds"] = round(time.perf_counter() - t0, 4)
        meta[c["id"]] = m
        print("%-6s %s" % (c["id"], text[:90]))
    write_meta(out, meta)
    return 1 if failed else 0


def term_found(term, text):
    # Word-ish boundaries so `oc` does not match inside "doc"; case-insensitive.
    return re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.I) is not None


def cmd_score(args):
    run = RESULTS / args.run
    if not run.is_dir():
        sys.exit("no such run: " + str(run))
    meta = read_meta(run)
    rows, review = [], []
    for c in select_cases(args.only):
        f = run / (c["id"] + ".txt")
        if not f.exists():
            continue
        out = f.read_text(encoding="utf-8").strip()
        terms = c.get("must_contain", [])
        found = [t for t in terms if term_found(t, out)]
        m = meta.get(c["id"], {})
        total = m.get("stt_seconds", 0) + m.get("clean_seconds", 0)
        dur = m.get("audio_seconds")
        # Latency target: finished within half the length of the speech.
        slow = bool(dur) and total > 0.5 * dur
        rows.append((c["id"], c.get("category", "?"), "%d/%d" % (len(found), len(terms)) if terms else "n/a",
                     "%.1f/%.1f" % (total, dur) if dur else "?", "slow" if slow else "ok"))
        ref = REFERENCE / (c["id"] + ".txt")
        review.append("## %s (%s)\nterms missing: %s\n\nREFERENCE:\n%s\n\nOUTPUT:\n%s\n" % (
            c["id"], c.get("category", "?"), [t for t in terms if t not in found] or "none",
            ref.read_text(encoding="utf-8").strip() if ref.exists() else "(no reference yet)", out))
    lines = ["%-6s %-14s %-6s %-11s %s" % ("id", "category", "terms", "sec/audio", "latency")]
    lines += ["%-6s %-14s %-6s %-11s %s" % r for r in rows]
    print("\n".join(lines))
    (run / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run / "review.md").write_text("\n".join(review), encoding="utf-8")
    print("\nmanual review sheet: " + str(run / "review.md"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("list", cmd_list), ("stt", cmd_stt), ("clean", cmd_clean), ("score", cmd_score)):
        p = sub.add_parser(name)
        p.add_argument("--only", nargs="*", help="case ids to run")
        if name == "stt":
            p.add_argument("--setup", required=True, help="stt setup name from settings.json")
        if name == "clean":
            p.add_argument("--stt", required=True, help="name of a finished stt run")
            p.add_argument("--cleanup", default="glossary", help="cleanup setup from settings.json")
        if name == "score":
            p.add_argument("run", help="results dir name, e.g. stt-turbo-en or turbo-en__clean")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
