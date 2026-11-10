#!/usr/bin/env python3
"""Compare speech-to-text setups on recordings of known sentences.

You read eval/sentences.json aloud once (eval/record_cases.py), then:

  list                          which sentences have a recording
  compare --stt A B C ...       run each setup over the recordings and score it

Scores: word error rate (WER, lower is better) on Whisper's raw text and on the text that
would be pasted (after the glossary and spoken edits), and how many of the tool names
survived. The first setup named is the baseline the others are compared against.

Stdlib only. Recordings (your voice) and results stay on your machine, gitignored.
"""
import argparse
import hashlib
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.backends import clean_text, read_pcm16, run_stt  # noqa: E402  (shared with the live app)

EVAL = ROOT / "eval"
RESULTS = EVAL / "results" / "compare"
DEFAULT_AUDIO = ROOT / "recordings"


# ---------- scoring ----------

def tokens(text):
    """Lowercase words for comparison. Keeps 'to-do', '4.14', "don't" as one word; '1,000' becomes '1000'."""
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text.lower())
    return re.findall(r"[a-z0-9]+(?:[-.'][a-z0-9]+)*", text)


def edit_distance(ref, hyp):
    """Word-level Levenshtein distance (substitutions + insertions + deletions)."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def term_found(spec, text):
    """spec 'a|b' = either spelling. Case-insensitive, whole words where the edge is a word character."""
    for alt in spec.split("|"):
        pat = ("(?<![\\w])" if re.match(r"\w", alt) else "") + re.escape(alt) + ("(?![\\w])" if re.search(r"\w$", alt) else "")
        if re.search(pat, text, re.I):
            return True
    return False


# ---------- data ----------

def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_cases(audio_dir, categories=None, only=None):
    cases = load_json(EVAL / "sentences.json")
    if categories:
        cases = [c for c in cases if c["category"] in categories]
    if only:
        cases = [c for c in cases if c["id"] in only]
    for c in cases:
        c["wav"] = Path(audio_dir) / (c["id"] + ".wav")
    return cases


def audio_seconds(wav):
    return len(read_pcm16(wav)) / 16000


# ---------- commands ----------

def cmd_list(args):
    cases = load_cases(args.audio_dir)
    have = [c for c in cases if c["wav"].exists()]
    for c in cases:
        mark = "%5.1fs" % audio_seconds(c["wav"]) if c["wav"].exists() else "  --  "
        print("%-7s %-11s %s  %s" % (c["id"], c["category"], mark, c["say"][:70]))
    print("\n%d of %d recorded in %s" % (len(have), len(cases), args.audio_dir))
    return 0


def score_setup(name, cfg, cleanup, cases, fresh):
    """Run one speech setup over the cases; cached per (setup config, audio file)."""
    rows = []
    cfg_key = hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:10]
    for c in cases:
        cache = RESULTS / c["wav"].parent.name / name / (c["id"] + ".json")
        key = "%s-%s" % (cfg_key, int(c["wav"].stat().st_mtime))
        got = None
        if cache.exists() and not fresh:
            got = json.loads(cache.read_text(encoding="utf-8"))
            got = got if got.get("key") == key else None
        if got is None:
            t0 = time.perf_counter()
            raw = run_stt(cfg, c["wav"])
            got = {"key": key, "raw": raw, "secs": round(time.perf_counter() - t0, 3)}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
        final = clean_text(cleanup, got["raw"])
        ref = tokens(c["expected"])
        rows.append({"case": c, "raw": got["raw"], "final": final, "secs": got["secs"],
                     "audio": audio_seconds(c["wav"]), "n": len(ref),
                     "edits_raw": edit_distance(ref, tokens(got["raw"])),
                     "edits_final": edit_distance(ref, tokens(final)),
                     "terms_raw": sum(term_found(t, got["raw"]) for t in c["terms"]),
                     "terms_final": sum(term_found(t, final) for t in c["terms"]),
                     "terms": len(c["terms"])})
    return rows


def pct(num, den):
    return 100.0 * num / den if den else 0.0


def cmd_compare(args):
    settings = load_json(ROOT / "settings.json")
    cleanup = settings["cleanup"][args.cleanup]
    cases = [c for c in load_cases(args.audio_dir, args.category) if c["wav"].exists()]
    if not cases:
        sys.exit("no recordings in %s: run  .venv/bin/python eval/record_cases.py" % args.audio_dir)
    for name in args.stt:
        if name not in settings["stt"]:
            sys.exit("unknown stt setup %r (have: %s)" % (name, ", ".join(settings["stt"])))
    results = {}
    for name in args.stt:
        print("running %-18s on %d clips ..." % (name, len(cases)), file=sys.stderr, flush=True)
        results[name] = score_setup(name, settings["stt"][name], cleanup, cases, args.fresh)

    plain = lambda rows: [r for r in rows if r["case"]["category"] != "edits"]  # noqa: E731
    print("\n%d clips from %s   (raw = Whisper only, final = what would be pasted)\n" % (len(cases), args.audio_dir))
    print("%-18s %9s %10s %11s %12s %8s %6s" % ("setup", "WER raw*", "WER final", "terms raw*", "terms final", "s/clip", "x real"))
    for name, rows in results.items():
        p = plain(rows)
        print("%-18s %8.1f%% %9.1f%% %5d/%-5d %6d/%-5d %8.2f %6.2f" % (
            name, pct(sum(r["edits_raw"] for r in p), sum(r["n"] for r in p)),
            pct(sum(r["edits_final"] for r in rows), sum(r["n"] for r in rows)),
            sum(r["terms_raw"] for r in p), sum(r["terms"] for r in p),
            sum(r["terms_final"] for r in rows), sum(r["terms"] for r in rows),
            statistics.mean(r["secs"] for r in rows), sum(r["secs"] for r in rows) / max(1e-9, sum(r["audio"] for r in rows))))
    print("* raw excludes the spoken-edit clips, which are meant to change. 'x real' = processing time / audio time.")

    cats = sorted({c["category"] for c in cases})
    print("\nWER final by category:")
    print("%-18s " % "setup" + " ".join("%-10s" % c[:10] for c in cats))
    for name, rows in results.items():
        cells = []
        for cat in cats:
            sub = [r for r in rows if r["case"]["category"] == cat]
            cells.append("%-10s" % ("%.0f%%" % pct(sum(r["edits_final"] for r in sub), sum(r["n"] for r in sub))))
        print("%-18s " % name + " ".join(cells))

    base = args.stt[0]
    if len(args.stt) > 1:
        print("\nclip by clip against the baseline %s (final text):" % base)
        for name in args.stt[1:]:
            better = worse = same = 0
            for a, b in zip(results[base], results[name]):
                better += b["edits_final"] < a["edits_final"]
                worse += b["edits_final"] > a["edits_final"]
                same += b["edits_final"] == a["edits_final"]
            print("  %-18s better on %2d, worse on %2d, identical on %2d" % (name, better, worse, same))
    n_err = sum(r["edits_final"] for r in results[base])
    print("\nwith %d clips the baseline makes %d word errors in total; differences of a few errors are noise,"
          " so trust a gap only if clip-by-clip wins clearly outnumber losses." % (len(cases), n_err))

    if args.verbose:
        for name, rows in results.items():
            print("\n== %s: clips with errors in the final text" % name)
            for r in sorted(rows, key=lambda r: -r["edits_final"]):
                if r["edits_final"]:
                    print("  %s (%d errors)\n     expected: %s\n     pasted  : %s" % (
                        r["case"]["id"], r["edits_final"], r["case"]["expected"], r["final"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audio-dir", default=str(DEFAULT_AUDIO), help="folder with <id>.wav recordings")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    p = sub.add_parser("compare")
    p.add_argument("--stt", nargs="+", required=True, help="setups from settings.json; the first is the baseline")
    p.add_argument("--cleanup", default="glossary", help="cleanup setup from settings.json")
    p.add_argument("--category", nargs="*", help="only these categories")
    p.add_argument("--fresh", action="store_true", help="ignore cached Whisper results")
    p.add_argument("-v", "--verbose", action="store_true", help="show every clip that has errors")
    p.set_defaults(fn=cmd_compare)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
