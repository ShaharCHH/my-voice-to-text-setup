"""State machine of the dictation core, with a fake microphone, fake Whisper and a fake paste.
Run: python3 -m unittest discover -s tests -v   (needs the venv: .venv/bin/python -m unittest ...)"""
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402
from pynput import keyboard  # noqa: E402

from src import core  # noqa: E402
from src.backends import Cancelled  # noqa: E402

K = keyboard.Key
LOUD = (np.random.RandomState(0).randn(32000) * 3000).astype("int16")  # 2 s of "speech"
SILENT = np.zeros(32000, dtype="int16")


class FakeRecorder:
    def __init__(self, audio):
        self.audio = audio

    def start(self):
        pass

    def stop(self):
        return self.audio


def slow_stt(cfg, wav, cancel=None):
    """~0.8 s of 'work' that notices cancellation, like the real whisper-cli wrapper."""
    for _ in range(16):
        time.sleep(0.05)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
    return "hello pod"


class CoreTests(unittest.TestCase):
    def setUp(self):
        for target, fake in (("run_stt", slow_stt), ("clean_text", lambda cfg, text: "OUT: " + text)):
            patcher = mock.patch.object(core, target, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.pasted, self.metrics = [], []

    def make(self, audio=LOUD, paste_ok=True, recorder=None, paster=None, **kwargs):
        def default_paster(text):
            self.pasted.append(text)
            return paste_ok
        d = core.Dictation({}, {}, K.alt_r, log=lambda m: None, paster=paster or default_paster, sounds=False,
                           recorder_factory=recorder or (lambda: FakeRecorder(audio)), stt_name="test",
                           on_metric=self.metrics.append, **kwargs)
        self.addCleanup(d.cancel)
        return d

    def wait_idle(self, d, timeout=5):
        end = time.time() + timeout
        while d.state != "idle" and time.time() < end:
            time.sleep(0.02)
        time.sleep(0.1)  # let the worker thread record its metric
        self.assertEqual(d.state, "idle")

    def tap(self, d):
        d.on_press(K.alt_r)
        d.on_release(K.alt_r)

    def test_normal_dictation_pastes_once(self):
        d = self.make()
        d.on_press(K.alt_r)
        self.assertEqual(d.state, "recording")
        d.on_press(K.alt_r)  # key auto-repeat while held must be ignored
        d.on_release(K.alt_r)
        self.assertEqual(d.state, "working")
        self.wait_idle(d)
        self.assertEqual(self.pasted, ["OUT: hello pod"])
        self.assertEqual(d.last, "OUT: hello pod")

    def test_esc_while_recording_discards(self):
        d = self.make()
        d.on_press(K.alt_r)
        d.on_press(K.esc)
        self.assertEqual(d.state, "idle")
        d.on_release(K.alt_r)  # releasing afterwards must not start a job
        time.sleep(0.3)
        self.assertEqual((d.state, self.pasted), ("idle", []))

    def test_esc_when_idle_does_nothing(self):
        d = self.make()
        d.on_press(K.esc)
        self.assertEqual(d.state, "idle")

    def test_double_tap_cancels_the_job(self):
        d = self.make()
        self.tap(d)
        self.assertEqual(d.state, "working")
        self.tap(d)
        time.sleep(0.1)
        self.tap(d)  # second tap within the double-tap window
        self.assertEqual(d.state, "idle")
        time.sleep(1.2)  # long enough for the job to have finished had it not been cancelled
        self.assertEqual(self.pasted, [])

    def test_single_tap_while_working_does_not_cancel(self):
        d = self.make()
        self.tap(d)
        self.tap(d)
        self.assertEqual(d.state, "working")
        self.wait_idle(d)
        self.assertEqual(self.pasted, ["OUT: hello pod"])

    def test_cancel_then_dictate_again(self):
        d = self.make()
        self.tap(d)
        d.cancel()
        self.assertEqual(d.state, "idle")
        self.tap(d)
        self.assertEqual(d.state, "working")
        self.wait_idle(d)
        self.assertEqual(self.pasted, ["OUT: hello pod"])  # the old job's cancel flag must not leak

    def test_silence_is_skipped(self):
        d = self.make(audio=SILENT)
        self.tap(d)
        self.wait_idle(d)
        self.assertEqual(self.pasted, [])
        self.assertIn("too quiet", d.message)

    def test_menu_cancel_while_recording(self):
        d = self.make()
        d.on_press(K.alt_r)
        d.cancel()
        self.assertEqual(d.state, "idle")

    def test_microphone_failure_is_reported_and_recoverable(self):
        class BrokenMic(FakeRecorder):
            broken = True

            def start(self):
                if BrokenMic.broken:
                    raise OSError("no input device")
        d = self.make(recorder=lambda: BrokenMic(LOUD))
        d.on_press(K.alt_r)  # must not raise into pynput's listener thread
        self.assertEqual(d.state, "idle")
        self.assertIn("microphone unavailable", d.message)
        d.on_release(K.alt_r)
        BrokenMic.broken = False  # permission granted again
        self.tap(d)
        self.wait_idle(d)
        self.assertEqual(self.pasted, ["OUT: hello pod"])

    def test_paste_crash_does_not_leave_ui_stuck_on_working(self):
        def exploding_paster(text):
            raise RuntimeError("pbcopy exploded")
        d = self.make(paster=exploding_paster)
        self.tap(d)
        self.wait_idle(d)  # fails if the state is left on "working"
        self.assertIn("error", d.message)
        self.assertEqual([m["outcome"] for m in self.metrics], ["error"])

    def test_broken_ui_callback_cannot_kill_the_listener(self):
        d = self.make()
        d.on_change = mock.Mock(side_effect=RuntimeError("UI broke"))
        d.on_press(K.alt_r)       # would raise if the error reached the caller
        d.on_release(K.alt_r)
        self.assertEqual(d.state, "idle")

    def test_time_limit_warns_then_stops_and_is_flagged(self):
        d = self.make(max_seconds=1.4, warn_before=1)   # warning at 0.4 s, stop at 1.4 s
        d.on_press(K.alt_r)
        time.sleep(0.7)
        self.assertEqual(d.state, "recording")
        self.assertIn("left", d.message)                # the warning reached the UI
        time.sleep(1.2)                                 # the limit passes while the key is still held
        self.assertNotEqual(d.state, "recording")
        self.wait_idle(d)
        self.assertEqual(self.pasted, ["OUT: hello pod"])
        self.assertTrue(self.metrics[-1]["capped"])
        self.assertIn("limit", d.message)

    def test_quiet_microphone_is_flagged_but_still_pasted(self):
        quiet = (np.random.RandomState(2).randn(32000) * 300).astype("int16")   # above the gate, below LOW_LEVEL
        d = self.make(audio=quiet)
        self.tap(d)
        self.wait_idle(d)
        self.assertEqual(self.pasted, ["OUT: hello pod"])
        self.assertIn("microphone is quiet", d.message)

    def test_loud_dictation_has_no_quiet_warning(self):
        d = self.make()
        self.tap(d)
        self.wait_idle(d)
        self.assertEqual(d.message, "done")

    def test_normal_dictation_is_not_flagged_as_capped(self):
        d = self.make()
        self.tap(d)
        self.wait_idle(d)
        self.assertFalse(self.metrics[-1]["capped"])
        self.assertEqual(self.metrics[-1]["level"] > 150, True)

    def test_one_metric_per_outcome_and_no_spoken_text(self):
        d = self.make()
        self.tap(d)
        self.wait_idle(d)                       # done
        self.tap(d)
        d.cancel()
        time.sleep(0.3)                         # cancelled
        d2 = self.make(audio=SILENT)
        self.tap(d2)
        self.wait_idle(d2)                      # skipped
        d3 = self.make(paste_ok=False)
        self.tap(d3)
        self.wait_idle(d3)                      # paste_failed
        self.assertEqual([m["outcome"] for m in self.metrics], ["done", "cancelled", "skipped", "paste_failed"])
        self.assertNotIn("hello", str(self.metrics))
        self.assertTrue(all(m["total_s"] is not None for m in self.metrics))


if __name__ == "__main__":
    unittest.main()
