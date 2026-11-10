"""Microphone capture to a 16 kHz mono WAV (what whisper.cpp wants)."""
import wave

import numpy as np
import sounddevice as sd

RATE = 16000


class Recorder:
    def __init__(self, device=None):
        self.device = device  # None = system default input
        self._chunks = []
        self._stream = None

    def _callback(self, indata, frames, time_info, status):
        self._chunks.append(indata.copy())

    def start(self):
        self._chunks = []
        self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16",
                                      device=self.device, callback=self._callback)
        self._stream.start()

    def stop(self):
        """Stop and return the audio as an int16 numpy array (empty if nothing captured)."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._chunks:
            return np.zeros(0, dtype="int16")
        return np.concatenate(self._chunks)[:, 0]


def seconds(audio):
    return len(audio) / RATE


def rms(audio):
    """Loudness, 0..32767. Used to skip silence: Whisper invents text on silent clips."""
    return float(np.sqrt(np.mean(audio.astype("float32") ** 2))) if len(audio) else 0.0


def save_wav(audio, path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio.tobytes())


def speech_level(audio, window_ms=50):
    """How loud the speech is, 0..32767: the 95th-percentile loudness of 50 ms windows.

    The whole-clip average (rms) drops when there are pauses, so a short phrase in a long
    silence looked 'silent' and could be skipped. The loud windows are the speech, so use them.
    """
    n = int(RATE * window_ms / 1000)
    if len(audio) < n:
        return rms(audio)
    windows = audio[: len(audio) // n * n].astype("float32").reshape(-1, n)
    return float(np.percentile(np.sqrt((windows ** 2).mean(axis=1)), 95))
