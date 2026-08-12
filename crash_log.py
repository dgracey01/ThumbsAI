#!/usr/bin/env python
"""
crash_log.py - persistent crash capture for Jarvis.

Jarvis renders SDXL IN-PROCESS via torch, and the whole agent turn runs on a QThread. A native fault
(access violation / segfault inside torch or Qt) kills the entire process with NO Python traceback, so
a crash left nothing behind but a Windows Event-Log entry. This makes the NEXT one diagnosable:

  • faulthandler  -> on a fatal signal, dumps a native+Python stack of ALL threads to the log (this is
                    what reveals which torch/render call was executing on the TurnWorker at crash time).
  • sys.excepthook / threading.excepthook -> log uncaught Python exceptions (main + worker threads),
                    which Qt otherwise swallows silently in a QThread.run().

stdlib only. Import + install() as the FIRST thing in the entry point (before torch/PySide6) so even an
import-time crash is captured. Log: .jarvis_crash.log next to this file (line-buffered, appended).
"""
from __future__ import annotations

import atexit
import datetime
import faulthandler
import os
import sys
import threading
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
# Name the log after the app folder this module sits in, so a shared copy across apps still produces
# an obvious filename (.jarvis_crash.log / .thumbsai_crash.log / .mediadl_crash.log / …).
_APP = os.path.basename(_HERE).lower().replace(" ", "_") or "app"
LOG_PATH = os.path.join(_HERE, f".{_APP}_crash.log")
_fh = None


def _stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def install() -> str | None:
    """Idempotent. Returns the log path, or None if the log couldn't be opened."""
    global _fh
    if _fh is not None:
        return LOG_PATH
    try:
        _fh = open(LOG_PATH, "a", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        return None
    _fh.write(f"\n===== Jarvis session start {_stamp()}  (pid {os.getpid()}) =====\n")
    _fh.flush()

    # Native fault (0xC0000005 access violation, segfault, abort): full C+Python stack, every thread.
    try:
        faulthandler.enable(file=_fh, all_threads=True)
    except Exception:
        pass

    # Uncaught Python exception on the MAIN thread.
    _prev_hook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        try:
            _fh.write(f"\n----- UNCAUGHT (main) {_stamp()} -----\n")
            traceback.print_exception(exc_type, exc, tb, file=_fh)
            _fh.flush()
        except Exception:
            pass
        _prev_hook(exc_type, exc, tb)

    sys.excepthook = _excepthook

    # Uncaught Python exception on a WORKER thread (TurnWorker etc.) — Qt swallows these otherwise.
    def _threadhook(args):
        try:
            name = getattr(args.thread, "name", "?")
            _fh.write(f"\n----- UNCAUGHT (thread {name}) {_stamp()} -----\n")
            traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback, file=_fh)
            _fh.flush()
        except Exception:
            pass

    try:
        threading.excepthook = _threadhook       # Python 3.8+
    except Exception:
        pass

    atexit.register(_on_exit)
    return LOG_PATH


def _on_exit():
    try:
        if _fh is not None:
            _fh.write(f"===== clean exit {_stamp()} =====\n")
            _fh.flush()
    except Exception:
        pass


def log_note(msg: str):
    """Write a breadcrumb (e.g. 'starting native render') so the crash log shows what was in flight."""
    try:
        if _fh is not None:
            _fh.write(f"[{_stamp()}] {msg}\n")
            _fh.flush()
    except Exception:
        pass


if __name__ == "__main__":
    p = install()
    print("crash log:", p)
    log_note("self-test note")
