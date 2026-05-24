"""
folder_panel.py — Left-side folder tree panel for ThumbsAI
Designed by: Zero  |  Built by: Jarvis
"""
from __future__ import annotations
from pathlib import Path
from PySide6.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout,
                               QTreeView, QLabel, QPushButton, QFileSystemModel,
                               QMenu, QCheckBox, QFileIconProvider)
from PySide6.QtCore    import Qt, Signal, QDir, QModelIndex, QFileInfo, QObject
from PySide6.QtGui     import QColor, QStandardItemModel, QStandardItem

from theme import BG, PAN, MUT, ACC, AMB, PRI, SEC, FONT, FONT_SM

# Folder state colors
_COL_NO_DATA   = QColor("#d4a43a")   # yellow  — not scanned
_COL_HAS_THUMB = QColor("#4caf50")   # green   — has thumbnails
_COL_HAS_SUBS  = QColor("#4ca8d4")   # blue    — has sub-folder thumbnails
_COL_FAVORITE  = QColor(AMB)         # amber   — favorited folder
_COL_WATCHED   = QColor("#00bcd4")   # cyan    — always-watched folder


class _ColoredFolderModel(QFileSystemModel):
    """QFileSystemModel that colours folder items based on DB thumbnail state."""

    _cache_ready = Signal(object)   # dict[str, QColor] — thread-safe cache update

    def __init__(self, parent=None):
        super().__init__(parent)
        self._db          = None
        self._cache: dict[str, QColor] = {}
        self._favorites: set[str] = set()
        self._watched:   set[str] = set()
        self._cache_ready.connect(self._apply_cache)

    def set_db(self, db) -> None:
        self._db = db
        # No preload here — colors are populated lazily per folder on demand
        # and explicitly when a folder is navigated to (prime_folder).

    def set_favorites(self, favs: set[str]) -> None:
        self._favorites = favs
        self.layoutChanged.emit()

    def set_watched(self, watched: set[str]) -> None:
        self._watched = watched
        self.layoutChanged.emit()

    def invalidate_cache(self) -> None:
        self._cache.clear()
        self.layoutChanged.emit()

    def prime_folder(self, folder: str) -> None:
        """
        Load thumbnail-presence colors for `folder`, its immediate children,
        and its parent folder — all in one background thread.  Priming the
        parent ensures that a container folder (e.g. Movies) is colored blue
        even when the user navigates directly to a leaf subfolder.
        """
        if self._db is None:
            return
        import threading, sqlite3
        from database import THUMBS_DB

        parent = str(Path(folder).parent)
        if parent == folder:      # drive root is its own parent — skip
            parent = ""

        def _prime_one(conn, tgt: str, cache: dict) -> None:
            """Query DB for tgt and its direct children; update cache in place."""
            prefix = tgt.rstrip("\\") + "\\"

            # Does tgt itself have thumbnails?
            row = conn.execute(
                "SELECT 1 FROM images i "
                "JOIN thumbnails t ON t.image_id = i.id "
                "WHERE i.folder = ? LIMIT 1",
                (tgt,)
            ).fetchone()
            if row:
                cache[tgt] = _COL_HAS_THUMB

            # Direct children with thumbnails (use | as LIKE escape — invalid
            # in Windows paths so \ in the prefix stays a plain literal).
            escaped = (prefix
                       .replace("|", "||")
                       .replace("%", "|%")
                       .replace("_", "|_"))
            rows = conn.execute(
                "SELECT DISTINCT i.folder FROM images i "
                "JOIN thumbnails t ON t.image_id = i.id "
                "WHERE i.folder LIKE ? ESCAPE '|'",
                (escaped + "%",)
            ).fetchall()
            for (child,) in rows:
                rel = child[len(prefix):]
                if "\\" not in rel:
                    cache[child] = _COL_HAS_THUMB
                if tgt not in cache:
                    cache[tgt] = _COL_HAS_SUBS

        def _bg():
            cache_update: dict[str, QColor] = {}
            try:
                conn = sqlite3.connect(str(THUMBS_DB), check_same_thread=False)
                conn.execute("PRAGMA query_only = ON")
                conn.execute("PRAGMA cache_size=-2048")
                _prime_one(conn, folder, cache_update)
                if parent:
                    _prime_one(conn, parent, cache_update)
                conn.close()
            except Exception:
                pass
            if cache_update:
                self._cache_ready.emit(cache_update)

        threading.Thread(target=_bg, daemon=True).start()

    def _apply_cache(self, update: dict) -> None:
        self._cache.update(update)
        self.layoutChanged.emit()

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if role == Qt.ForegroundRole and self._db is not None:
            path = str(Path(self.filePath(index)))   # normalize to OS backslashes
            if path in self._favorites:
                return _COL_FAVORITE
            if path in self._watched:
                return _COL_WATCHED
            # prime_folder() caches colors for the current folder and its children.
            # Anything not yet primed shows yellow (no data) until navigated to.
            return self._cache.get(path, _COL_NO_DATA)
        return super().data(index, role)


def _tree_qss(size: int = FONT_SM) -> str:
    return f"""
QTreeView {{
    background: {BG};
    color: {PRI};
    border: none;
    font-family: {FONT};
    font-size: {size}px;
    outline: none;
    show-decoration-selected: 1;
}}
QTreeView::item {{
    padding: 2px 2px;
    min-height: 20px;
}}
QTreeView::item:hover  {{ background: {MUT}; }}
QTreeView::item:selected {{ background: {ACC}; color: #000; }}
QTreeView::branch {{ background: {BG}; color: {SEC}; }}
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {{
    border-image: none;
    image: url(:/qt-project.org/styles/commonstyle/images/right-arrow.png);
}}
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {{
    border-image: none;
    image: url(:/qt-project.org/styles/commonstyle/images/down-arrow.png);
}}
"""

_MENU_QSS = f"""
QMenu {{
    background: {PAN}; color: {PRI};
    border: 1px solid {MUT};
    font-family: {FONT}; font-size: {FONT_SM}px;
    padding: 2px;
}}
QMenu::item {{ padding: 5px 20px; }}
QMenu::item:selected {{ background: {ACC}; color: #000; }}
"""


class FolderPanel(QWidget):
    folder_selected = Signal(str)
    refresh_clicked = Signal()
    watch_toggled   = Signal(str, bool)   # (path, is_now_watched)

    def __init__(self, db=None, settings=None, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(160)
        self.setMaximumWidth(340)

        self._settings      = settings
        self._current_path  = ""
        self._favorites: set[str] = set(
            (settings.get("favorites") or []) if settings else [])
        self._watched: set[str] = set(
            (settings.get("watched_folders") or []) if settings else [])
        self._fav_mode  = bool(settings.get("fav_mode", False)) if settings else False
        self._font_size = int((settings.get("font_folder") or FONT_SM)
                              if settings else FONT_SM)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = QFrame(self)
        hdr.setFixedHeight(26)
        hdr.setStyleSheet(
            f"QFrame{{background:{PAN};border-bottom:1px solid {MUT};}}")
        hh = QHBoxLayout(hdr)
        hh.setContentsMargins(6, 0, 4, 0)
        hh.setSpacing(4)

        lbl = QLabel("Folders", hdr)
        lbl.setStyleSheet(
            f"color:{SEC};font-family:{FONT};"
            f"font-size:{FONT_SM}px;font-weight:bold;background:transparent;")
        hh.addWidget(lbl)
        hh.addStretch()

        self._fav_chk = QCheckBox("★ Favorites", hdr)
        self._fav_chk.setStyleSheet(
            f"QCheckBox{{color:{AMB};font-family:{FONT};"
            f"font-size:{FONT_SM}px;spacing:4px;background:transparent;}}"
            f"QCheckBox::indicator{{width:12px;height:12px;"
            f"border:1px solid {MUT};border-radius:2px;background:{BG};}}"
            f"QCheckBox::indicator:checked{{background:{AMB};border-color:{AMB};}}")
        self._fav_chk.setToolTip("Show only favorite folders")
        if self._fav_mode:
            self._fav_chk.blockSignals(True)
            self._fav_chk.setChecked(True)
            self._fav_chk.blockSignals(False)
        self._fav_chk.toggled.connect(self._on_fav_filter_toggled)
        hh.addWidget(self._fav_chk)

        btn_refresh = QPushButton("⟳", hdr)
        btn_refresh.setFixedSize(20, 20)
        btn_refresh.setToolTip("Refresh folder colours")
        btn_refresh.setStyleSheet(
            f"QPushButton{{background:transparent;color:{SEC};border:none;"
            f"font-size:{FONT_SM + 2}px;font-weight:bold;}}"
            f"QPushButton:hover{{color:{ACC};}}")
        btn_refresh.clicked.connect(self.refresh_clicked.emit)
        hh.addWidget(btn_refresh)
        v.addWidget(hdr)

        # ── Filesystem model + tree ───────────────────────────────────────────
        self._model = _ColoredFolderModel(self)
        if db is not None:
            self._model.set_db(db)
        self._model.set_favorites(self._favorites)
        self._model.set_watched(self._watched)
        self._model.setRootPath("")
        self._model.setFilter(
            QDir.Filter.AllDirs |
            QDir.Filter.NoDotAndDotDot |
            QDir.Filter.Drives)

        self._tree = QTreeView(self)
        self._tree.setModel(self._model)
        self._tree.setRootIndex(self._model.index(""))
        for col in range(1, self._model.columnCount()):
            self._tree.hideColumn(col)
        self._tree.setHeaderHidden(True)
        self._tree.setAnimated(True)
        self._tree.setIndentation(14)
        self._tree.setUniformRowHeights(True)
        self._tree.setStyleSheet(_tree_qss(self._font_size))
        self._tree.clicked.connect(self._on_clicked)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        v.addWidget(self._tree)

        if self._fav_mode:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self._rebuild_fav_model)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_db(self, db) -> None:
        self._model.set_db(db)

    def refresh_colors(self) -> None:
        self._model.invalidate_cache()
        if self._current_path:
            self._model.prime_folder(self._current_path)

    def set_font_size(self, size: int) -> None:
        self._font_size = size
        self._tree.setStyleSheet(_tree_qss(size))

    def navigate_to(self, path: str):
        if self._fav_mode:
            return
        self._current_path = path
        idx = self._model.index(path)
        if idx.isValid():
            self._tree.setCurrentIndex(idx)
            self._tree.scrollTo(idx)
            self._tree.expand(idx)
        self._model.prime_folder(path)

    # ── Favorites ─────────────────────────────────────────────────────────────

    def _save_favorites(self):
        if self._settings:
            self._settings.set("favorites", sorted(self._favorites))

    def _add_favorite(self, path: str):
        self._favorites.add(path)
        self._model.set_favorites(self._favorites)
        self._save_favorites()
        if self._fav_mode:
            self._rebuild_fav_model()

    def _remove_favorite(self, path: str):
        self._favorites.discard(path)
        self._model.set_favorites(self._favorites)
        self._save_favorites()
        if self._fav_mode:
            self._rebuild_fav_model()

    # ── Always-watched folders ─────────────────────────────────────────────────

    def _save_watched(self):
        if self._settings:
            self._settings.set("watched_folders", sorted(self._watched))

    def _add_watch(self, path: str):
        self._watched.add(path)
        self._model.set_watched(self._watched)
        self._save_watched()
        self.watch_toggled.emit(path, True)

    def _remove_watch(self, path: str):
        self._watched.discard(path)
        self._model.set_watched(self._watched)
        self._save_watched()
        self.watch_toggled.emit(path, False)

    def _rebuild_fav_model(self):
        """Swap the tree to a flat QStandardItemModel of favorite paths."""
        icon_provider = QFileIconProvider()
        fav_model = QStandardItemModel(self)
        for path in sorted(self._favorites):
            item = QStandardItem(Path(path).name)
            item.setData(path, Qt.UserRole)
            item.setForeground(_COL_FAVORITE)
            item.setIcon(icon_provider.icon(QFileInfo(path)))
            item.setToolTip(path)
            item.setEditable(False)
            fav_model.appendRow(item)
        self._tree.setModel(fav_model)
        self._tree.setRootIndex(fav_model.invisibleRootItem().index())

    def _on_fav_filter_toggled(self, checked: bool):
        self._fav_mode = checked
        if self._settings:
            self._settings.set("fav_mode", checked)
        if checked:
            self._rebuild_fav_model()
        else:
            self._tree.setModel(self._model)
            self._tree.setRootIndex(self._model.index(""))
            # setModel resets column visibility — re-apply
            for col in range(1, self._model.columnCount()):
                self._tree.hideColumn(col)
            self._tree.setHeaderHidden(True)

    # ── Tree interaction ──────────────────────────────────────────────────────

    def _on_clicked(self, index: QModelIndex):
        if self._fav_mode:
            path = self._tree.model().data(index, Qt.UserRole)
        else:
            path = str(Path(self._model.filePath(index)))
        if path:
            self._current_path = path
            self._model.prime_folder(path)
            self.folder_selected.emit(path)

    def _on_context_menu(self, pos):
        index = self._tree.indexAt(pos)
        if not index.isValid():
            return

        if self._fav_mode:
            path = self._tree.model().data(index, Qt.UserRole)
        else:
            path = str(Path(self._model.filePath(index)))
        if not path:
            return

        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)

        if path in self._favorites:
            act = menu.addAction("★  Remove from Favorites")
            act.triggered.connect(lambda: self._remove_favorite(path))
        else:
            act = menu.addAction("☆  Add to Favorites")
            act.triggered.connect(lambda: self._add_favorite(path))

        menu.addSeparator()

        if path in self._watched:
            act = menu.addAction("👁  Stop Watching this Folder")
            act.triggered.connect(lambda: self._remove_watch(path))
        else:
            act = menu.addAction("👁  Always Watch this Folder")
            act.triggered.connect(lambda: self._add_watch(path))

        menu.exec(self._tree.viewport().mapToGlobal(pos))
