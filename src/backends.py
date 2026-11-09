"""STT and LLM backends, shared by the eval harness and the live app.

Stdlib only. Keep this the single place that talks to whisper-cli / Ollama / Anthropic,
so what the eval measures is exactly what the app runs.
"""
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
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


def run_stt(cfg, wav, cancel=None):
    """cancel: optional threading.Event; when set, whisper-cli is killed and Cancelled raised."""
    kind = cfg["kind"]
    if kind == "fake":
        return "FAKE TRANSCRIPT oc get pods"
    if kind == "whisper-cpp":
        cmd = [
            find_binary(cfg.get("bin", "whisper-cli")),
            "-m", expanduser(cfg["model"]),
            "-f", str(wav),
            "-l", cfg.get("language", "he"),  # "he" forces Hebrew, "auto" detects
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


def post_json(url, body, headers, timeout=120):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers=dict(headers, **{"content-type": "application/json"}),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Surface the API's own error text; a bare "HTTP 400" hides the cause.
        raise RuntimeError("HTTP %d from %s: %s" % (e.code, url, e.read().decode("utf-8", "replace")[:300]))


def apply_glossary(rules_file, text):
    """Deterministic cleanup: regex find-and-replace rules from a JSON file. Cannot drop or
    reword anything it has no rule for, which is why English mode uses it instead of an LLM."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, rules_file), encoding="utf-8") as f:
        rules = json.load(f)
    for r in rules:
        text = re.sub(r["match"], r["to"], text, flags=re.I)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:1].upper() + text[1:] if text and text[0].islower() else text


def run_llm(cfg, system, transcript):
    # The transcript is wrapped in tags so the model treats it as data (see prompts/v1.md).
    user = "<transcript>\n" + transcript + "\n</transcript>"
    kind = cfg["kind"]
    if kind == "fake":
        return "FAKE OUTPUT: " + transcript
    if kind == "glossary":
        return apply_glossary(cfg["rules"], transcript)
    if kind == "ollama":
        host = cfg.get("host", "http://localhost:11434")
        r = post_json(host + "/api/chat", {
            "model": cfg["model"], "stream": False,
            "keep_alive": cfg.get("keep_alive", "30m"),  # keep the model in RAM between dictations
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }, {})
        return r["message"]["content"].strip()
    if kind == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        r = post_json("https://api.anthropic.com/v1/messages", {
            "model": cfg["model"], "max_tokens": 1024, "temperature": 0, "system": system,
            "messages": [{"role": "user", "content": user}],
        }, {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return r["content"][0]["text"].strip()
    raise ValueError("unknown llm kind: " + kind)
