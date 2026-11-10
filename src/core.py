"""Dictation core: hotkey state machine + record -> STT -> LLM -> paste.

No UI in here, so the CLI (app.py) and the menubar app (menubar.py) share it.

States: idle -> recording -> working -> idle
  hold hotkey            idle -> recording
  release hotkey         recording -> working (transcribe, translate, paste)
  Esc while recording    recording -> idle   (recording thrown away)
  double-tap hotkey      working -> idle     (job cancelled, nothing pasted)
"""
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from src import metrics, recorder
from src.backends import Cancelled, run_llm, run_stt

MIN_SECONDS = 0.5     # shorter than this is an accidental tap
MIN_RMS = 150.0       # quieter than this is silence (int16 scale); Whisper would hallucinate
MAX_SECONDS = 60      # safety cap: stop recording if the key-release is ever missed
DOUBLE_TAP = 0.4      # two hotkey presses within this many seconds cancel a running job

IDLE, RECORDING, WORKING = "idle", "recording", "working"


def sound(name):
    """Audible feedback, non-blocking. Tink = recording, Pop = done, Basso = error/skipped/cancelled."""
    subprocess.Popen(["afplay", "/System/Library/Sounds/%s.aiff" % name],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def check_accessibility():
    """Posting Cmd+V needs this process's host app trusted for Accessibility. macOS drops the
    keystrokes silently otherwise, so ask up front instead of failing quietly."""
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except ImportError:
        return True  # cannot check; do not block


V_KEYCODE = 9  # the physical "V" key; Cmd+V is matched by key position, so no layout lookup is needed


def press_cmd_v():
    """Post Cmd+V through Quartz. pynput's Controller() looks up the keyboard layout via a macOS
    call (TSMGetInputSourceProperty) that must run on the main thread; from our worker thread it
    killed the whole app (SIGTRAP, see HANDOFF). A raw key code avoids that call entirely."""
    from Quartz import (CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
                        kCGEventFlagMaskCommand, kCGHIDEventTap)
    for key_down in (True, False):
        event = CGEventCreateKeyboardEvent(None, V_KEYCODE, key_down)
        CGEventSetFlags(event, kCGEventFlagMaskCommand)
        CGEventPost(kCGHIDEventTap, event)


def paste(text):
    """Copy text and press Cmd+V in the frontmost app. On success restore the old clipboard
    (text only); on failure leave the result on the clipboard to paste by hand."""
    env = {"LC_CTYPE": "UTF-8"}  # so pbcopy/pbpaste handle non-ASCII correctly
    old = subprocess.run(["pbpaste"], capture_output=True, env=env).stdout
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), env=env, check=True)
    try:
        press_cmd_v()
    except Exception as e:
        print("paste failed (%s); result is on your clipboard, press Cmd+V yourself" % e, file=sys.stderr)
        return False
    time.sleep(0.3)  # let the target app read the clipboard before we put the old one back
    subprocess.run(["pbcopy"], input=old, env=env)
    return True


def process_wav(wav, stt_cfg, llm_cfg, system, cancel=None, log=print):
    """wav -> (heard, output, stt_seconds, llm_seconds). Raises Cancelled if cancel is set."""
    t0 = time.perf_counter()
    heard = run_stt(stt_cfg, wav, cancel)
    t1 = time.perf_counter()
    if not heard.strip():
        return heard, "", t1 - t0, 0.0
    log("heard (%.1fs): %s\ntranslating..." % (t1 - t0, heard))
    out = run_llm(llm_cfg, system, heard)
    if cancel is not None and cancel.is_set():
        raise Cancelled()  # Ollama cannot be interrupted; we just drop its answer
    return heard, out, t1 - t0, time.perf_counter() - t1


class Dictation:
    def __init__(self, stt_cfg, llm_cfg, system, hotkey, on_change=None, log=print, debug=None,
                 recorder_factory=recorder.Recorder, paster=paste, sounds=True,
                 mode="", on_metric=metrics.record, on_text=None):
        """hotkey / esc are pynput Key objects. on_change(state, last) is called on any change.
        log gets status lines without spoken text; debug (optional) also gets the transcripts.
        on_metric(dict) gets one record per dictation (timings, sizes, outcome; no text).
        on_text(heard, output, mode), if given, gets the transcript pair of each finished job."""
        from pynput import keyboard
        self.stt_cfg, self.llm_cfg, self.system = stt_cfg, llm_cfg, system
        self.hotkey, self.esc = hotkey, keyboard.Key.esc
        self.on_change, self.log = on_change, log
        self.debug = debug or (lambda m: None)
        self.mode, self.on_metric, self.on_text = mode, on_metric, on_text
        self.paster, self.sounds = paster, sounds
        self.state = IDLE
        self.last = ""                 # last result text
        self.message = ""              # short status for the UI
        self._rec = recorder_factory()
        self._down = False             # hotkey physically held (filters key auto-repeat)
        self._timer = None
        self._cancel = threading.Event()
        self._last_tap = 0.0
        self._lock = threading.Lock()

    def set_mode(self, stt_cfg, llm_cfg, system, mode=""):
        """Switch models/prompt. A job already running keeps what it started with."""
        self.stt_cfg, self.llm_cfg, self.system, self.mode = stt_cfg, llm_cfg, system, mode

    # ---- helpers ----
    def _set(self, state, message=""):
        self.state, self.message = state, message
        if self.on_change:
            self.on_change(state, self.last)

    def _sound(self, name):
        if self.sounds:
            sound(name)

    # ---- key events (call from the pynput listener thread) ----
    def on_press(self, key):
        with self._lock:
            if key == self.esc and self.state == RECORDING:
                self._abort_recording()
            elif key == self.hotkey and not self._down:  # auto-repeat re-fires press while held
                self._down = True
                if self.state == IDLE:
                    self._start_recording()
                elif self.state == WORKING:
                    now = time.monotonic()
                    if now - self._last_tap < DOUBLE_TAP:
                        self._cancel_work()
                    self._last_tap = now

    def on_release(self, key):
        with self._lock:
            if key != self.hotkey:
                return
            self._down = False
            if self.state == RECORDING:
                self._stop_and_process()

    # ---- transitions (lock held) ----
    def _start_recording(self):
        self._cancel = threading.Event()
        self._rec.start()
        self._sound("Tink")
        self._set(RECORDING, "recording...")
        self.log("recording... (release to stop, Esc to discard)")
        self._timer = threading.Timer(MAX_SECONDS, lambda: self.on_release(self.hotkey))
        self._timer.daemon = True  # else quitting mid-recording would wait up to MAX_SECONDS
        self._timer.start()

    def _stop_timer(self):
        if self._timer:
            self._timer.cancel()  # else a stale timer could cut off the next recording
            self._timer = None

    def _abort_recording(self):
        self._stop_timer()
        self._rec.stop()
        self._sound("Basso")
        self.log("discarded")
        self._set(IDLE, "discarded")

    def _stop_and_process(self):
        self._stop_timer()
        audio = self._rec.stop()
        self._set(WORKING, "transcribing...")
        # off the listener thread, or the keyboard hook stalls while we work
        threading.Thread(target=self._work, args=(audio, self._cancel), daemon=True).start()

    def _cancel_work(self):
        self._cancel.set()
        self._sound("Basso")
        self.log("cancelled")
        self._set(IDLE, "cancelled")

    def cancel(self):
        """Menu 'Cancel' item: cancel whatever is running."""
        with self._lock:
            if self.state == WORKING:
                self._cancel_work()
            elif self.state == RECORDING:
                self._abort_recording()

    # ---- the job ----
    def _work(self, audio, cancel):
        t_start = time.perf_counter()          # ~ the moment the key was released
        dur, level = recorder.seconds(audio), recorder.rms(audio)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "mode": self.mode, "audio_s": round(dur, 2),
               "stt_s": None, "llm_s": None, "paste_s": None, "total_s": None,
               "chars_in": None, "chars_out": None, "outcome": "error"}
        try:
            if dur < MIN_SECONDS or level < MIN_RMS:
                rec["outcome"] = "skipped"
                self.log("skipped (%.1fs, level %.0f)" % (dur, level))
                self._sound("Basso")
                self._finish(cancel, IDLE, "too short or silent")
                return
            self.log("recorded %.1fs (level %.0f), transcribing..." % (dur, level))
            stt_cfg, llm_cfg, system = self.stt_cfg, self.llm_cfg, self.system  # snapshot
            try:
                with tempfile.TemporaryDirectory() as d:
                    wav = Path(d) / "clip.wav"
                    recorder.save_wav(audio, wav)
                    heard, out, ts, tl = process_wav(wav, stt_cfg, llm_cfg, system, cancel, self.debug)
            except Cancelled:
                rec["outcome"] = "cancelled"
                return  # state was already reset by whoever cancelled
            except Exception as e:
                self.log("ERROR: %s" % e)
                self._sound("Basso")
                self._finish(cancel, IDLE, "error: %s" % str(e)[:60])
                return
            rec.update(stt_s=round(ts, 2), llm_s=round(tl, 2), chars_in=len(heard), chars_out=len(out))
            if cancel.is_set():
                rec["outcome"] = "cancelled"
                return
            self.debug("heard : %s\noutput: %s" % (heard, out))
            if self.on_text and out:
                self.on_text(heard, out, self.mode)
            if not out:
                rec["outcome"] = "empty"
                self._finish(cancel, IDLE, "nothing heard")
                return
            self.last = out
            t_paste = time.perf_counter()
            ok = self.paster(out)
            rec["paste_s"] = round(time.perf_counter() - t_paste, 2)
            rec["outcome"] = "done" if ok else "paste_failed"
            self._sound("Pop" if ok else "Basso")
            self.log("done: stt %.1fs, llm %.1fs, audio %.1fs" % (ts, tl, dur))
            self._finish(cancel, IDLE, "done" if ok else "paste failed (copied to clipboard)")
        finally:
            rec["total_s"] = round(time.perf_counter() - t_start, 2)
            self.on_metric(rec)

    def _finish(self, cancel, state, message):
        with self._lock:
            if cancel.is_set():  # cancelled meanwhile; do not clobber the newer state
                return
            self._set(state, message)
