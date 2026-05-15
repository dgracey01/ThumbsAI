"""
ThumbsAI — AI Image Browser
Designed by: Zero  |  Built by: Jarvis

Entry point.
"""
import sys
import os
import traceback
from pathlib import Path

# ── Venv enforcer ─────────────────────────────────────────────────────────────
# If launched via double-click or system Python, re-exec under the venv
# interpreter so all installed packages and compiled .pyc files are consistent.
_HERE      = Path(__file__).resolve().parent
_VENV_PY   = _HERE / ".venv" / "Scripts" / "pythonw.exe"
if _VENV_PY.exists():
    _want = _VENV_PY.resolve()
    _have = Path(sys.executable).resolve()
    if _have != _want:
        import subprocess
        subprocess.Popen([str(_want), str(Path(__file__).resolve())] + sys.argv[1:])
        sys.exit(0)
# ──────────────────────────────────────────────────────────────────────────────

_LOG = Path(__file__).parent / "data" / "startup_error.log"


def _write_crash(tb: str):
    try:
        from datetime import datetime
        _LOG.parent.mkdir(exist_ok=True)
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.now()} ===\n{tb}\n")
    except Exception:
        pass


def main():
    app = None
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtCore    import QLockFile
        app = QApplication(sys.argv)
        app.setApplicationName("ThumbsAI")

        _lock = QLockFile(str(Path.home() / ".thumbsai.lock"))
        _lock.setStaleLockTime(10000)  # break stale locks older than 10 s
        if not _lock.tryLock(500):
            _lock.removeStaleLockFile()
            if not _lock.tryLock(200):
                QMessageBox.warning(None, "ThumbsAI", "ThumbsAI is already running.")
                sys.exit(0)

        from theme         import apply_theme
        from thumbs_window import ThumbsWindow
        apply_theme(app)

        win = ThumbsWindow()
        win.show()
        sys.exit(app.exec())

    except Exception:
        tb = traceback.format_exc()
        _write_crash(tb)
        # Try to show a message box if Qt is up
        try:
            from PySide6.QtWidgets import QMessageBox
            if app:
                mb = QMessageBox()
                mb.setWindowTitle("ThumbsAI — Startup Error")
                mb.setText("ThumbsAI failed to start.")
                mb.setDetailedText(tb)
                mb.exec()
        except Exception:
            pass


if __name__ == "__main__":
    main()
