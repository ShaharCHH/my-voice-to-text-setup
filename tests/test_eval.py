"""The accuracy test's own machinery: scoring, the sentence set, recording flow, normalisation.
If the scorer were wrong, every conclusion drawn from it would be too. Run with the venv python."""
import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402

from eval import record_cases, run_eval  # noqa: E402
from src import backends, recorder  # noqa: E402


class Scoring(unittest.TestCase):
    def test_tokens_keep_hyphenated_and_version_words_whole(self):
        self.assertEqual(run_eval.tokens("Upgrade to-do 4.14, don't go"), ["upgrade", "to-do", "4.14", "don't", "go"])

    def test_thousands_separator_is_not_a_word_break(self):
        self.assertEqual(run_eval.tokens("above 1,000 now"), run_eval.tokens("above 1000 now"))

    def test_edit_distance_known_cases(self):
        d = run_eval.edit_distance
        self.assertEqual(d(["a", "b", "c"], ["a", "b", "c"]), 0)
        self.assertEqual(d(["a", "b", "c"], ["a", "x", "c"]), 1)        # substitution
        self.assertEqual(d(["a", "b", "c"], ["a", "c"]), 1)             # deletion
        self.assertEqual(d(["a", "c"], ["a", "b", "c"]), 1)             # insertion
        self.assertEqual(d([], ["a", "b"]), 2)
        self.assertEqual(d(["a", "b"], []), 2)
        self.assertEqual(d("kitten sitting on a mat".split(), "sitting kitten on mat".split()), 3)

    def test_term_matching(self):
        f = run_eval.term_found
        self.assertTrue(f("kubectl", "Run kubectl get pods"))
        self.assertTrue(f("KUBECTL", "run kubectl"))
        self.assertFalse(f("oc", "the doc says so"))                    # whole words only
        self.assertTrue(f("8 gigabytes|8 GB", "give it 8 GB of memory"))
        self.assertTrue(f("1000|1,000", "above 1,000 now"))
        self.assertFalse(f("Kube Deck", "the cube deck project"))


class SentenceSet(unittest.TestCase):
    def setUp(self):
        self.cases = json.loads((ROOT / "eval" / "sentences.json").read_text(encoding="utf-8"))

    def test_ids_are_unique_and_fields_present(self):
        ids = [c["id"] for c in self.cases]
        self.assertEqual(len(ids), len(set(ids)))
        for c in self.cases:
            for field in ("id", "category", "say", "expected", "terms"):
                self.assertIn(field, c)

    def test_every_term_can_be_satisfied_by_the_expected_text(self):
        for c in self.cases:
            for t in c["terms"]:
                self.assertTrue(run_eval.term_found(t, c["expected"]), "%s: %r not in its own expected text" % (c["id"], t))

    def test_edit_cases_really_change_the_text_and_the_others_do_not(self):
        for c in self.cases:
            if c["category"] == "edits":
                self.assertNotEqual(c["say"], c["expected"])
            else:
                self.assertEqual(c["say"], c["expected"])

    def test_expected_text_of_edit_cases_matches_what_the_app_would_produce(self):
        cleanup = {"kind": "glossary", "rules": "glossary/en.json"}
        for c in self.cases:
            if c["category"] == "edits":
                self.assertEqual(backends.clean_text(cleanup, c["say"]), c["expected"], c["id"])


def write_wav(path, samples):
    recorder.save_wav(np.asarray(samples, dtype="int16"), path)


class Normalisation(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def peak(self, path):
        return int(np.abs(np.array(backends.read_pcm16(path))).max())

    def test_quiet_audio_is_amplified_without_clipping(self):
        p = os.path.join(self.dir, "q.wav")
        write_wav(p, np.random.RandomState(0).randn(32000) * 300)
        out, temporary = backends.normalized_copy(p)
        self.addCleanup(lambda: os.path.exists(out) and temporary and os.unlink(out))
        self.assertTrue(temporary)
        self.assertGreater(self.peak(out), 8 * self.peak(p))
        self.assertLess(self.peak(out), 32767)

    def test_loud_and_silent_audio_are_left_alone(self):
        loud, silent = os.path.join(self.dir, "l.wav"), os.path.join(self.dir, "s.wav")
        write_wav(loud, np.random.RandomState(0).randn(32000) * 6000)
        write_wav(silent, np.zeros(32000))
        for p in (loud, silent):
            out, temporary = backends.normalized_copy(p)
            self.assertEqual((out, temporary), (p, False))

    def test_gain_is_capped_so_noise_is_not_blown_up(self):
        p = os.path.join(self.dir, "n.wav")
        write_wav(p, np.random.RandomState(0).randn(32000) * 20)
        out, temporary = backends.normalized_copy(p)
        self.addCleanup(lambda: temporary and os.unlink(out))
        self.assertLessEqual(self.peak(out), 31 * self.peak(p))

    def test_one_loud_thump_does_not_hold_the_gain_down(self):
        samples = np.random.RandomState(0).randn(32000) * 300
        samples[100] = 30000
        p = os.path.join(self.dir, "t.wav")
        write_wav(p, samples)
        out, temporary = backends.normalized_copy(p)
        self.addCleanup(lambda: temporary and os.unlink(out))
        body = np.abs(np.array(backends.read_pcm16(out)))[1000:]
        self.assertGreater(body.max(), 8000)

    def test_output_is_a_valid_wav(self):
        p = os.path.join(self.dir, "v.wav")
        write_wav(p, np.random.RandomState(0).randn(16000) * 300)
        out, temporary = backends.normalized_copy(p)
        self.addCleanup(lambda: temporary and os.unlink(out))
        with wave.open(out) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()), (1, 2, 16000, 16000))


class RecordingFlow(unittest.TestCase):
    class FakeMic:
        def __init__(self, audios):
            self.audios = list(audios)

        def start(self):
            pass

        def stop(self):
            return self.audios.pop(0)

    def run_flow(self, audios, answers, cases):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        it = iter(answers)
        saved = record_cases.record_all(cases, d, make_recorder=lambda: self.FakeMic(audios),
                                        ask=lambda prompt: next(it), say=lambda m: None)
        return saved, d

    LOUD = (np.random.RandomState(0).randn(32000) * 3000).astype("int16")
    CASES = [{"id": "x01", "category": "c", "say": "one"}, {"id": "x02", "category": "c", "say": "two"}]

    def test_keep_saves_a_wav_per_sentence(self):
        saved, d = self.run_flow([self.LOUD, self.LOUD], ["", "", "", "", "", ""], self.CASES)
        self.assertEqual(saved, 2)
        self.assertTrue((d / "x01.wav").exists() and (d / "x02.wav").exists())

    def test_silence_is_rejected_and_asked_again(self):
        silent = np.zeros(32000, dtype="int16")
        saved, d = self.run_flow([silent, self.LOUD], ["", "", "", "", ""], self.CASES[:1])
        self.assertEqual(saved, 1)

    def test_redo_discards_the_first_take_and_skip_saves_nothing(self):
        saved, d = self.run_flow([self.LOUD, self.LOUD, self.LOUD], ["", "", "r", "", "", "", "", "", "s"], self.CASES)
        self.assertEqual(saved, 1)
        self.assertTrue((d / "x01.wav").exists())
        self.assertFalse((d / "x02.wav").exists())

    def test_quit_stops_and_keeps_earlier_recordings(self):
        saved, d = self.run_flow([self.LOUD, self.LOUD], ["", "", "", "", "", "q"], self.CASES)
        self.assertEqual(saved, 1)
        self.assertTrue((d / "x01.wav").exists())
        self.assertFalse((d / "x02.wav").exists())


class CompareEndToEnd(unittest.TestCase):
    """Runs the real compare command with the fake speech setup, to prove the plumbing and the cache."""

    def test_compare_prints_a_table_and_reuses_its_cache(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        for cid in ("pla01", "pla02", "edi01"):
            write_wav(d / (cid + ".wav"), np.random.RandomState(1).randn(16000) * 2000)
        args = argparse.Namespace(audio_dir=str(d), stt=["fake", "fake"], cleanup="glossary",
                                  category=None, fresh=False, verbose=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_eval.cmd_compare(args), 0)
        text = out.getvalue()
        self.assertIn("WER final", text)
        self.assertIn("3 clips", text)
        self.assertIn("identical on  3", text)          # same setup against itself: no clip differs
        cache = list((run_eval.RESULTS / d.name).rglob("*.json"))
        self.addCleanup(lambda: __import__("shutil").rmtree(run_eval.RESULTS / d.name, ignore_errors=True))
        self.assertEqual(len(cache), 3)

    def test_unknown_setup_name_is_a_clear_error(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        write_wav(d / "pla01.wav", np.zeros(16000) + 1000)
        args = argparse.Namespace(audio_dir=str(d), stt=["nope"], cleanup="glossary", category=None, fresh=False, verbose=False)
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stderr(io.StringIO()):
            run_eval.cmd_compare(args)
        self.assertIn("unknown stt setup", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
