"""
ai_console.py — the AI console that lives in the lower third of the left column.

The visible home of ThumbsAI's in-app intelligence and the DUAL-ENTRY switch:
  • Internal — ThumbsAI runs its own JoyCaption (works with the Suite/Jarvis closed).
  • Jarvis   — Jarvis drives the work; internal captioning stays off so JoyCaption isn't
               double-loaded into VRAM.
Both act on the same DB levers (see ai_ops.py) — this panel is just the front-end: mode switch,
Tag-folder / Find-dupes actions, a progress bar, and a live log. The heavy lifting runs on worker
threads (wired by thumbs_window); this widget only emits intent and displays results.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QComboBox, QProgressBar, QPlainTextEdit, QLineEdit)

from theme import BG, PAN, CAR, ACC, GRN, MUT, PRI, SEC, FONT, FONT_SM, FONT_MD


class AIConsole(QWidget):
    """Lower-left AI panel. Emits intent; thumbs_window runs the workers and calls back log/progress."""

    tag_folder_requested = Signal()
    find_dupes_requested = Signal()
    fetch_requested      = Signal()         # caption the selected image, then search for its concepts
    image_dropped        = Signal(str)      # an image dropped onto the console -> search by it
    ask_requested        = Signal(str)      # free-text question put to JoyCaption about the selected image
    stop_requested       = Signal()
    mode_changed         = Signal(str)      # "internal" | "jarvis"
    tag_mode_changed     = Signal(bool)     # True = Add (merge), False = Replace (overwrite)

    _IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif")

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self._db = db
        self.setStyleSheet(f"background:{BG};")
        self.setAcceptDrops(True)                    # drop an image here to search by it
        self._base_style = f"background:{BG};"
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(5)

        # ── header: title + mode toggle ──────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)
        title = QLabel("🤖 JoyCaption")
        title.setStyleSheet(f"color:{PRI};font-family:{FONT};font-size:{FONT_MD}px;font-weight:bold;"
                            f"background:transparent;")
        hdr.addWidget(title)
        hdr.addStretch(1)
        self._mode = QComboBox()
        self._mode.addItem("Internal", "internal")
        self._mode.addItem("Jarvis", "jarvis")
        self._mode.addItem("Off", "off")
        self._mode.setToolTip("Internal: ThumbsAI runs its own JoyCaption.\n"
                              "Jarvis: the Suite drives it (internal stays off to spare VRAM).\n"
                              "Off: unload JoyCaption and free its VRAM — Find dupes still works.")
        self._mode.setStyleSheet(self._combo_qss())
        self._mode.currentIndexChanged.connect(
            lambda *_: self.mode_changed.emit(self._mode.currentData()))
        hdr.addWidget(self._mode)
        # Tag write-mode: Add (merge into existing) vs Replace (overwrite). Mirrors the Settings ▸ AI
        # 'Merge / Override' toggle — same ai_tag_merge setting, surfaced here for one-click switching.
        self._tagmode = QComboBox()
        self._tagmode.addItem("Add", True)        # merge new tags with existing; skip already-tagged on re-run
        self._tagmode.addItem("Replace", False)   # re-tag every image and overwrite its tags
        self._tagmode.setToolTip("How 'Tag folder' writes tags:\n"
                                 "Add — merge new tags into existing (skips already-tagged, so a re-run "
                                 "resumes a big folder).\n"
                                 "Replace — re-tag every image and OVERWRITE its tags (regenerate after "
                                 "changing the persona or alias map).")
        self._tagmode.setStyleSheet(self._combo_qss())
        self._tagmode.currentIndexChanged.connect(
            lambda *_: self.tag_mode_changed.emit(bool(self._tagmode.currentData())))
        hdr.addWidget(self._tagmode)
        v.addLayout(hdr)

        # ── action buttons (two rows — the panel is narrow) ────────────────────────
        self._btn_tag = QPushButton("Tag folder")
        self._btn_tag.setToolTip("Caption every image in the current folder and write the words to its "
                                 "tags — makes even images with no embedded metadata searchable.")
        self._btn_tag.clicked.connect(self.tag_folder_requested)
        self._btn_dupes = QPushButton("Find dupes")
        self._btn_dupes.setToolTip("Group visually-identical images in the current folder and flag the "
                                   "extras as Reject (non-destructive — you review before deleting).")
        self._btn_dupes.clicked.connect(self.find_dupes_requested)
        self._btn_fetch = QPushButton("Fetch similar")
        self._btn_fetch.setToolTip("Caption the SELECTED image, then search the library for others that "
                                   "share its concepts — 'find more like this'.")
        self._btn_fetch.clicked.connect(self.fetch_requested)
        self._btn_stop = QPushButton("Stop")
        self._btn_stop.clicked.connect(self.stop_requested)
        self._btn_stop.setEnabled(False)
        for b in (self._btn_tag, self._btn_dupes, self._btn_fetch):
            b.setStyleSheet(self._btn_qss(ACC))
        self._btn_stop.setStyleSheet(self._btn_qss(MUT))
        r1 = QHBoxLayout(); r1.setSpacing(5); r1.addWidget(self._btn_tag); r1.addWidget(self._btn_dupes)
        r2 = QHBoxLayout(); r2.setSpacing(5); r2.addWidget(self._btn_fetch); r2.addWidget(self._btn_stop)
        v.addLayout(r1); v.addLayout(r2)

        # ── progress ─────────────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setTextVisible(True)
        self._progress.setFixedHeight(14)
        self._progress.setStyleSheet(
            f"QProgressBar{{background:{CAR};border:1px solid {MUT};border-radius:3px;color:{PRI};"
            f"font-family:{FONT};font-size:{FONT_SM}px;text-align:center;}}"
            f"QProgressBar::chunk{{background:{GRN};border-radius:2px;}}")
        self._progress.setVisible(False)
        v.addWidget(self._progress)

        # ── log ──────────────────────────────────────────────────────────────────
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(500)         # keep the buffer bounded
        self._log.setStyleSheet(
            f"QPlainTextEdit{{background:{PAN};color:{SEC};border:1px solid {MUT};border-radius:3px;"
            f"font-family:{FONT};font-size:{FONT_SM}px;padding:3px;}}")
        v.addWidget(self._log, stretch=1)

        # ── ask box: type a question to JoyCaption ABOUT the selected image ─────────
        ask_row = QHBoxLayout(); ask_row.setSpacing(5)
        self._ask = QLineEdit()
        self._ask.setPlaceholderText("“show me pictures of…” to search · or ask about the selected image")
        self._ask.setStyleSheet(
            f"QLineEdit{{background:{CAR};color:{PRI};border:1px solid {MUT};border-radius:3px;"
            f"padding:4px 6px;font-family:{FONT};font-size:{FONT_SM}px;}}"
            f"QLineEdit:focus{{border-color:{ACC};}}")
        self._ask.returnPressed.connect(self._emit_ask)
        self._btn_ask = QPushButton("Ask")
        self._btn_ask.setToolTip("Two things in one box:\n"
                                 "• “show me pictures of a blonde woman in a park” — searches the tagged "
                                 "library.\n"
                                 "• a question with an image selected — asks JoyCaption ABOUT that image "
                                 "(read-only, never changes tags).")
        self._btn_ask.setStyleSheet(self._btn_qss(ACC))
        self._btn_ask.clicked.connect(self._emit_ask)
        ask_row.addWidget(self._ask, stretch=1); ask_row.addWidget(self._btn_ask)
        v.addLayout(ask_row)

        self.log("AI console ready — Tag folder / Find dupes, DROP an image to search by it, or use the "
                 "box below: “show me pictures of…” searches the library; a question with an image "
                 "selected asks JoyCaption about it.")

    def _emit_ask(self):
        text = self._ask.text().strip()
        if text:
            self._ask.clear()
            self.ask_requested.emit(text)

    # ── styling helpers ─────────────────────────────────────────────────────────
    def _btn_qss(self, bg):
        """Idle button: clear hover / pressed / keyboard-focus feedback so a click is obviously landing."""
        return (f"QPushButton{{background:{bg};color:{PRI};border:1px solid transparent;border-radius:3px;"
                f"padding:4px 8px;font-family:{FONT};font-size:{FONT_SM}px;}}"
                f"QPushButton:hover{{background:#2a7fff;border:1px solid #8ec0ff;}}"
                f"QPushButton:pressed{{background:#1550c0;border:1px solid {PRI};}}"
                f"QPushButton:focus{{border:1px solid {PRI};outline:none;}}"
                f"QPushButton:disabled{{background:{MUT};color:{SEC};border:1px solid transparent;}}")

    def _btn_active_qss(self):
        """The action currently RUNNING: a bright green, bold, ring-bordered highlight that stays lit even
        while the button is disabled mid-job — so it's obvious at a glance which action is in progress."""
        return (f"QPushButton{{background:{GRN};color:#0b120b;border:2px solid {PRI};border-radius:3px;"
                f"padding:3px 7px;font-family:{FONT};font-size:{FONT_SM}px;font-weight:bold;}}"
                f"QPushButton:disabled{{background:{GRN};color:#0b120b;border:2px solid {PRI};}}")

    def _combo_qss(self):
        return (f"QComboBox{{background:{CAR};color:{PRI};border:1px solid {MUT};border-radius:3px;"
                f"padding:2px 6px;font-family:{FONT};font-size:{FONT_SM}px;}}"
                f"QComboBox QAbstractItemView{{background:{CAR};color:{PRI};"
                f"selection-background-color:{ACC};}}")

    # ── public API (called by thumbs_window / workers) ───────────────────────────
    def mode(self) -> str:
        return self._mode.currentData()

    def set_mode(self, mode: str):
        """Set the mode combo without emitting mode_changed (used to init from saved settings)."""
        i = self._mode.findData(mode)
        if i >= 0:
            self._mode.blockSignals(True)
            self._mode.setCurrentIndex(i)
            self._mode.blockSignals(False)

    def tag_mode(self) -> bool:
        """True = Add (merge), False = Replace (overwrite)."""
        return bool(self._tagmode.currentData())

    def set_tag_mode(self, merge: bool):
        """Set the Add/Replace combo without emitting tag_mode_changed (init from saved settings)."""
        i = self._tagmode.findData(bool(merge))
        if i >= 0:
            self._tagmode.blockSignals(True)
            self._tagmode.setCurrentIndex(i)
            self._tagmode.blockSignals(False)

    def log(self, msg: str):
        self._log.appendPlainText(str(msg))

    def set_progress(self, current: int, total: int):
        """Show/advance the bar. total<=0 hides it (done/idle)."""
        if total <= 0:
            self._progress.setVisible(False)
            return
        self._progress.setVisible(True)
        self._progress.setMaximum(total)
        self._progress.setValue(max(0, min(current, total)))

    def set_busy(self, busy: bool, active: str | None = None):
        """Disable actions + enable Stop while a job runs (and disable mode switching mid-job). `active`
        is which action is running ("tag" | "dupes" | "fetch" | "ask") — that button is lit up so it's
        obvious what's in progress; the rest grey out."""
        self._btn_tag.setEnabled(not busy)
        self._btn_dupes.setEnabled(not busy)
        self._btn_fetch.setEnabled(not busy)
        self._btn_ask.setEnabled(not busy)
        self._ask.setEnabled(not busy)
        self._mode.setEnabled(not busy)
        self._tagmode.setEnabled(not busy)
        self._btn_stop.setEnabled(busy)
        # Highlight the running action (bright green) and normal-style the rest.
        for name, b in (("tag", self._btn_tag), ("dupes", self._btn_dupes),
                        ("fetch", self._btn_fetch), ("ask", self._btn_ask)):
            b.setStyleSheet(self._btn_active_qss() if (busy and name == active) else self._btn_qss(ACC))
        if not busy:
            self.set_progress(0, 0)

    # ── drag & drop: drop an image onto the console to search by it ────────────────
    def _first_image(self, mime) -> str:
        if not mime.hasUrls():
            return ""
        for url in mime.urls():
            p = url.toLocalFile()
            if p and p.lower().endswith(self._IMG_EXTS):
                return p
        return ""

    def dragEnterEvent(self, e):
        if self._first_image(e.mimeData()):
            e.acceptProposedAction()
            self.setStyleSheet(f"background:{BG};border:2px dashed {ACC};")   # highlight drop zone
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self.setStyleSheet(self._base_style)

    def dropEvent(self, e):
        self.setStyleSheet(self._base_style)
        path = self._first_image(e.mimeData())
        if path:
            e.acceptProposedAction()
            self.image_dropped.emit(path)
        else:
            e.ignore()
