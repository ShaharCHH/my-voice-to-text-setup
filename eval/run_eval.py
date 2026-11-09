#!/usr/bin/env python3
"""Run STT / LLM setups over the eval recordings and score the results.

Stdlib only (macOS system Python 3.9 is enough). Stages are separate on purpose,
so you can tell whether a term was lost by Whisper or by the LLM:

  list                         show cases, audio/reference presence, duration
  stt   --setup NAME           audio -> Hebrew/English transcript   (results/stt-NAME/)
  llm   --stt NAME --llm NAME  saved transcript -> English prompt  (results/NAME__LLM/)
  score RUN                    score a results dir (term check, latency, review sheet)
  prompts --prompt P [P..] --llm L [L..]
                               text-only test of prompt x LLM combos on eval/prompt_cases.json

Setups live in eval/setups.json, cases in eval/cases.json.
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
from src.backends import run_stt, run_llm  # noqa: E402  (shared with the live app)
EVAL = ROOT / "eval"
CACHE = EVAL / ".cache"
RESULTS = EVAL / "results"
REFERENCE = EVAL / "reference"
HEBREW = re.compile("[֐-׿]")


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


# ---------- commands ----------

def cmd_list(args):
    for c in select_cases(args.only):
        audio = ROOT / c["audio"]
        dur = "%.1fs" % duration(wav_for(c)) if audio.exists() else "MISSING"
        ref = "ref" if (REFERENCE / (c["id"] + ".txt")).exists() else "no-ref"
        print("%-6s %-14s %-8s %-7s terms=%s" % (
            c["id"], c.get("category", "?"), dur, ref, c.get("must_contain", [])))
    return 0


def cmd_stt(args):
    cfg = load_json(EVAL / "setups.json")["stt"][args.setup]
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


def cmd_llm(args):
    cfg = load_json(EVAL / "setups.json")["llm"][args.llm]
    system = (ROOT / args.prompt).read_text(encoding="utf-8")
    stt_dir = RESULTS / ("stt-" + args.stt)
    stt_meta = read_meta(stt_dir)
    out = RESULTS / (args.stt + "__" + args.llm)
    out.mkdir(parents=True, exist_ok=True)
    meta, failed = {}, 0
    for c in select_cases(args.only):
        src = stt_dir / (c["id"] + ".txt")
        if not src.exists():
            print("%-6s skipped (no stt transcript)" % c["id"], file=sys.stderr)
            failed += 1
            continue
        try:
            t0 = time.perf_counter()
            text = run_llm(cfg, system, src.read_text(encoding="utf-8").strip())
            dt = time.perf_counter() - t0
            (out / (c["id"] + ".txt")).write_text(text + "\n", encoding="utf-8")
            m = dict(stt_meta.get(c["id"], {}))
            m.update({"llm_seconds": round(dt, 2), "prompt": args.prompt})
            meta[c["id"]] = m
            print("%-6s %5.1fs  %s" % (c["id"], dt, text[:90]))
        except Exception as e:
            failed += 1
            meta[c["id"]] = {"error": str(e)}
            print("%-6s ERROR %s" % (c["id"], e), file=sys.stderr)
    write_meta(out, meta)
    return 1 if failed else 0


def term_found(term, text):
    # Word-ish boundaries so `oc` does not match inside "doc"; case-insensitive.
    return re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.I) is not None


# ---------- prompt test set (text in, text out; no audio needed) ----------

FILLER = re.compile(r"(?<![\w])(um|uh|er)(?![\w])", re.I)


def has(spec, text):
    """spec 'a|b' = either alternative. Boundary check only where the term edge is a word char."""
    for alt in spec.split("|"):
        pat = ("(?<!\\w)" if re.match(r"\w", alt) else "") + re.escape(alt) + ("(?!\\w)" if re.search(r"\w$", alt) else "")
        if re.search(pat, text, re.I):
            return True
    return False


def judge(case, out):
    """Return a list of failure reasons (empty list = pass)."""
    fails = []
    text, n_in, n_out = case["input"], len(case["input"].split()), len(out.split())
    if not out.strip():
        return ["empty output"]
    fails += ["missing: " + t for t in case["must_contain"] if not has(t, out)]
    fails += ["forbidden: " + t for t in case.get("must_not_contain", []) if has(t, out)]
    if FILLER.search(out) and not FILLER.search(text):
        fails.append("filler left in")
    if n_out > 1.4 * n_in + 3:
        fails.append("too long (%d words from %d): answered or added?" % (n_out, n_in))
    if n_out < case.get("min_ratio", 0.45) * n_in:  # a self-correction legitimately shrinks a sentence
        fails.append("too short (%d words from %d): details dropped?" % (n_out, n_in))
    if out.startswith("`") and out.endswith("`") and out.count("`") == 2 and n_out > 6:
        fails.append("whole output in backticks")
    return fails


def cmd_prompts(args):
    cases = load_json(EVAL / "prompt_cases.json")
    if args.category:
        cases = [c for c in cases if c["category"] in args.category]
    llms = load_json(EVAL / "setups.json")["llm"]
    out_root = RESULTS / "prompts"
    out_root.mkdir(parents=True, exist_ok=True)
    table = []
    for pf in args.prompt:
        system = (ROOT / pf).read_text(encoding="utf-8")
        for name in args.llm:
            label = Path(pf).stem + "__" + name
            try:
                run_llm(llms[name], system, "warm up")  # load the model first so timings are steady-state
            except Exception as e:
                print("%-22s skipped: %s" % (label, str(e)[:90]), file=sys.stderr)
                continue
            by_cat, fails, secs, rows = {}, [], 0.0, []
            for c in cases:
                t0 = time.perf_counter()
                try:
                    out = run_llm(llms[name], system, c["input"])
                except Exception as e:
                    out = ""
                    print("%s %s ERROR %s" % (label, c["id"], e), file=sys.stderr)
                dt = time.perf_counter() - t0
                secs += dt
                why = judge(c, out)
                ok, tot = by_cat.get(c["category"], (0, 0))
                by_cat[c["category"]] = (ok + (not why), tot + 1)
                if why:
                    fails.append((c["id"], c["input"], out, why))
                rows.append({"id": c["id"], "output": out, "fail": why, "seconds": round(dt, 2)})
            (out_root / (label + ".json")).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
            passed = sum(v[0] for v in by_cat.values())
            table.append((label, passed, len(cases), secs / max(1, len(cases)), by_cat))
            print("%-22s %2d/%d pass   avg %.1fs" % (label, passed, len(cases), secs / max(1, len(cases))))
            if args.verbose:
                for cid, inp, out, why in fails:
                    print("   FAIL %s: %s\n      in : %s\n      out: %s" % (cid, "; ".join(why), inp, out))
    cats = sorted({c["category"] for c in cases})
    print("\n%-22s " % "by category" + " ".join("%-11s" % x[:11] for x in cats))
    for label, passed, total, avg, by_cat in table:
        print("%-22s " % label + " ".join("%-11s" % ("%d/%d" % by_cat[x] if x in by_cat else "-") for x in cats))
    print("\nper-case outputs: " + str(out_root))
    return 0


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
        total = m.get("stt_seconds", 0) + m.get("llm_seconds", 0)
        dur = m.get("audio_seconds")
        # Target from goal.md: under ~5s for a 10s utterance => latency <= 0.5 x audio.
        slow = bool(dur) and total > 0.5 * dur
        rows.append((c["id"], c.get("category", "?"),
                     "%d/%d" % (len(found), len(terms)) if terms else "n/a",
                     "HE" if HEBREW.search(out) else "-",
                     "%.1f/%.1f" % (total, dur) if dur else "?",
                     "slow" if slow else "ok"))
        ref = REFERENCE / (c["id"] + ".txt")
        review.append("## %s (%s)\nterms missing: %s\n\nREFERENCE:\n%s\n\nOUTPUT:\n%s\n" % (
            c["id"], c.get("category", "?"),
            [t for t in terms if t not in found] or "none",
            ref.read_text(encoding="utf-8").strip() if ref.exists() else "(no reference yet)",
            out))
    header = "%-6s %-14s %-6s %-5s %-11s %s" % ("id", "category", "terms", "hebrew", "sec/audio", "latency")
    lines = [header] + ["%-6s %-14s %-6s %-6s %-11s %s" % r for r in rows]
    print("\n".join(lines))
    (run / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (run / "review.md").write_text("\n".join(review), encoding="utf-8")
    print("\nmanual review sheet (meaning, fillers): " + str(run / "review.md"))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("list", cmd_list), ("stt", cmd_stt), ("llm", cmd_llm), ("score", cmd_score),
                     ("prompts", cmd_prompts)):
        p = sub.add_parser(name)
        p.add_argument("--only", nargs="*", help="case ids to run")
        if name == "stt":
            p.add_argument("--setup", required=True)
        if name == "llm":
            p.add_argument("--stt", required=True, help="name of a finished stt run")
            p.add_argument("--llm", required=True)
            p.add_argument("--prompt", default="prompts/v1.md")
        if name == "prompts":
            p.add_argument("--prompt", nargs="+", required=True, help="prompt files, e.g. prompts/v4-en.md")
            p.add_argument("--llm", nargs="+", required=True, help="llm setup names from setups.json")
            p.add_argument("--category", nargs="*", help="only these categories")
            p.add_argument("-v", "--verbose", action="store_true", help="print every failing case")
        if name == "score":
            p.add_argument("run", help="results dir name, e.g. stt-turbo-he or turbo-he__haiku")
        p.set_defaults(fn=fn)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
