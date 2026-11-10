"""Dictation core: hotkey state machine + record -> speech-to-text -> cleanup -> paste.

No UI in here, so the CLI (app.py) and the menubar app (menubar.py) share it.

States: idle -> recording -> working -> idle
  hold hotkey            idle -> recording
  release hotkey         recording -> working (transcribe, clean up, paste)
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
from src.backends import Cancelled, clean_text, run_stt

MIN_SECONDS = 0.5     # shorter than this is an accidental tap
MIN_LEVEL = 150.0     # speech quieter than this (int16 scale, see recorder.speech_level) is skipped; Whisper hallucinates on silence
LOW_LEVEL = 450.0     # pastes fine, but tell the user the microphone is quiet (accuracy suffers well before the gate)
MAX_SECONDS = 90      # recording stops by itself here (guards a missed key release)
WARN_BEFORE = 10      # ...and a warning sound plays this many seconds earlier
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


def process_wav(wav, stt_cfg, cleanup_cfg, cancel=None, log=print):
    """wav -> (heard, output, stt_seconds, cleanup_seconds). Raises Cancelled if cancel is set."""
    t0 = time.perf_counter()
    heard = run_stt(stt_cfg, wav, cancel)
    t1 = time.perf_counter()
    if not heard.strip():
        return heard, "", t1 - t0, 0.0
    log("heard (%.1fs): %s" % (t1 - t0, heard))
    out = clean_text(cleanup_cfg, heard)
    return heard, out, t1 - t0, time.perf_counter() - t1


class Dictation:
    def __init__(self, stt_cfg, cleanup_cfg, hotkey, on_change=None, log=print, debug=None,
                 recorder_factory=recorder.Recorder, paster=paste, sounds=True,
                 stt_name="", on_metric=metrics.record, on_text=None,
                 max_seconds=MAX_SECONDS, warn_before=WARN_BEFORE):
        """hotkey / esc are pynput Key objects. on_change(state, last) is called on any change.
        log gets status lines without spoken text; debug (optional) also gets the transcripts.
        on_metric(dict) gets one record per dictation (timings, sizes, outcome; no text).
        on_text(heard, output, stt_name), if given, gets the transcript pair of each finished job."""
        from pynput import keyboard
        self.stt_cfg, self.cleanup_cfg = stt_cfg, cleanup_cfg
        self.hotkey, self.esc = hotkey, keyboard.Key.esc
        self.on_change, self.log = on_change, log
        self.debug = debug or (lambda m: None)
        self.stt_name, self.on_metric, self.on_text = stt_name, on_metric, on_text
        self.paster, self.sounds = paster, sounds
        self.state = IDLE
        self.last = ""                 # last result text
        self.message = ""              # short status for the UI
        self._rec = recorder_factory()
        self._down = False             # hotkey physically held (filters key auto-repeat)
        self.max_seconds, self.warn_before = max_seconds, warn_before
        self._timer = self._warn_timer = None
        self._capped = False           # the current recording was stopped by the time limit
        self._cancel = threading.Event()
        self._last_tap = 0.0
        self._lock = threading.Lock()

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
        try:
            self._on_press(key)
        except Exception as e:
            self._recover("key press", e)

    def on_release(self, key):
        try:
            self._on_release(key)
        except Exception as e:
            self._recover("key release", e)

    def _recover(self, where, error):
        """An exception escaping into pynput's listener thread would end that thread, and with it
        the hotkey, silently. Log it, reset to idle and carry on."""
        try:
            self.log("ERROR in %s: %r" % (where, error))
            self._sound("Basso")
            with self._lock:
                self._down = False
                self._stop_timer()
                self.state, self.message = IDLE, "error: %s" % str(error)[:60]  # not _set(): its UI callback may be what broke
        except Exception:
            pass  # last line of defence: never raise into the listener thread

    def _on_press(self, key):
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

    def _on_release(self, key):
        with self._lock:
            if key != self.hotkey:
                return
            self._down = False
            if self.state == RECORDING:
                self._stop_and_process()

    # ---- transitions (lock held) ----
    def _start_recording(self):
        self._cancel = threading.Event()
        self._capped = False
        try:
            self._rec.start()
        except Exception as e:  # e.g. microphone permission revoked, or no input device
            self.log("microphone error: %r" % e)
            self._sound("Basso")
            self._set(IDLE, "microphone unavailable: %s" % str(e)[:50])
            return
        self._sound("Tink")
        self._set(RECORDING, "recording...")
        self.log("recording... (release to stop, Esc to discard)")
        # daemon timers: else quitting mid-recording would wait for them
        self._timer = threading.Timer(self.max_seconds, self._cap)
        self._warn_timer = threading.Timer(max(0.0, self.max_seconds - self.warn_before), self._warn)
        for t in (self._timer, self._warn_timer):
            t.daemon = True
            t.start()

    def _warn(self):
        with self._lock:
            if self.state == RECORDING:
                self._sound("Funk")
                self._set(RECORDING, "%d s left, then it stops" % round(self.warn_before))

    def _cap(self):
        """Time limit reached: stop as if the key had been released, and remember that we did."""
        self._capped = True
        self.log("time limit (%d s) reached, stopping" % self.max_seconds)
        self.on_release(self.hotkey)

    def _stop_timer(self):
        for t in (self._timer, self._warn_timer):
            if t:
                t.cancel()  # else a stale timer could cut off the next recording
        self._timer = self._warn_timer = None

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
        threading.Thread(target=self._work, args=(audio, self._cancel, self._capped), daemon=True).start()

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
    def _work(self, audio, cancel, capped=False):
        t_start = time.perf_counter()          # ~ the moment the key was released
        dur, level = recorder.seconds(audio), recorder.speech_level(audio)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "stt": self.stt_name, "audio_s": round(dur, 2),
               "stt_s": None, "clean_s": None, "paste_s": None, "total_s": None,
               "chars_in": None, "chars_out": None, "level": round(level), "capped": capped,
               "outcome": "error"}
        try:
            if dur < MIN_SECONDS or level < MIN_LEVEL:
                rec["outcome"] = "skipped"
                why = ("too short (%.1f s)" % dur if dur < MIN_SECONDS
                       else "too quiet (level %.0f): speak up or check the microphone" % level)
                self.log("skipped: " + why)
                self._sound("Basso")
                self._finish(cancel, IDLE, "skipped, " + why)
                return
            self.log("recorded %.1fs (level %.0f), transcribing..." % (dur, level))
            stt_cfg, cleanup_cfg = self.stt_cfg, self.cleanup_cfg  # snapshot
            try:
                with tempfile.TemporaryDirectory() as d:
                    wav = Path(d) / "clip.wav"
                    recorder.save_wav(audio, wav)
                    heard, out, ts, tl = process_wav(wav, stt_cfg, cleanup_cfg, cancel, self.debug)
            except Cancelled:
                rec["outcome"] = "cancelled"
                return  # state was already reset by whoever cancelled
            except Exception as e:
                self.log("ERROR: %s" % e)
                self._sound("Basso")
                self._finish(cancel, IDLE, "error: %s" % str(e)[:60])
                return
            rec.update(stt_s=round(ts, 2), clean_s=round(tl, 2), chars_in=len(heard), chars_out=len(out))
            if cancel.is_set():
                rec["outcome"] = "cancelled"
                return
            self.debug("heard : %s\noutput: %s" % (heard, out))
            if self.on_text and out:
                self.on_text(heard, out, self.stt_name)
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
            self.log("done: stt %.1fs, cleanup %.2fs, audio %.1fs" % (ts, tl, dur))
            note = "done (stopped at the %d s limit)" % self.max_seconds if capped else "done"
            if level < LOW_LEVEL:
                note += ", but the microphone is quiet (level %.0f): raise Sound > Input volume" % level
            self._finish(cancel, IDLE, note if ok else "paste failed (copied to clipboard)")
        except Exception as e:  # anything unexpected: never leave the UI stuck on "working"
            self.log("ERROR: %r" % e)
            rec["outcome"] = "error"
            self._sound("Basso")
            self._finish(cancel, IDLE, "error: %s" % str(e)[:60])
        finally:
            rec["total_s"] = round(time.perf_counter() - t_start, 2)
            self.on_metric(rec)

    def _finish(self, cancel, state, message):
        with self._lock:
            if cancel.is_set():  # cancelled meanwhile; do not clobber the newer state
                return
            self._set(state, message)
