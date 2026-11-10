"""Loudness measure used to decide whether a clip holds speech. Run with the venv python."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402

from src import recorder  # noqa: E402

RATE = recorder.RATE
GATE = 150  # core.MIN_LEVEL


def clip(seconds, speech_seconds, speech_amp, floor_amp=20, seed=0):
    """`seconds` of low room noise with `speech_seconds` of louder noise in the middle."""
    r = np.random.RandomState(seed)
    audio = r.randn(int(RATE * seconds)) * floor_amp
    start = int(RATE * (seconds - speech_seconds) / 2)
    audio[start:start + int(RATE * speech_seconds)] = r.randn(int(RATE * speech_seconds)) * speech_amp
    return audio.astype("int16")


class SpeechLevel(unittest.TestCase):
    def test_short_phrase_in_a_long_silence_is_not_mistaken_for_silence(self):
        audio = clip(seconds=3.0, speech_seconds=0.35, speech_amp=350)
        self.assertLess(recorder.rms(audio), GATE, "premise: the old whole-clip average is under the gate")
        self.assertGreater(recorder.speech_level(audio), GATE)

    def test_one_word_in_a_six_second_recording_is_still_speech(self):
        audio = clip(seconds=6.0, speech_seconds=0.45, speech_amp=500)   # speech is 7.5% of the clip
        self.assertLess(recorder.rms(audio), GATE, "premise: the old whole-clip average is under the gate")
        self.assertGreater(recorder.speech_level(audio), GATE)

    def test_room_noise_alone_is_below_the_gate(self):
        self.assertLess(recorder.speech_level(clip(3.0, 0.0, 0, floor_amp=40)), GATE)

    def test_digital_silence_is_zero(self):
        self.assertEqual(recorder.speech_level(np.zeros(RATE, dtype="int16")), 0.0)

    def test_very_quiet_constant_whisper_is_below_the_gate(self):
        self.assertLess(recorder.speech_level((np.random.RandomState(1).randn(RATE * 2) * 80).astype("int16")), GATE)

    def test_clip_shorter_than_one_window_does_not_crash(self):
        self.assertGreaterEqual(recorder.speech_level(np.ones(10, dtype="int16") * 500), 0)
        self.assertEqual(recorder.speech_level(np.zeros(0, dtype="int16")), 0.0)

    def test_normal_speech_level_passes(self):
        self.assertGreater(recorder.speech_level(clip(4.0, 3.0, 3000)), GATE)


if __name__ == "__main__":
    unittest.main()
