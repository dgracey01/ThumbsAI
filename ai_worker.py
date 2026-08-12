"""
ai_worker.py — background QThread workers for the AI console.

Keeps the heavy AI-ops (duplicate scan now; captioning next) OFF the UI thread. Each worker calls the
shared ai_ops core, streams progress/log via signals, and polls a cancel flag so Stop is responsive.
The DB is safe to touch from a worker thread — ThumbsDB opens one connection PER THREAD over WAL.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

import ai_ops


class DupeWorker(QThread):
    """Scan a folder for visually-duplicate images (perceptual hash over cached thumbnails)."""

    progress = Signal(int, int)     # (current, total)
    log      = Signal(str)
    done     = Signal(list)         # list[{"paths":[…], "exact":[…]}]

    def __init__(self, db, folder: str, recursive: bool = False, threshold: int = 5, parent=None):
        super().__init__(parent)
        self._db = db
        self._folder = folder
        self._recursive = recursive
        self._threshold = threshold
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            groups = ai_ops.find_duplicate_groups(
                self._db, folder=self._folder, recursive=self._recursive, threshold=self._threshold,
                on_progress=lambda c, t: self.progress.emit(c, t),
                should_cancel=lambda: self._cancel)
        except Exception as e:
            self.log.emit(f"Duplicate scan failed: {e}")
            groups = []
        self.done.emit([] if self._cancel else groups)


class CaptionWorker(QThread):
    """Auto-tag a list of images: bring the JoyCaption endpoint up, then caption→tags each one."""

    progress = Signal(int, int)     # (current, total)
    caption  = Signal(str, str)     # (filename, tags preview) — live
    log      = Signal(str)
    done     = Signal(int)          # count tagged

    def __init__(self, db, captioner, filepaths, merge: bool = True, parent=None):
        super().__init__(parent)
        self._db = db
        self._captioner = captioner
        self._filepaths = filepaths
        self._merge = merge
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        import os
        self.log.emit(f"Starting JoyCaption ({self._captioner.mode()} mode)…")
        rep = self._captioner.ensure_up()
        if not rep.get("ok"):
            self.log.emit(rep.get("error") or "captioner failed to start")
            self.done.emit(0)
            return
        self.log.emit(f"Tagging {len(self._filepaths)} image(s)…")

        def prog(c, t, fp, text):
            self.progress.emit(c, t)
            self.caption.emit(os.path.basename(fp), (text or "")[:80])

        try:
            n = ai_ops.caption_to_tags(
                self._db, self._filepaths, self._captioner, merge=self._merge,
                on_progress=prog, should_cancel=lambda: self._cancel)
        except Exception as e:
            self.log.emit(f"Tagging failed: {e}")
            n = 0
        self.done.emit(0 if self._cancel else n)


class FetchWorker(QThread):
    """Caption ONE image (the query) and return its tags — for 'fetch similar'. Does not write to the DB."""

    log  = Signal(str)
    done = Signal(str)              # the caption/tags text ("" on failure/cancel)

    def __init__(self, captioner, image_path, parent=None):
        super().__init__(parent)
        self._captioner = captioner
        self._image_path = image_path
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        self.log.emit(f"Looking at the image ({self._captioner.mode()} mode)…")
        rep = self._captioner.ensure_up()
        if not rep.get("ok"):
            self.log.emit(rep.get("error") or "captioner failed to start")
            self.done.emit("")
            return
        text = self._captioner.caption(self._image_path)
        if text.startswith(("ERROR", "CAPTION FAILED")):
            self.log.emit(text)
            text = ""
        self.done.emit("" if self._cancel else text)


class AskWorker(QThread):
    """Talk to JoyCaption: put a free-text question/instruction to it ABOUT one image and return the
    reply. This is the console's conversational channel — it does NOT write tags or touch the DB, so
    you can interrogate an image ('what colour is her jacket?', 'describe the background') without
    changing anything. JoyCaption is a vision model, so an image is required."""

    log  = Signal(str)
    done = Signal(str)              # the reply text ("" on failure)

    def __init__(self, captioner, prompt: str, image_path: str, parent=None):
        super().__init__(parent)
        self._captioner = captioner
        self._prompt = prompt
        self._image_path = image_path

    def run(self):
        rep = self._captioner.ensure_up()
        if not rep.get("ok"):
            self.log.emit(rep.get("error") or "captioner failed to start")
            self.done.emit("")
            return
        text = self._captioner.caption(self._image_path, self._prompt)   # custom instruction, not the persona
        if text.startswith(("ERROR", "CAPTION FAILED")):
            self.log.emit(text)
            text = ""
        self.done.emit(text)
