#!/usr/bin/env python3
"""Voice Prompt: menubar app around src/core.py.

Hold Right Option to talk. Esc while holding = discard. Double-tap Right Option while it
is working = cancel. Settings are saved in ~/Library/Application Support/VoicePrompt/config.json.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

# The .app sets VOICEPROMPT_ROOT (see setup.py); from source fall back to this file's location.
ROOT = Path(os.environ.get("VOICEPROMPT_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT))

import rumps  # noqa: E402
from pynput import keyboard  # noqa: E402

from src import login, metrics  # noqa: E402
from src.core import IDLE, RECORDING, WORKING, Dictation, check_accessibility  # noqa: E402

CONFIG = Path.home() / "Library" / "Application Support" / "VoicePrompt" / "config.json"
DEFAULTS = {"mode": "hebrew", "hotkey": "alt_r", "log_text": True}
ICONS = {IDLE: "🎙", RECORDING: "🔴", WORKING: "⏳"}
ACCESSIBILITY_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


class VoicePrompt(rumps.App):
    def __init__(self):
        super().__init__("Voice Prompt", title=ICONS[IDLE], quit_button=None)
        self.cfg = load_config()
        self.setups = json.loads((ROOT / "eval" / "setups.json").read_text(encoding="utf-8"))
        if self.cfg["mode"] not in self.setups["modes"]:
            self.cfg["mode"] = DEFAULTS["mode"]
        stt_cfg, llm_cfg, system = self._mode_parts(self.cfg["mode"])
        self.dictation = Dictation(stt_cfg, llm_cfg, system, getattr(keyboard.Key, self.cfg["hotkey"]),
                                   log=lambda m: (print(m, flush=True), metrics.log_line(m)),
                                   debug=lambda m: print(m, flush=True), mode=self.cfg["mode"],
                                   on_text=metrics.record_text if self.cfg["log_text"] else None)
        self.listener = keyboard.Listener(on_press=self.dictation.on_press,
                                          on_release=self.dictation.on_release)
        self.listener.start()

        self.status = rumps.MenuItem("Ready")
        self.last = rumps.MenuItem("Last result: (none yet)", callback=self.copy_last)
        self.cancel_item = rumps.MenuItem("Cancel (or Esc / double-tap)")
        self.mode_menu = self._mode_menu()
        self.logs_item = rumps.MenuItem("Open logs folder", callback=lambda _: subprocess.run(["open", str(metrics.log_dir())]))
        self.login_item = rumps.MenuItem("Start at login", callback=self.toggle_login)
        self.login_item.state = 1 if login.is_enabled() else 0
        self.access_item = rumps.MenuItem("⚠ Allow Accessibility (needed to paste)", callback=self.open_access)
        self.menu = [self.status, self.last, self.cancel_item, None, self.mode_menu,
                     self.logs_item, self.login_item, self.access_item, None, rumps.MenuItem("Quit", callback=self.quit)]
        self.access_item.hidden = check_accessibility()
        self.timer = rumps.Timer(self.refresh, 0.3)  # UI updates on the main thread, not from workers
        self.timer.start()

    # ---- UI refresh ----
    def refresh(self, _):
        d = self.dictation
        self.title = ICONS[d.state]
        self.status.title = d.message.capitalize() if d.message else "Ready"
        self.cancel_item.set_callback(self.cancel if d.state != IDLE else None)  # None = greyed out
        if d.last:
            self.last.title = "Last result: " + (d.last[:48] + "…" if len(d.last) > 48 else d.last)
        self.access_item.hidden = check_accessibility()

    # ---- actions ----
    def cancel(self, _):
        self.dictation.cancel()

    def copy_last(self, _):
        if self.dictation.last:
            subprocess.run(["pbcopy"], input=self.dictation.last.encode("utf-8"),
                           env={"LC_CTYPE": "UTF-8"})

    def open_access(self, _):
        subprocess.run(["open", ACCESSIBILITY_URL])

    def toggle_login(self, item):
        if login.is_enabled():
            login.disable()
            item.state = 0
        else:
            path = login.bundle_path()
            if not path:
                rumps.alert("Start at login",
                            "This only works from the built Voice Prompt.app (you are running from source).")
                return
            login.enable(path)
            item.state = 1

    def _mode_parts(self, mode):
        m = self.setups["modes"][mode]
        return (self.setups["stt"][m["stt"]], self.setups["llm"][m["llm"]],
                (ROOT / m["prompt"]).read_text(encoding="utf-8"))

    def _mode_menu(self):
        menu = rumps.MenuItem("Mode")
        for key, m in self.setups["modes"].items():
            item = rumps.MenuItem(m["label"], callback=lambda it, k=key: self.choose_mode(k))
            item.state = 1 if key == self.cfg["mode"] else 0
            menu.add(item)
        return menu

    def choose_mode(self, key):
        self.cfg["mode"] = key
        save_config(self.cfg)
        self.dictation.set_mode(*self._mode_parts(key), mode=key)
        for k, it in zip(self.setups["modes"], self.mode_menu.values()):
            it.state = 1 if k == key else 0

    def quit(self, _):
        self.listener.stop()
        rumps.quit_application()


if __name__ == "__main__":
    VoicePrompt().run()
