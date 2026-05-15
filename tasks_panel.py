"""
tasks_panel.py — Task queue panel for ThumbsAI
Designed by: Zero  |  Built by: Jarvis

Shows queued background operations below the folder tree.
"""
from __future__ import annotations
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem, QMenu,
)
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui  import QColor

from theme import BG, PAN, CAR, ACC, MUT, PRI, SEC, RED, FONT, FONT_SM, FONT_MD


class TasksPanel(QWidget):
    """
    Compact panel that shows the current task queue.

    Signals
    -------
    close_requested   : user clicked ✕ — parent should hide panel + uncheck setting
    pin_changed(bool) : user toggled the pin button
    """
    close_requested  = Signal()
    pin_changed      = Signal(bool)
    quit_task        = Signal(int)   # index of task to cancel
    quit_all_tasks   = Signal()
    clear_errors     = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pinned  = False
        self._tasks:  list[str] = []
        self._enabled = True       # False when user unchecks the setting

        self.setMaximumHeight(200)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────────
        hdr = QFrame()
        hdr.setFixedHeight(24)
        hdr.setStyleSheet(
            f"QFrame{{background:{PAN};border-top:1px solid {MUT};"
            f"border-bottom:1px solid {MUT};}}")
        hh = QHBoxLayout(hdr)
        hh.setContentsMargins(8, 0, 4, 0)
        hh.setSpacing(2)

        lbl = QLabel("Tasks", hdr)
        lbl.setStyleSheet(
            f"color:{SEC};font-family:{FONT};font-size:{FONT_SM}px;"
            f"font-weight:bold;background:transparent;border:none;")
        hh.addWidget(lbl)
        hh.addStretch()

        # Pin button — keeps panel visible even when queue is empty
        self._btn_pin = QPushButton("◈", hdr)
        self._btn_pin.setFixedSize(18, 18)
        self._btn_pin.setCheckable(True)
        self._btn_pin.setToolTip("Pin — keep panel visible when queue is empty")
        self._btn_pin.setStyleSheet(self._pin_style(False))
        self._btn_pin.clicked.connect(self._on_pin_clicked)
        hh.addWidget(self._btn_pin)

        # Close button
        btn_close = QPushButton("✕", hdr)
        btn_close.setFixedSize(18, 18)
        btn_close.setToolTip("Hide Tasks panel")
        btn_close.setStyleSheet(
            f"QPushButton{{background:transparent;color:{SEC};border:none;"
            f"font-size:{FONT_SM}px;font-weight:bold;}}"
            f"QPushButton:hover{{color:{RED};}}")
        btn_close.clicked.connect(self.close_requested.emit)
        hh.addWidget(btn_close)

        v.addWidget(hdr)

        # ── Task list ─────────────────────────────────────────────────────────
        self._list = QListWidget(self)
        self._list.setStyleSheet(
            f"QListWidget{{background:{CAR};border:none;"
            f"font-family:{FONT};font-size:{FONT_SM}px;color:{PRI};}}"
            f"QListWidget::item{{padding:4px 6px;}}"
            f"QListWidget::item:selected{{background:{MUT};}}")
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        v.addWidget(self._list)

        # Start hidden — appears when tasks arrive
        self.setVisible(False)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable the panel (from settings checkbox)."""
        self._enabled = enabled
        if not enabled:
            self.setVisible(False)
        else:
            self._apply_visibility()

    def update_tasks(self, tasks: list[str]) -> None:
        """Receive updated task list from ThumbGrid.task_list_changed."""
        self._tasks = list(tasks)
        self._list.clear()
        for i, desc in enumerate(tasks):
            prefix = "▶" if i == 0 else "·"
            item   = QListWidgetItem(f"  {prefix}  {desc}")
            color  = QColor(RED) if i == 0 else QColor("#cc8844")
            item.setForeground(color)
            self._list.addItem(item)
        # Resize list to fit items (max 6 visible rows)
        row_h = self._list.sizeHintForRow(0) if self._list.count() else 20
        self._list.setFixedHeight(min(len(tasks), 6) * (row_h + 2) + 4)
        self._apply_visibility()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _apply_visibility(self) -> None:
        if not self._enabled:
            self.setVisible(False)
            return
        show = bool(self._tasks) or self._pinned
        self.setVisible(show)

    def _on_pin_clicked(self, checked: bool) -> None:
        self._pinned = checked
        self._btn_pin.setStyleSheet(self._pin_style(checked))
        self.pin_changed.emit(checked)
        self._apply_visibility()

    def _on_context_menu(self, pos: QPoint) -> None:
        item  = self._list.itemAt(pos)
        idx   = self._list.row(item) if item else -1
        has_tasks = bool(self._tasks)

        _ms = (f"QMenu{{background:{CAR};color:{PRI};border:1px solid {MUT};"
               f"font-family:{FONT};font-size:{FONT_SM}px;}}"
               f"QMenu::item{{padding:5px 20px;}}"
               f"QMenu::item:selected{{background:{ACC};color:#000;}}"
               f"QMenu::item:disabled{{color:{MUT};}}"
               f"QMenu::separator{{background:{MUT};height:1px;margin:3px 6px;}}")
        menu = QMenu(self)
        menu.setStyleSheet(_ms)

        act_props = menu.addAction("Task Properties")
        act_props.setEnabled(idx >= 0)
        menu.addSeparator()

        act_pause  = menu.addAction("Pause task")
        act_resume = menu.addAction("Resume Task")
        act_quit   = menu.addAction("Quit Task")
        act_pause.setEnabled(False)    # pause not yet implemented
        act_resume.setEnabled(False)
        act_quit.setEnabled(idx >= 0)
        menu.addSeparator()

        act_pause_all  = menu.addAction("Pause All tasks")
        act_resume_all = menu.addAction("Resume All Tasks")
        act_quit_all   = menu.addAction("Quit All Tasks")
        act_pause_all.setEnabled(False)
        act_resume_all.setEnabled(False)
        act_quit_all.setEnabled(has_tasks)
        menu.addSeparator()

        act_show_err  = menu.addAction("Show Errors")
        act_clear_err = menu.addAction("Clear Errors")
        act_clear_all = menu.addAction("Clear All Errors")
        act_show_err.setEnabled(False)
        act_clear_err.setEnabled(False)
        act_clear_all.setEnabled(False)

        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen == act_quit and idx >= 0:
            self.quit_task.emit(idx)
        elif chosen == act_quit_all:
            self.quit_all_tasks.emit()
        elif chosen == act_clear_err or chosen == act_clear_all:
            self.clear_errors.emit()
        elif chosen == act_props and idx >= 0:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "Task Properties",
                f"Task #{idx + 1}:\n{self._tasks[idx]}")

    @staticmethod
    def _pin_style(pinned: bool) -> str:
        bg  = ACC if pinned else "transparent"
        col = PRI if pinned else SEC
        return (
            f"QPushButton{{background:{bg};color:{col};border:none;"
            f"font-size:{FONT_SM}px;border-radius:3px;}}"
            f"QPushButton:hover{{color:{PRI};}}")
