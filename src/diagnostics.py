"""Leave a trail when the app dies, so a crash is not just "it closed".

- Fatal signals (segfault, abort, and SIGTRAP, which is how macOS reports the
  EXC_BREAKPOINT that killed the app once) write every thread's Python stack to crash.log.
- An uncaught exception in any thread goes to app.log instead of vanishing.
- On the next start, a non-empty crash.log is moved aside and reported once.
"""
import faulthandler
import os
import signal
import sys
import threading
import time
import traceback

from src import metrics

_crash_file = None  # kept open for the life of the process; faulthandler writes to the raw fd


def install():
    """Call once, early, from the main thread."""
    global _crash_file
    path = metrics.log_dir() / "crash.log"
    _crash_file = open(path, "a", buffering=1)
    _crash_file.write("--- started %s pid %d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    faulthandler.enable(file=_crash_file, all_threads=True)
    # faulthandler skips SIGTRAP by default; chain=True lets the default action (die) still happen
    faulthandler.register(signal.SIGTRAP, file=_crash_file, all_threads=True, chain=True)

    def thread_hook(args):
        metrics.log_line("UNCAUGHT in thread %s: %s" % (getattr(args.thread, "name", "?"),
                         "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)).strip()))
    threading.excepthook = thread_hook

    def main_hook(exc_type, exc, tb):
        metrics.log_line("UNCAUGHT: " + "".join(traceback.format_exception(exc_type, exc, tb)).strip())
        sys.__excepthook__(exc_type, exc, tb)
    sys.excepthook = main_hook


def previous_crash():
    """If the last session died with a trace, move it aside and return its path (once); else None."""
    path = metrics.log_dir() / "crash.log"
    try:
        # a clean start only writes the one "--- started" line; a trace has more
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                 if line.strip() and not line.startswith("--- started")]
    except OSError:
        return None
    if not lines:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    dest = metrics.log_dir() / ("crash-%s.log" % time.strftime("%Y%m%d-%H%M%S"))
    path.rename(dest)
    return dest
