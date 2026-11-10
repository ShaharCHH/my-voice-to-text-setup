"""Speech-to-text and text cleanup, shared by the eval harness and the live app.

Stdlib only, no network. Keep this the single place that runs whisper-cli and the cleanup
rules, so what the eval measures is exactly what the app runs.
"""
import array
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import wave
from os.path import expanduser


def find_binary(name):
    """Resolve a command to a full path. Apps started from Finder get a minimal PATH that
    lacks Homebrew, so also look in the usual Homebrew locations."""
    if os.path.isabs(name) or "/" in name:
        return expanduser(name)
    path = os.environ.get("PATH", "") + os.pathsep + "/opt/homebrew/bin" + os.pathsep + "/usr/local/bin"
    found = shutil.which(name, path=path)
    if not found:
        raise RuntimeError("%s not found (looked in PATH and Homebrew); install it with brew" % name)
    return found


class Cancelled(Exception):
    """The user aborted this dictation."""


def read_pcm16(path):
    """Samples of a 16-bit mono WAV. Finds the data chunk by hand because macOS tools write
    'extensible' headers that Python's wave module rejects."""
    with open(path, "rb") as f:
        b = f.read()
    i = b.index(b"data")
    n = struct.unpack("<I", b[i + 4:i + 8])[0]
    samples = array.array("h")
    samples.frombytes(b[i + 8:i + 8 + n])
    return samples


def normalized_copy(wav, target_peak=0.5, max_gain=30.0):
    """Louden a quiet recording. Returns (path, is_temporary). Gain is set from the 99.5th
    percentile (not the maximum), so one keyboard thump cannot hold the whole clip down,
    and capped, so room noise in a nearly silent clip is not blown up."""
    samples = read_pcm16(wav)
    loud = sorted(abs(x) for x in samples)
    ref = loud[int(0.995 * (len(loud) - 1))] if loud else 0
    gain = min(max_gain, target_peak * 32767 / ref) if ref else 1.0
    if gain <= 1.1:  # already loud enough
        return str(wav), False
    out = array.array("h", (max(-32768, min(32767, int(x * gain))) for x in samples))
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(out.tobytes())
    return path, True


def run_stt(cfg, wav, cancel=None):
    """cancel: optional threading.Event; when set, whisper-cli is killed and Cancelled raised.
    cfg["normalize"]: true = louden quiet audio first."""
    if cfg["kind"] == "fake":
        return "FAKE TRANSCRIPT oc get pods"
    temporary = False
    if cfg.get("normalize"):
        wav, temporary = normalized_copy(wav)
    try:
        return _whisper_cpp(cfg, wav, cancel)
    finally:
        if temporary:
            os.unlink(wav)


def _whisper_cpp(cfg, wav, cancel):
    kind = cfg["kind"]
    if kind == "whisper-cpp":
        cmd = [
            find_binary(cfg.get("bin", "whisper-cli")),
            "-m", expanduser(cfg["model"]),
            "-f", str(wav),
            "-l", cfg.get("language", "en"),
            "-nt",  # no timestamps in output
            "-np",  # no progress/debug prints
        ]
        if cfg.get("prompt"):
            cmd += ["--prompt", cfg["prompt"]]  # vocabulary hint for decoding
        timeout = cfg.get("timeout", 60)  # Whisper can loop on bad audio
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8")
        waited = 0.0
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                waited += 0.1
                if cancel is not None and cancel.is_set():
                    proc.kill()
                    proc.communicate()
                    raise Cancelled()
                if waited > timeout:
                    proc.kill()
                    proc.communicate()
                    raise RuntimeError("whisper-cli timed out after %ss" % timeout)
        if proc.returncode != 0:
            raise RuntimeError("whisper-cli failed: " + stderr.strip()[-300:])
        return " ".join(stdout.split())
    raise ValueError("unknown stt kind: " + kind)


def apply_glossary(rules_file, text):
    """Deterministic cleanup: regex find-and-replace rules from a JSON file. Cannot drop or
    reword anything it has no rule for, which is why we use rules and not an LLM."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, rules_file), encoding="utf-8") as f:
        rules = json.load(f)
    for r in rules:
        text = re.sub(r["match"], r["to"], text, flags=re.I)
    return re.sub(r"\s{2,}", " ", text).strip()


# Spoken editing commands for dictation. A command must be a clause of its own
# (start of text or right after . ! ? , ; :) and be followed by punctuation or the end, so
# "I think we should delete that file." and "delete everything in the namespace" are left alone.
_BOUNDARY = r"(?:^|(?<=[.!?,;:]\s))"
_CMD_ALL = (r"(?:(?:scratch|delete|erase|forget|clear|remove)\s+(?:all\s+of\s+)?(?:everything|all\s+that|it\s+all)"
            r"(?:\s+(?:i|we)\s+(?:said|wrote|have))?(?:\s+so\s+far)?"
            r"|start\s+(?:over|again|from\s+scratch|from\s+the\s+beginning))")
_CMD_LAST = r"(?:scratch|strike|delete|erase|forget|cancel)\s+that"
_LEAD = r"(?:(?:no|nope|wait|sorry|oh|okay|ok|actually|never\s+mind)[,.!]?\s+)*"  # "No, never mind. Scratch that."
_TAIL = r"(?:[,.]?\s*(?:and\s+)?start\s+(?:from\s+here|over|again))?"
_AFTER = r"(?=[.!?,;:]|\s*$)"
_VOICE_CMD = re.compile(_BOUNDARY + _LEAD + r"(?:(?P<all>" + _CMD_ALL + r")|(?P<last>" + _CMD_LAST + r"))" + _TAIL + _AFTER, re.I)


_LOWERCASE_TOOLS = {"kubectl", "oc", "git", "docker", "helm", "terraform", "npm", "pip", "aws", "curl"}


def _capitalise(text):
    """A cut can leave a fragment like 'in eu-west-1.'; start it with a capital, but leave tool names alone."""
    first = text.split(" ", 1)[0] if text else ""
    return text if first in _LOWERCASE_TOOLS or not first[:1].islower() else text[:1].upper() + text[1:]


def apply_voice_commands(text):
    """'scratch that' removes the previous sentence; 'start over' / 'delete everything' removes
    everything before it. An optional trailing 'and start from here' is swallowed too."""
    cut = False
    for _ in range(20):  # a dictation with 20 commands is a different problem
        m = _VOICE_CMD.search(text)
        if not m:
            break
        cut = True
        after = text[m.end():].lstrip(" ,;:.!?")
        if m.group("all"):
            text = after
        else:
            before = text[:m.start()].rstrip(" ,;:")
            end = len(before) - 1 if before[-1:] in ".!?" else len(before)  # skip the sentence's own final mark
            at = max(before.rfind(c, 0, end) for c in ".!?")
            text = (before[:at + 1] + " " + after).strip() if at >= 0 else after
    return _capitalise(text) if cut else text


def clean_text(cfg, text):
    """Turn Whisper's text into the text we paste: glossary rules, then spoken edits."""
    kind = cfg["kind"]
    if kind == "fake":
        return "FAKE OUTPUT: " + text
    if kind == "glossary":
        text = apply_glossary(cfg["rules"], text)
        return apply_voice_commands(text) if cfg.get("voice_commands", True) else text
    raise ValueError("unknown cleanup kind: " + kind)
