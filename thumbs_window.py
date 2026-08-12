"""
thumbs_window.py — Main window for ThumbsAI
Designed by: Zero  |  Built by: Jarvis

Layout:  Folder Tree  |  Thumbnail Grid
Toolbar: Path bar, Sort, Thumb Size slider, Search, Refresh
"""
from __future__ import annotations
import os
import shutil
from pathlib import Path
from datetime import datetime

from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QLineEdit,
    QHBoxLayout,
    QVBoxLayout,
    QSplitter,
    QSlider,
    QComboBox,
    QMenu,
    QSpinBox,
    QDoubleSpinBox,
    QDialog,
    QCheckBox,
    QFileDialog,
    QApplication,
    QMessageBox,
    QScrollArea,
)
from PySide6.QtGui import QAction
from PySide6.QtCore import Qt, QTimer, QSize, QMimeData, QByteArray, QEvent, Signal, QThread, QObject, QFileSystemWatcher
from PySide6.QtGui  import QIcon, QDrag, QImage, QPixmap

from theme import (
    BG,
    PAN,
    CAR,
    ACC,
    GRN,
    RED,
    MUT,
    PRI,
    SEC,
    FONT,
    FONT_SM,
    FONT_MD,
    FONT_LG,
    SIGNATURE,
)
from database     import ThumbsDB
from folder_panel import FolderPanel
from ai_console   import AIConsole
from settings     import AppSettings
from tasks_panel  import TasksPanel
from thumb_grid   import (ThumbGrid, DEFAULT_THUMB, MIN_THUMB, MAX_THUMB,
                          SUPPORTED_EXTS, FILE_TYPE_GROUPS)
import image_ops

ASSETS = Path(__file__).parent / "assets"

def _icon(filename: str) -> QIcon:
    """Load an icon from the assets folder. Returns empty QIcon if not found."""
    for name in (filename,):
        p = ASSETS / name
        if p.exists():
            return QIcon(str(p))
    return QIcon()


def _app_icon_from_b64(b64: str) -> QIcon:
    """Decode a base64 PNG icon string into a QIcon."""
    if not b64:
        return QIcon()
    import base64 as _b64
    from PySide6.QtGui import QPixmap
    try:
        data = _b64.b64decode(b64)
        px   = QPixmap()
        px.loadFromData(data)
        return QIcon(px)
    except Exception:
        return QIcon()


def _extract_exe_icon_b64(exe_path: str) -> str:
    """Extract the shell icon of an executable and return it as base64 PNG."""
    import base64 as _b64
    from PySide6.QtWidgets import QFileIconProvider
    from PySide6.QtCore    import QFileInfo, QBuffer, QByteArray
    from PySide6.QtGui     import QPixmap
    provider = QFileIconProvider()
    icon = provider.icon(QFileInfo(exe_path))
    px   = icon.pixmap(32, 32)
    ba   = QByteArray()
    buf  = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    px.save(buf, "PNG")
    buf.close()
    return _b64.b64encode(bytes(ba)).decode()


# ── Drag-reorderable icon bar ─────────────────────────────────────────────────

class _IconGroup(QFrame):
    """
    One named group of draggable icon buttons.
    Buttons can be reordered within the group by drag-and-drop.
    The group itself exposes a drag handle so the _GroupedIconBar can
    reorder groups by dragging.
    """
    order_changed = Signal(list)   # [btn_ids] emitted after reorder

    _MIME = "application/x-iconbarbtn"

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._btns:      list[tuple[str, QPushButton]] = []
        self._drag_id:   str    = ""
        self._drag_pos:  object = None

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(1)

        # Group label (hidden — kept for drag-handle logic)
        lbl = QLabel(label)
        lbl.setVisible(False)
        lbl.setFixedHeight(0)
        v.addWidget(lbl)

        # Buttons row
        self._btn_row = QHBoxLayout()
        self._btn_row.setContentsMargins(0, 0, 0, 0)
        self._btn_row.setSpacing(3)
        self._btn_row.addStretch()
        v.addLayout(self._btn_row)

        self.setStyleSheet(
            f"_IconGroup{{background:transparent;border:none;}}")

    def register(self, btn_id: str, btn: QPushButton):
        pos = len(self._btns)
        self._btns.append((btn_id, btn))
        self._btn_row.insertWidget(pos, btn)
        btn.installEventFilter(self)

    def set_order(self, order: list):
        bmap = {bid: btn for bid, btn in self._btns}
        new  = [(bid, bmap[bid]) for bid in order if bid in bmap]
        seen = {bid for bid in order}
        for bid, btn in self._btns:
            if bid not in seen:
                new.append((bid, btn))
        self._btns = new
        self._rebuild()

    def get_order(self) -> list:
        return [bid for bid, _ in self._btns]

    def _rebuild(self):
        for _, btn in self._btns:
            self._btn_row.removeWidget(btn)
            btn.setParent(None)
        for i, (_, btn) in enumerate(self._btns):
            self._btn_row.insertWidget(i, btn)
            btn.setParent(self)
            btn.show()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            for bid, btn in self._btns:
                if btn is obj:
                    self._drag_id  = bid
                    self._drag_pos = event.globalPosition().toPoint()
                    break
        elif event.type() == QEvent.MouseMove:
            if self._drag_id and self._drag_pos:
                diff = event.globalPosition().toPoint() - self._drag_pos
                if diff.manhattanLength() > 8:
                    self._start_drag(self._drag_id)
                    self._drag_id  = ""
                    self._drag_pos = None
                    return True
        elif event.type() == QEvent.MouseButtonRelease:
            self._drag_id  = ""
            self._drag_pos = None
        return False

    def _start_drag(self, btn_id: str):
        btn = next((b for bid, b in self._btns if bid == btn_id), None)
        if btn is None:
            return
        drag = QDrag(btn)
        mime = QMimeData()
        mime.setData(self._MIME, QByteArray(btn_id.encode()))
        drag.setMimeData(mime)
        drag.setPixmap(btn.grab())
        drag.setHotSpot(btn.grab().rect().center())
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(self._MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(self._MIME):
            e.acceptProposedAction()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(self._MIME):
            return
        drag_id   = e.mimeData().data(self._MIME).data().decode()
        drop_x    = e.position().x()
        insert_idx = len(self._btns)
        for i, (bid, btn) in enumerate(self._btns):
            if drop_x < btn.geometry().center().x():
                insert_idx = i
                break
        old_idx = next((i for i, (bid, _) in enumerate(self._btns)
                        if bid == drag_id), None)
        if old_idx is None:
            return
        item = self._btns.pop(old_idx)
        if insert_idx > old_idx:
            insert_idx -= 1
        self._btns.insert(insert_idx, item)
        self._rebuild()
        self.order_changed.emit(self.get_order())
        e.acceptProposedAction()


class _GroupedIconBar(QFrame):
    """
    Icon bar divided into three islands (Disk, Image, Apps).
    Icons are draggable within their group.
    Groups themselves can be dragged left/right to reorder.
    """
    order_changed = Signal(dict)   # {"disk": [...], "image": [...], "group_order": [...]}

    _GROUP_MIME = "application/x-iconbargroup"

    def __init__(self, style: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(style)
        self.setAcceptDrops(True)

        self._group_drag_id:  str    = ""
        self._group_drag_pos: object = None

        # Three groups — order is preserved in self._group_order
        self._groups: dict[str, _IconGroup] = {
            "disk":  _IconGroup("DISK", self),
            "image": _IconGroup("IMAGE", self),
            "apps":  _IconGroup("APPS", self),
        }
        self._group_order: list[str] = ["disk", "image", "apps"]
        self._right_widgets: list = []

        self._h = QHBoxLayout(self)
        self._h.setContentsMargins(8, 2, 8, 2)
        self._h.setSpacing(0)
        self._rebuild_groups()

    def _sep(self) -> QFrame:
        s = QFrame()
        s.setFrameShape(QFrame.VLine)
        s.setFixedWidth(1)
        s.setStyleSheet(f"background:{MUT};border:none;margin:4px 6px;")
        return s

    def _rebuild_groups(self):
        # Clear layout (preserve group/right widgets by re-parenting, not destroying)
        while self._h.count():
            item = self._h.takeAt(0)
            w = item.widget()
            if w and w not in self._groups.values() and w not in self._right_widgets:
                w.setParent(None)  # separators/stretch — discard

        for i, gid in enumerate(self._group_order):
            if i > 0:
                self._h.addWidget(self._sep())
            grp = self._groups[gid]
            grp.setParent(self)
            grp.show()
            self._h.addWidget(grp)
            grp.installEventFilter(self)
        self._h.addStretch(1)
        for w in self._right_widgets:
            w.setParent(self)
            w.show()
            self._h.addWidget(w)

    def register(self, group: str, btn_id: str, btn: QPushButton):
        self._groups[group].register(btn_id, btn)
        self._groups[group].order_changed.connect(
            lambda _: self.order_changed.emit(self._serialize()))

    def add_right_widget(self, w):
        self._right_widgets.append(w)
        w.setParent(self)
        w.show()
        self._h.addWidget(w)

    def set_order(self, saved: dict):
        for gid in ("disk", "image", "apps"):
            if gid in saved and saved[gid]:
                self._groups[gid].set_order(saved[gid])
        if "group_order" in saved and saved["group_order"]:
            valid = [g for g in saved["group_order"] if g in self._groups]
            if len(valid) == 3:
                self._group_order = valid
                self._rebuild_groups()

    def get_order(self) -> dict:
        return self._serialize()

    def _serialize(self) -> dict:
        d = {gid: self._groups[gid].get_order() for gid in self._groups}
        d["group_order"] = list(self._group_order)
        return d

    # ── Group-level drag ──────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        # Watch for group-level drag (clicking on the group frame, not its buttons)
        for gid, grp in self._groups.items():
            if grp is obj:
                if event.type() == QEvent.MouseButtonPress and \
                        event.button() == Qt.LeftButton:
                    self._group_drag_id  = gid
                    self._group_drag_pos = event.globalPosition().toPoint()
                elif event.type() == QEvent.MouseMove and self._group_drag_id == gid:
                    if self._group_drag_pos:
                        diff = (event.globalPosition().toPoint()
                                - self._group_drag_pos)
                        if diff.manhattanLength() > 12:
                            self._start_group_drag(gid)
                            self._group_drag_id  = ""
                            self._group_drag_pos = None
                            return True
                elif event.type() == QEvent.MouseButtonRelease:
                    self._group_drag_id  = ""
                    self._group_drag_pos = None
        return False

    def _start_group_drag(self, gid: str):
        grp  = self._groups[gid]
        drag = QDrag(grp)
        mime = QMimeData()
        mime.setData(self._GROUP_MIME, QByteArray(gid.encode()))
        drag.setMimeData(mime)
        drag.setPixmap(grp.grab())
        drag.setHotSpot(grp.grab().rect().center())
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(self._GROUP_MIME):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasFormat(self._GROUP_MIME):
            e.acceptProposedAction()

    def dropEvent(self, e):
        if not e.mimeData().hasFormat(self._GROUP_MIME):
            return
        drag_gid = e.mimeData().data(self._GROUP_MIME).data().decode()
        drop_x   = e.position().x()

        # Find which group position we're dropping at
        insert_idx = len(self._group_order)
        for i, gid in enumerate(self._group_order):
            grp = self._groups[gid]
            if drop_x < grp.geometry().center().x():
                insert_idx = i
                break

        old_idx = self._group_order.index(drag_gid)
        self._group_order.pop(old_idx)
        if insert_idx > old_idx:
            insert_idx -= 1
        self._group_order.insert(insert_idx, drag_gid)
        self._rebuild_groups()
        self.order_changed.emit(self._serialize())
        e.acceptProposedAction()


# Keep old name as alias for compatibility
_DragIconBar = _GroupedIconBar


class _FileOpWorker(QObject):
    """Runs copy or move operations on a background thread."""
    finished  = Signal(int, str)   # (count, dest_name)
    error     = Signal(str)

    def __init__(self, paths: list[str], dest: str, mode: str):
        super().__init__()
        self._paths = paths
        self._dest  = dest
        self._mode  = mode   # "copy" | "move"

    def run(self):
        op    = shutil.copy2 if self._mode == "copy" else shutil.move
        count = 0
        for p in self._paths:
            try:
                op(p, self._dest)
                count += 1
            except Exception as e:
                self.error.emit(str(e))
        self.finished.emit(count, Path(self._dest).name)


# ── ThumbsPlus Read-Only accessor ────────────────────────────────────────────

class _ThumbsPlusReader:
    """Read-only accessor for a ThumbsPlus 10 database."""

    _TP = 1627   # .tn file path divisor: dir = idThumb // 1627, file = idThumb % 1627

    def __init__(self, db_path: str):
        import sqlite3 as _sq
        self._db_path   = db_path
        self._c         = _sq.connect(f"file:{db_path}?mode=ro", uri=True)
        self._files_dir = db_path + "_files\\000\\"
        self._kw_map: dict | None = None

    def _load_keywords(self):
        if self._kw_map is not None:
            return
        self._kw_map = {}
        try:
            for kid, kname in self._c.execute(
                "SELECT tk.idThumb, k.name "
                "FROM ThumbnailKeyword tk "
                "JOIN Keyword k ON k.idKeyword = tk.idKeyword"
            ).fetchall():
                self._kw_map.setdefault(kid, []).append(kname)
        except Exception:
            pass

    def _thumbnail_for(self, id_thumb: int, inline_data) -> bytes | None:
        tn_dir  = id_thumb // self._TP
        tn_file = id_thumb % self._TP
        tn_path = Path(f"{self._files_dir}{tn_dir:03x}\\{tn_file:03x}.tn")
        if tn_path.exists():
            try:
                return tn_path.read_bytes()
            except Exception:
                pass
        return bytes(inline_data) if inline_data else None

    def images_in_folder(self, folder: str) -> list[dict]:
        """Return ThumbsAI-compatible row dicts for images in a folder."""
        self._load_keywords()
        try:
            p         = Path(folder)
            vol       = p.drive
            path_part = str(p.relative_to(p.anchor)).rstrip("\\")
            if path_part == ".":
                path_part = ""
        except Exception:
            return []
        try:
            if path_part:
                rows = self._c.execute(
                    "SELECT t.idThumb, t.name, t.width, t.height, t.rating, "
                    "       t.thumbnail, p.name AS path_name, v.netname "
                    "FROM Thumbnail t "
                    "JOIN Path p ON p.idPath = t.idPath "
                    "JOIN Volume v ON v.idVolume = p.idVolume "
                    "WHERE v.netname=? AND p.name=?",
                    (vol, path_part)).fetchall()
            else:
                rows = self._c.execute(
                    "SELECT t.idThumb, t.name, t.width, t.height, t.rating, "
                    "       t.thumbnail, p.name AS path_name, v.netname "
                    "FROM Thumbnail t "
                    "JOIN Path p ON p.idPath = t.idPath "
                    "JOIN Volume v ON v.idVolume = p.idVolume "
                    "WHERE v.netname=? AND (p.name='' OR p.name IS NULL)",
                    (vol,)).fetchall()
        except Exception:
            return []
        result = []
        for row in rows:
            id_thumb = row[0]
            name     = row[1] or ""
            tags     = ", ".join((self._kw_map or {}).get(id_thumb, []))
            result.append({
                "id": id_thumb, "filepath": folder + "\\" + name,
                "filename": name, "folder": folder,
                "width": row[2], "height": row[3],
                "filesize": None, "modified_at": None, "file_hash": None,
                "added_at": None, "prompt": None, "negative_prompt": None,
                "seed": None, "model": None, "sampler": None,
                "cfg_scale": None, "steps": None,
                "source": "ThumbsPlus", "raw_meta": None,
                "rating": int(row[4] or 0),
                "tags": tags or None,
                "thumbnail": self._thumbnail_for(id_thumb, row[5]),
            })
        return result

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass


# ── ThumbsPlus Import Worker ──────────────────────────────────────────────────

class _ThumbsPlusImportWorker(QObject):
    """Background two-phase import from ThumbsPlus into the ThumbsAI DB."""

    progress     = Signal(int, int, str)   # current, total, message
    phase_change = Signal(int, str)        # phase_num, description
    log_msg      = Signal(str)
    finished     = Signal(int, int)        # phase1_new, phase2_updated
    error        = Signal(str)

    _TP = 1627

    def __init__(self, tp_path: str, ai_db_path: str, parent=None):
        super().__init__(parent)
        self._tp_path = tp_path
        self._ai_path = ai_db_path
        self._cancel  = False

    def cancel(self):
        self._cancel = True

    def run(self):
        import sqlite3 as _sq
        from pathlib import Path as _P
        from datetime import datetime as _dt

        try:
            self._run_inner(_sq, _P, _dt)
        except Exception as e:
            import traceback
            self.error.emit(
                f"Unexpected error: {e}\n{traceback.format_exc()}")

    def _run_inner(self, _sq, _P, _dt):
        # SQLite URI requires forward slashes on Windows
        tp_uri = "file:///" + self._tp_path.replace("\\", "/") + "?mode=ro"
        self.log_msg.emit(f"Opening ThumbsPlus DB: {self._tp_path}")
        try:
            tp = _sq.connect(tp_uri, uri=True)
        except Exception as e:
            self.error.emit(f"Cannot open ThumbsPlus DB: {e}")
            return

        try:
            ai = _sq.connect(self._ai_path, check_same_thread=False, timeout=30.0)
            ai.execute("PRAGMA journal_mode=WAL")
            ai.execute("PRAGMA synchronous=OFF")        # safe for bulk import
            ai.execute("PRAGMA cache_size=-65536")      # 64 MB page cache
            ai.execute("PRAGMA temp_store=MEMORY")
            ai.execute("PRAGMA busy_timeout=5000")
        except Exception as e:
            self.error.emit(f"Cannot open ThumbsAI DB: {e}")
            tp.close()
            return

        # ── Phase 1: metadata + thumbnails ────────────────────────────────
        self.phase_change.emit(1, "Phase 1 — Importing from ThumbsPlus")

        # Introspect actual column names so the query adapts to any TP version
        try:
            thumb_cols = {r[1].lower() for r in
                          tp.execute("PRAGMA table_info(Thumbnail)").fetchall()}
            path_cols  = {r[1].lower() for r in
                          tp.execute("PRAGMA table_info(Path)").fetchall()}
            vol_cols   = {r[1].lower() for r in
                          tp.execute("PRAGMA table_info(Volume)").fetchall()}
            self.log_msg.emit(f"Thumbnail cols: {sorted(thumb_cols)}")
            self.log_msg.emit(f"Path cols:      {sorted(path_cols)}")
            self.log_msg.emit(f"Volume cols:    {sorted(vol_cols)}")
        except Exception as e:
            self.error.emit(f"Cannot read ThumbsPlus schema: {e}")
            tp.close(); ai.close()
            return

        # Resolve column names that vary across ThumbsPlus versions
        rating_col = next((c for c in ("rating", "colorclass", "rank", "stars")
                           if c in thumb_cols), None)
        width_col  = next((c for c in ("width",  "imagewidth",  "imgwidth")
                           if c in thumb_cols), None)
        height_col = next((c for c in ("height", "imageheight", "imgheight")
                           if c in thumb_cols), None)
        path_fk    = next((c for c in ("idpath", "pathid", "path_id")
                           if c in thumb_cols), None)
        path_pk    = next((c for c in ("idpath", "pathid", "path_id", "id")
                           if c in path_cols), None)
        path_name_col = next((c for c in ("name", "path", "pathname", "fullpath")
                              if c in path_cols), None)
        vol_fk     = next((c for c in ("idvol", "idvolume", "volumeid", "volume_id")
                           if c in path_cols), None)
        vol_pk     = next((c for c in ("idvol", "idvolume", "volumeid", "volume_id", "id")
                           if c in vol_cols), None)
        netname_col = next((c for c in ("netname", "label", "name", "drivepath", "path")
                            if c in vol_cols), None)

        missing = [n for n, v in [
            ("Thumbnail.rating-like", rating_col),
            ("Thumbnail.width-like",  width_col),
            ("Thumbnail.height-like", height_col),
            ("Thumbnail→Path FK",     path_fk),
            ("Path PK",               path_pk),
            ("Path.name-like",        path_name_col),
            ("Path→Volume FK",        vol_fk),
            ("Volume PK",             vol_pk),
            ("Volume.netname-like",   netname_col),
        ] if v is None]
        if missing:
            self.log_msg.emit(f"WARNING: could not map columns: {missing}")

        self.log_msg.emit("Counting ThumbsPlus records…")
        try:
            total1 = tp.execute("SELECT COUNT(*) FROM Thumbnail").fetchone()[0]
            self.log_msg.emit(f"Found {total1:,} records.")
        except Exception as e:
            self.error.emit(f"ThumbsPlus query failed: {e}")
            tp.close(); ai.close()
            return

        kw_map: dict[int, list[str]] = {}
        try:
            for kid, kname in tp.execute(
                "SELECT tk.idThumb, k.name FROM ThumbnailKeyword tk "
                "JOIN Keyword k ON k.idKeyword = tk.idKeyword"
            ).fetchall():
                kw_map.setdefault(kid, []).append(kname)
            self.log_msg.emit(f"Loaded keywords for {len(kw_map):,} items.")
        except Exception:
            self.log_msg.emit("No keyword data (table not found).")

        # Pre-load existing filepaths+ids — eliminates per-record SELECT lookups
        self.log_msg.emit("Loading existing ThumbsAI records into memory…")
        existing_map: dict[str, int] = {}  # filepath → image id
        try:
            for fp_row in ai.execute("SELECT filepath, id FROM images"):
                existing_map[fp_row[0]] = fp_row[1]
            self.log_msg.emit(f"  {len(existing_map):,} existing records cached.")
        except Exception as e:
            self.log_msg.emit(f"  (could not pre-cache: {e})")

        # ThumbsPlus stores .tn files as <DBStem>_files\<dir>\<file>.tn
        _tp_p = _P(self._tp_path)
        tp_files_base = str(_tp_p.parent / (_tp_p.stem + "_files")) + "\\"
        self.log_msg.emit(f"Thumbnail files dir: {tp_files_base}")
        now      = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        new_rec  = 0
        updated  = 0
        skipped  = 0
        BATCH    = 2000
        CKPT     = 20000   # WAL checkpoint every N records

        # Build SELECT using discovered column names; fall back to NULL if absent
        sel_rating  = f"t.{rating_col}"  if rating_col  else "NULL"
        sel_width   = f"t.{width_col}"   if width_col   else "NULL"
        sel_height  = f"t.{height_col}"  if height_col  else "NULL"
        join_path   = (f"JOIN Path p ON p.{path_pk} = t.{path_fk}"
                       if path_fk and path_pk else "")
        sel_path    = f"p.{path_name_col}" if path_name_col and join_path else "NULL"
        join_vol    = (f"JOIN Volume v ON v.{vol_pk} = p.{vol_fk}"
                       if vol_fk and vol_pk and join_path else "")
        sel_netname = f"v.{netname_col}" if netname_col and join_vol else "NULL"

        main_sql = (
            f"SELECT t.idThumb, t.name, {sel_width}, {sel_height}, {sel_rating}, "
            f"       {sel_path} AS path_name, {sel_netname} AS netname "
            f"FROM Thumbnail t "
            f"{join_path} "
            f"{join_vol}"
        )
        self.log_msg.emit(f"Query: {main_sql.strip()}")

        try:
            cursor = tp.execute(main_sql)
        except Exception as e:
            self.error.emit(f"Main query failed: {e}\nSQL: {main_sql}")
            tp.close(); ai.close()
            return
        for i, row in enumerate(cursor):
            if self._cancel:
                self.log_msg.emit("Import cancelled by user.")
                break

            try:
                id_thumb  = row[0]
                name      = row[1] or ""
                width     = row[2]
                height    = row[3]
                rating    = int(row[4] or 0)
                path_name = (row[5] or "").strip("\\")
                netname   = (row[6] or "").rstrip("\\")
            except Exception as _re:
                skipped += 1
                self.log_msg.emit(f"  Skipping row {i}: {_re}")
                continue

            if not name or not netname:
                skipped += 1
                continue

            filepath = (f"{netname}\\{path_name}\\{name}" if path_name
                        else f"{netname}\\{name}")
            folder   = str(_P(filepath).parent)
            tags     = ", ".join(kw_map.get(id_thumb, []))

            # Thumbnail: try .tn file first, fall back to inline blob only if missing
            thumb_data = None
            tn_dir  = id_thumb // self._TP
            tn_file = id_thumb % self._TP
            tn_path = _P(f"{tp_files_base}{tn_dir:03x}\\{tn_file:03x}.tn")
            if tn_path.exists():
                try:
                    thumb_data = tn_path.read_bytes()
                except Exception:
                    pass
            if thumb_data is None:
                # Only hit the DB for inline blob when .tn is absent
                inline_row = tp.execute(
                    "SELECT thumbnail FROM Thumbnail WHERE idThumb=?",
                    (id_thumb,)).fetchone()
                if inline_row and inline_row[0]:
                    thumb_data = bytes(inline_row[0])

            try:
                img_id = existing_map.get(filepath)
                if img_id is not None:
                    ai.execute(
                        "UPDATE images SET "
                        "  width=COALESCE(width,?), "
                        "  height=COALESCE(height,?), "
                        "  rating=CASE WHEN COALESCE(rating,0)=0 AND ?>0 THEN ? ELSE rating END, "
                        "  tags=CASE WHEN COALESCE(tags,'')='' AND ?<>'' THEN ? ELSE tags END "
                        "WHERE id=?",
                        (width, height, rating, rating, tags, tags, img_id))
                    if thumb_data:
                        ai.execute(
                            "INSERT INTO thumbnails(image_id,data) VALUES(?,?) "
                            "ON CONFLICT(image_id) DO NOTHING",
                            (img_id, thumb_data))
                    updated += 1
                else:
                    cur = ai.execute(
                        "INSERT OR IGNORE INTO images"
                        "(filepath,filename,folder,width,height,rating,tags,added_at)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (filepath, name, folder, width, height, rating, tags or None, now))
                    img_id = cur.lastrowid
                    if img_id and thumb_data:
                        ai.execute(
                            "INSERT OR IGNORE INTO thumbnails(image_id,data) VALUES(?,?)",
                            (img_id, thumb_data))
                    existing_map[filepath] = img_id or 0
                    new_rec += 1
            except Exception as _we:
                skipped += 1
                self.log_msg.emit(f"  Write error at row {i} ({filepath}): {_we}")

            if (i + 1) % 100 == 0:
                self.progress.emit(i + 1, total1,
                    f"Phase 1: {new_rec:,} new · {updated:,} updated · {skipped} skipped")
            if (i + 1) % BATCH == 0:
                ai.commit()
            if (i + 1) % CKPT == 0:
                # Prevent WAL from growing unbounded on spinning disk
                ai.execute("PRAGMA wal_checkpoint(PASSIVE)")

        ai.commit()
        self.log_msg.emit(
            f"Phase 1 complete — {new_rec:,} new, {updated:,} updated, {skipped} skipped.")
        self.progress.emit(total1, total1, f"Phase 1 done: {new_rec + updated:,} processed")
        tp.close()

        if self._cancel:
            ai.close()
            self.finished.emit(new_rec, 0)
            return

        # ── Phase 2: extract SD metadata from PNG files ────────────────────
        self.phase_change.emit(2, "Phase 2 — Extracting AI metadata from PNG files")
        self.log_msg.emit("Querying for PNG files without SD metadata…")

        try:
            png_rows = ai.execute(
                "SELECT id, filepath FROM images "
                "WHERE prompt IS NULL AND lower(filepath) LIKE '%.png'"
            ).fetchall()
        except Exception as e:
            self.log_msg.emit(f"Phase 2 query failed: {e}")
            ai.close()
            self.finished.emit(new_rec, 0)
            return

        total2    = len(png_rows)
        enriched  = 0
        self.log_msg.emit(f"Found {total2:,} PNG files without SD metadata.")

        from ai_metadata import parse_png_metadata

        for j, (img_id, fp) in enumerate(png_rows):
            if self._cancel:
                self.log_msg.emit("Phase 2 cancelled.")
                break
            try:
                meta = parse_png_metadata(fp)
                if not meta:
                    continue
                sets, vals = [], []
                for field in ("prompt", "negative_prompt", "seed", "model",
                              "sampler", "cfg_scale", "steps", "source", "raw_meta"):
                    if meta.get(field):
                        sets.append(f"{field}=COALESCE({field},?)")
                        vals.append(meta[field])
                if sets:
                    vals.append(img_id)
                    ai.execute(
                        f"UPDATE images SET {', '.join(sets)} WHERE id=?", vals)
                    enriched += 1
            except Exception:
                pass

            if (j + 1) % 100 == 0:
                ai.commit()
                self.progress.emit(j + 1, total2,
                    f"Phase 2: {enriched:,} enriched / {j+1:,} scanned")

        ai.commit()
        ai.close()

        self.log_msg.emit(f"Phase 2 complete — {enriched:,} files enriched with SD metadata.")
        self.progress.emit(total2, total2,
            f"Import complete — Phase 1: {new_rec:,} new · Phase 2: {enriched:,} enriched")
        self.finished.emit(new_rec, enriched)


# ── Import Progress Dialog ────────────────────────────────────────────────────

class _ImportProgressDialog(QDialog):
    """Modeless dialog that runs and monitors the two-phase ThumbsPlus import."""

    def __init__(self, tp_path: str, ai_db_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ThumbsAI — Database Import")
        self.resize(600, 400)
        self.setMinimumSize(460, 320)
        self.setStyleSheet(f"background:{BG};color:{PRI};font-family:{FONT};")
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowMinimizeButtonHint)

        self._worker: _ThumbsPlusImportWorker | None = None
        self._thread: QThread | None = None
        self._done   = False

        v = QVBoxLayout(self)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(8)

        self._phase_lbl = QLabel("Preparing import…")
        self._phase_lbl.setStyleSheet(
            f"color:{PRI};font-size:{FONT_MD}px;font-weight:bold;"
            f"background:transparent;")
        v.addWidget(self._phase_lbl)

        from PySide6.QtWidgets import QProgressBar as _PB
        self._prog = _PB()
        self._prog.setRange(0, 1000)
        self._prog.setValue(0)
        self._prog.setFixedHeight(18)
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{CAR};border:1px solid {MUT};"
            f"border-radius:4px;text-align:center;color:{PRI};"
            f"font-family:{FONT};font-size:{FONT_SM}px;}}"
            f"QProgressBar::chunk{{background:{ACC};border-radius:3px;}}")
        v.addWidget(self._prog)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        v.addWidget(self._status_lbl)

        from PySide6.QtWidgets import QTextEdit as _TE
        from PySide6.QtCore import Qt as _Qt
        self._log = _TE()
        self._log.setReadOnly(True)
        self._log.setTextInteractionFlags(
            _Qt.TextSelectableByMouse | _Qt.TextSelectableByKeyboard)
        self._log.setStyleSheet(
            f"QTextEdit{{background:{CAR};color:{SEC};"
            f"border:1px solid {MUT};border-radius:4px;"
            f"font-family:{FONT};font-size:{FONT_SM}px;padding:4px;}}")
        v.addWidget(self._log)

        btn_row_w = QWidget()
        btn_row_w.setStyleSheet("background:transparent;")
        btn_row = QHBoxLayout(btn_row_w)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(8)

        btn_copy = QPushButton("Copy Log")
        btn_copy.setFixedSize(90, 28)
        btn_copy.setStyleSheet(
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;}}"
            f"QPushButton:hover{{background:#555577;}}")
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self._log.toPlainText()))
        btn_row.addWidget(btn_copy)
        btn_row.addStretch()

        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setFixedSize(90, 28)
        self._btn_cancel.setStyleSheet(
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"font-weight:bold;}}"
            f"QPushButton:hover{{background:#555577;}}")
        self._btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._btn_cancel)

        self._btn_close = QPushButton("Close")
        self._btn_close.setFixedSize(90, 28)
        self._btn_close.setEnabled(False)
        self._btn_close.setStyleSheet(
            f"QPushButton{{background:{ACC};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"font-weight:bold;}}"
            f"QPushButton:hover{{background:#185FA5;}}"
            f"QPushButton:disabled{{background:{MUT};color:{SEC};}}")
        self._btn_close.clicked.connect(self.accept)
        btn_row.addWidget(self._btn_close)
        v.addWidget(btn_row_w)

        self._start(tp_path, ai_db_path)

    def _start(self, tp_path: str, ai_db_path: str):
        self._worker = _ThumbsPlusImportWorker(tp_path, ai_db_path)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.phase_change.connect(self._on_phase)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_msg.connect(self._on_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_phase(self, num: int, name: str):
        self._phase_lbl.setText(name)
        self._prog.setValue(0)
        self._on_log(f"\n── {name} ──")

    def _on_progress(self, cur: int, total: int, msg: str):
        if total > 0:
            self._prog.setValue(int(cur * 1000 / total))
        self._status_lbl.setText(msg)

    def _on_log(self, msg: str):
        self._log.append(msg)
        self._log.ensureCursorVisible()

    def _on_finished(self, p1: int, p2: int):
        self._done = True
        self._prog.setValue(1000)
        self._btn_cancel.setEnabled(False)
        self._btn_close.setEnabled(True)
        self._phase_lbl.setStyleSheet(
            f"color:{GRN};font-size:{FONT_MD}px;font-weight:bold;"
            f"background:transparent;")
        self._phase_lbl.setText("Import Complete")
        self._on_log(
            f"\nDone — {p1:,} records imported, {p2:,} PNG files enriched with SD metadata.")

    def _on_error(self, msg: str):
        self._on_log(f"\nError: {msg}")
        self._btn_cancel.setEnabled(False)
        self._btn_close.setEnabled(True)
        self._phase_lbl.setStyleSheet(
            f"color:{RED};font-size:{FONT_MD}px;font-weight:bold;"
            f"background:transparent;")
        self._phase_lbl.setText("Import Failed")

    def _on_cancel(self):
        if self._worker:
            self._worker.cancel()
        self._btn_cancel.setEnabled(False)
        self._on_log("Cancelling…")

    def closeEvent(self, event):
        if not self._done and self._worker:
            self._worker.cancel()
        super().closeEvent(event)


# ─────────────────────────────────────────────────────────────────────────────

class ThumbsWindow(QMainWindow):

    _bg_status = Signal(str)   # thread-safe status update from background workers

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ThumbsAI")
        self.resize(1400, 900)
        self.setMinimumSize(900, 600)
        self.setStyleSheet(f"background:{BG};")

        self._db             = ThumbsDB()
        self._settings       = AppSettings()
        self._current_folder = ""
        self._tp_reader: _ThumbsPlusReader | None = None
        self._apply_db_mode()

        # QFileSystemWatcher — detects files added/removed while app is open.
        # Tied to the "auto scan folder on click" setting.
        self._watcher        = QFileSystemWatcher(self)
        self._watcher_pending: set[str] = set()
        self._watcher_timer  = QTimer(self)
        self._watcher_timer.setSingleShot(True)
        self._watcher_timer.setInterval(500)   # 500 ms debounce
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._watcher_timer.timeout.connect(self._on_watcher_flush)

        # Polling fallback — QFileSystemWatcher does NOT fire on mapped/network drives, so
        # periodically diff the Watched Folders' file lists and route any change through the
        # same debounced scan path. Cheap (filename-only scandir); seeds silently on first run.
        self._poll_snapshot: dict[str, frozenset] = {}
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(8000)   # 8 s
        self._poll_timer.timeout.connect(self._poll_watched_folders)
        self._poll_timer.start()

        root = QWidget()
        root.setStyleSheet(f"background:{BG};")
        self.setCentralWidget(root)

        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self._build_menu()
        self._build_toolbar(v)
        self._build_main(v)
        self._build_statusbar(v)

        # Apply saved file-type filter and sort (grid now exists)
        self._apply_ext_settings()
        self._on_sort_changed()

        # Scan plugins on startup (background thread, non-blocking)
        QTimer.singleShot(500, self._grid.rescan_plugins)

        # Restore last folder after UI is fully constructed
        if self._settings.get("remember_last_folder"):
            last = self._settings.get("last_folder")
            if last:
                QTimer.singleShot(0, lambda: self._restore_folder(last))

        # Start watching always-watched folders immediately (tier 2 only at this
        # point — tier 1 is added when the first folder is selected/restored).
        QTimer.singleShot(0, lambda: self._update_watcher(self._current_folder))

        # Run DB maintenance after the window is shown so it never blocks startup.
        QTimer.singleShot(3000, self._bg_optimize)

    # ── Menu bar ──────────────────────────────────────────────────────────────

    def _build_menu(self):
        mb = self.menuBar()
        mb.setStyleSheet(f"""
            QMenuBar {{ background: {PAN}; color: {PRI}; }}
            QMenuBar::item:selected {{ background: {CAR}; }}
            QMenu {{ background: {PAN}; color: {PRI}; border: 1px solid {MUT}; }}
            QMenu::item:selected {{ background: {CAR}; }}
        """)

        # File
        file_menu = mb.addMenu("File")

        act_settings = QAction("Settings…", self)
        act_settings.setShortcut("Ctrl+,")
        act_settings.triggered.connect(self._open_settings)
        file_menu.addAction(act_settings)

        act_watched = QAction("Watched Folders…", self)
        act_watched.triggered.connect(self._open_watched_folders)
        file_menu.addAction(act_watched)

        file_menu.addSeparator()

        act_quit = QAction("Quit", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # Help
        help_menu = mb.addMenu("Help")

        act_about = QAction("About ThumbsAI", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    def _show_about(self):
        QMessageBox.about(
            self, "About ThumbsAI",
            f"<b>ThumbsAI</b><br>"
            f"AI-powered image browser<br><br>"
            f"{SIGNATURE}")

    def _open_watched_folders(self):
        dlg = _WatchedFoldersDialog(self._settings, self)
        dlg.exec()
        dlg.deleteLater()      # parented to the window — free it so dialogs don't accumulate over days
        # Sync folder panel colours and watcher regardless of how dialog closed
        self._folder_panel._model.set_watched(
            set(self._settings.get("watched_folders") or []))
        self._folder_panel._watched = set(
            self._settings.get("watched_folders") or [])
        self._update_watcher(self._current_folder)

    # ── Toolbar ───────────────────────────────────────────────────────────────

    def _build_toolbar(self, parent):
        _tb_style = f"QFrame{{background:{PAN};border-bottom:1px solid {MUT};}}"
        _dir_ss   = (
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:3px;font-size:{FONT_SM}px;padding:0;}}"
            f"QPushButton:hover{{background:{ACC};}}"
            f"QPushButton:checked{{background:{ACC};}}")
        _sort_style = (
            f"QComboBox{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"border-radius:4px;padding:1px 2px;"
            f"font-family:{FONT};font-size:{FONT_SM}px;}}"
            f"QComboBox::drop-down{{border:none;width:14px;}}"
            f"QComboBox QAbstractItemView{{background:{CAR};color:{PRI};"
            f"selection-background-color:{ACC};}}")
        _SORT_ITEMS  = ["Numeric Name", "Name", "Date", "Size", "Modified", "Rating"]
        _SORT_ITEMS2 = ["—"] + _SORT_ITEMS

        # ── Row 1: drag-reorderable icon action buttons ───────────────────────
        self._icon_bar = _DragIconBar(_tb_style, self)
        self._icon_bar.setFixedHeight(48)

        btn_make = self._mk_icon_btn(
            _icon("make selected.webp"),
            tooltip="Make Selected — Regenerate thumbnail for selected image")
        btn_make.clicked.connect(self._on_make_selected)

        btn_scan_folder = self._mk_icon_dropdown_btn(
            _icon("scan folder.webp"),
            tooltip="Scan Folder",
            actions=[("Scan Folder",       self._on_scan_folder),
                     ("Remove Orphans",    self._on_remove_orphans_folder),
                     ("Remove All Orphans", self._on_remove_orphans_disk)])

        btn_scan_disk = self._mk_icon_dropdown_btn(
            _icon("scan disk.webp"),
            tooltip="Scan Disk",
            actions=[("Scan All Folders",   self._on_scan_disk),
                     ("Remove All Orphans", self._on_remove_orphans_disk)])

        btn_batch = self._mk_icon_dropdown_btn(
            _icon("batch process.webp"),
            tooltip="Batch Process",
            actions=[("Batch Pipeline…", self._on_batch_pipeline),
                     ("Batch Save As…",  self._on_batch_save_as)])

        btn_copy_to = self._mk_icon_btn(
            _icon("copy to.png"),
            tooltip="Copy To — Copy selected images to another folder")
        btn_copy_to.clicked.connect(self._on_copy_to)

        btn_move_to = self._mk_icon_btn(
            _icon("move to.png"),
            tooltip="Move To — Move selected images to another folder")
        btn_move_to.clicked.connect(self._on_move_to)

        btn_copy_cb = self._mk_icon_btn(
            _icon("copy to clipboard.webp"),
            tooltip="Copy to Clipboard — Copy selected image to clipboard")
        btn_copy_cb.clicked.connect(self._on_copy_to_clipboard)

        btn_paste_cb = self._mk_icon_btn(
            _icon("paste from clipboard.webp"),
            tooltip="Paste from Clipboard — Save clipboard image to current folder")
        btn_paste_cb.clicked.connect(self._on_paste_from_clipboard)

        btn_similar = self._mk_glyph_btn(
            "≋",
            tooltip="Sort by Similarity — Sort images by visual similarity to selected")
        btn_similar.clicked.connect(self._on_sort_similar)

        # ── Image editing menu button ─────────────────────────────────────────
        btn_image = self._mk_icon_btn(
            _icon("Image.png"),
            tooltip="Image — Classic image editing operations")
        _img_menu = QMenu(btn_image)
        _img_menu.setStyleSheet(
            f"QMenu{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"font-family:{FONT};font-size:{FONT_SM}px;padding:2px;}}"
            f"QMenu::item{{padding:6px 16px;}}"
            f"QMenu::item:selected{{background:{ACC};}}"
            f"QMenu::separator{{height:1px;background:{MUT};margin:2px 8px;}}")
        _img_ops = [
            ("Enlarge…",           "enlarge"),
            ("Reduce…",            "reduce"),
            None,
            ("Rotate 90° CW",      "rotate_cw"),
            ("Rotate 90° CCW",     "rotate_ccw"),
            ("Rotate 180°",        "rotate_180"),
            None,
            ("Flip Vertical",      "flip_v"),
            ("Flip Horizontal",    "flip_h"),
            None,
            ("Blur…",              "blur"),
            ("Sharpen…",           "sharpen"),
            ("Color Balance…",     "color_balance"),
            ("Tint…",              "tint"),
            ("Gamma…",             "gamma"),
            ("HSLC…",              "hslc"),
            None,
            ("Denoise",            "denoise"),
            ("Fix Compression",    "fix_compression"),
        ]
        for entry in _img_ops:
            if entry is None:
                _img_menu.addSeparator()
            else:
                label, op = entry
                _img_menu.addAction(
                    label,
                    lambda _op=op: self._on_image_op(_op))
        btn_image.clicked.connect(
            lambda: _img_menu.exec(btn_image.mapToGlobal(
                btn_image.rect().bottomLeft())))

        # Register buttons with IDs so order can be saved/restored
        self._icon_bar.register("disk",  "make_selected",        btn_make)
        self._icon_bar.register("disk",  "scan_folder",          btn_scan_folder)
        self._icon_bar.register("disk",  "scan_disk",            btn_scan_disk)
        self._icon_bar.register("disk",  "copy_to",              btn_copy_to)
        self._icon_bar.register("disk",  "move_to",              btn_move_to)
        self._icon_bar.register("disk",  "copy_to_clipboard",    btn_copy_cb)
        self._icon_bar.register("disk",  "paste_from_clipboard", btn_paste_cb)
        self._icon_bar.register("image", "batch_process",        btn_batch)
        self._icon_bar.register("image", "sort_similar",         btn_similar)
        self._icon_bar.register("image", "image_ops",            btn_image)

        # Restore saved order (or use default)
        saved_order = self._settings.get("icon_bar_order") or {}
        if saved_order:
            self._icon_bar.set_order(saved_order)

        self._icon_bar.order_changed.connect(
            lambda order: self._settings.set("icon_bar_order", order))

        # Right side: app buttons
        self._app_btns_frame  = QWidget()
        self._app_btns_layout = QHBoxLayout(self._app_btns_frame)
        self._app_btns_layout.setContentsMargins(0, 0, 0, 0)
        self._app_btns_layout.setSpacing(6)
        self._icon_bar.add_right_widget(self._app_btns_frame)
        self._rebuild_app_btns()

        parent.addWidget(self._icon_bar)

        # ── Row 2: controls ───────────────────────────────────────────────────
        bar = QFrame()
        bar.setFixedHeight(36)
        bar.setStyleSheet(_tb_style)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(6)

        # ── Sort group helper ─────────────────────────────────────────────────
        def _sort_group(label: str, items: list, saved_val: str,
                        saved_dir: str) -> tuple:
            """Returns (group_widget, combo, dir_btn)."""
            grp = QWidget()
            grp.setStyleSheet("background:transparent;")
            gh  = QHBoxLayout(grp)
            gh.setContentsMargins(0, 0, 0, 0)
            gh.setSpacing(0)
            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"color:{SEC};font-family:{FONT};font-size:{FONT_SM}px;"
                f"background:transparent;padding-right:2px;")
            combo = QComboBox()
            combo.addItems(items)
            combo.setFixedHeight(24)
            combo.setFixedWidth(72)
            combo.setStyleSheet(_sort_style)
            # Restore saved value
            idx = combo.findText(saved_val, Qt.MatchFixedString | Qt.MatchCaseSensitive)
            if idx < 0:
                idx = combo.findText(saved_val.title(),
                                     Qt.MatchFixedString | Qt.MatchCaseSensitive)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            # Direction toggle ↑/↓
            dir_btn = QPushButton("↑" if saved_dir == "asc" else "↓")
            dir_btn.setFixedSize(20, 24)
            dir_btn.setCheckable(True)
            dir_btn.setChecked(saved_dir == "desc")
            dir_btn.setToolTip("Toggle ascending / descending")
            dir_btn.setStyleSheet(_dir_ss)
            dir_btn.clicked.connect(
                lambda checked, b=dir_btn: b.setText("↓" if checked else "↑"))
            gh.addWidget(lbl)
            gh.addWidget(combo)
            gh.addWidget(dir_btn)
            return grp, combo, dir_btn

        s = self._settings
        grp1, self._sort_combo,  self._sort_dir_btn  = _sort_group(
            "Sort:", _SORT_ITEMS,
            (s.get("sort1") or "Numeric Name").title(),
            s.get("sort1_dir") or "asc")
        grp2, self._sort2_combo, self._sort2_dir_btn = _sort_group(
            " then:", _SORT_ITEMS2,
            (s.get("sort2") or "—").title(),
            s.get("sort2_dir") or "asc")
        grp3, self._sort3_combo, self._sort3_dir_btn = _sort_group(
            " then:", _SORT_ITEMS2,
            (s.get("sort3") or "—").title(),
            s.get("sort3_dir") or "asc")

        for combo in (self._sort_combo, self._sort2_combo, self._sort3_combo):
            combo.currentTextChanged.connect(lambda _: self._on_sort_changed())
        for btn in (self._sort_dir_btn, self._sort2_dir_btn, self._sort3_dir_btn):
            btn.clicked.connect(lambda _: self._on_sort_changed())

        h.addWidget(grp1)
        h.addWidget(grp2)
        h.addWidget(grp3)
        h.addWidget(self._vsep())

        # ── Size group (no gap between label and slider) ──────────────────────
        sz_grp = QWidget()
        sz_grp.setStyleSheet("background:transparent;")
        szh = QHBoxLayout(sz_grp)
        szh.setContentsMargins(0, 0, 0, 0)
        szh.setSpacing(0)
        sz_lbl = QLabel("Size:")
        sz_lbl.setStyleSheet(
            f"color:{SEC};font-family:{FONT};font-size:{FONT_SM}px;"
            f"background:transparent;padding-right:0px;margin-right:0px;")
        self._size_slider = QSlider(Qt.Horizontal)
        self._size_slider.setRange(MIN_THUMB, MAX_THUMB)
        self._size_slider.setValue(DEFAULT_THUMB)
        self._size_slider.setFixedWidth(100)
        self._size_slider.setFixedHeight(20)
        self._size_slider.valueChanged.connect(self._on_size_changed)
        self._size_lbl = QLabel(f"{DEFAULT_THUMB}px")
        self._size_lbl.setFixedWidth(36)
        self._size_lbl.setStyleSheet(
            f"color:{SEC};font-family:{FONT};font-size:{FONT_SM}px;"
            f"background:transparent;padding-left:3px;")
        szh.addWidget(sz_lbl)
        szh.addWidget(self._size_slider)
        szh.addWidget(self._size_lbl)
        h.addWidget(sz_grp)

        # ── Faceted filter (folder-scoped): minimum rating + pick/reject ──────
        _fstyle = (f"QComboBox{{background:{CAR};color:{PRI};border:1px solid {MUT};border-radius:4px;"
                   f"padding:1px 6px;font-family:{FONT};font-size:{FONT_SM}px;}}")
        self._filter_rating_op = QPushButton("≥", bar); self._filter_rating_op.setCheckable(True)
        self._filter_rating_op.setFixedSize(24, 24)
        self._filter_rating_op.setToolTip("Rating match: ≥ (this many and up) / = (exactly this many)")
        self._filter_rating_op.setStyleSheet(
            f"QPushButton{{background:{CAR};color:{PRI};border:1px solid {MUT};border-radius:4px;"
            f"font-family:{FONT};font-size:{FONT_MD}px;font-weight:bold;}}"
            f"QPushButton:checked{{background:{ACC};color:white;}}")
        self._filter_rating_op.toggled.connect(self._on_rating_op_toggled)
        h.addWidget(self._filter_rating_op)
        self._filter_rating = QComboBox(bar); self._filter_rating.setFixedHeight(24)
        self._filter_rating.addItems(["★ any", "★ 1", "★ 2", "★ 3", "★ 4", "★ 5"])
        self._filter_rating.setStyleSheet(_fstyle)
        self._filter_rating.setToolTip("Filter by rating — use the ≥/= toggle for 'and up' vs exact")
        self._filter_rating.currentIndexChanged.connect(self._apply_filter)
        h.addWidget(self._filter_rating)
        self._filter_label = QComboBox(bar); self._filter_label.setFixedHeight(24)
        self._filter_label.addItems(["label: all", "✓ picks", "✗ rejects", "unlabeled"])
        self._filter_label.setStyleSheet(_fstyle)
        self._filter_label.setToolTip("Show only picks / rejects / unlabeled")
        self._filter_label.currentIndexChanged.connect(self._apply_filter)
        h.addWidget(self._filter_label)

        h.addStretch()

        # Search (pinned to the right)
        h.addWidget(self._vsep())
        self._search_edit = QLineEdit(bar)
        self._search_edit.setPlaceholderText("Search…")
        self._search_edit.setFixedWidth(200)
        self._search_edit.setFixedHeight(24)
        self._search_edit.setStyleSheet(
            f"background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"border-radius:4px;padding:2px 8px;"
            f"font-family:{FONT};font-size:{FONT_SM}px;")
        self._search_edit.returnPressed.connect(self._on_search)
        self._search_edit.textChanged.connect(self._on_search_changed)
        h.addWidget(self._search_edit)

        btn_clear = self._mk_btn("✕", MUT, min_w=24)
        btn_clear.setFixedWidth(24)
        btn_clear.setToolTip("Clear search")
        btn_clear.clicked.connect(self._clear_search)
        h.addWidget(btn_clear)

        parent.addWidget(bar)

    # ── Main area: splitter ───────────────────────────────────────────────────

    def _build_main(self, parent):
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setStyleSheet(
            f"QSplitter::handle{{background:{MUT};}}"
            f"QSplitter::handle:horizontal{{width:3px;}}")

        # Left pane: folder tree + tasks panel stacked vertically
        left_widget = QWidget()
        left_widget.setStyleSheet(f"background:{BG};")
        left_vbox = QVBoxLayout(left_widget)
        left_vbox.setContentsMargins(0, 0, 0, 0)
        left_vbox.setSpacing(0)

        self._folder_panel = FolderPanel(db=self._db, settings=self._settings)
        self._folder_panel.folder_selected.connect(self._on_folder_selected)
        self._folder_panel.refresh_clicked.connect(self._on_refresh)
        self._folder_panel.watch_toggled.connect(self._on_watch_toggled)
        self._folder_panel.files_dropped.connect(self._on_files_dropped)

        # AI console occupies the lower third of the left column (folder tree on top, draggable divider).
        self._ai_console = AIConsole(db=self._db)
        self._ai_console.set_mode(self._settings.get("ai_mode", "internal"))
        self._ai_console.set_tag_mode(bool(self._settings.get("ai_tag_merge", True)))
        self._ai_console.tag_mode_changed.connect(self._on_ai_tag_mode_changed)
        self._ai_console.find_dupes_requested.connect(self._on_ai_find_dupes)
        self._ai_console.tag_folder_requested.connect(self._on_ai_tag_folder)
        self._ai_console.fetch_requested.connect(self._on_ai_fetch)
        self._ai_console.image_dropped.connect(self._on_ai_drop_search)
        self._ai_console.ask_requested.connect(self._on_ai_ask)
        self._ai_console.stop_requested.connect(self._on_ai_stop)
        self._ai_console.mode_changed.connect(self._on_ai_mode_changed)
        self._left_vsplit = QSplitter(Qt.Vertical)
        self._left_vsplit.setStyleSheet(
            f"QSplitter::handle{{background:{MUT};}}"
            f"QSplitter::handle:vertical{{height:3px;}}")
        self._left_vsplit.addWidget(self._folder_panel)
        self._left_vsplit.addWidget(self._ai_console)
        self._left_vsplit.setStretchFactor(0, 2)     # tree ~2/3
        self._left_vsplit.setStretchFactor(1, 1)     # console ~1/3
        self._left_vsplit.setSizes([420, 210])
        left_vbox.addWidget(self._left_vsplit, stretch=1)

        self._tasks_panel = TasksPanel()
        self._tasks_panel.set_enabled(bool(self._settings.get("show_tasks_panel")))
        self._tasks_panel.close_requested.connect(self._on_tasks_panel_close)
        left_vbox.addWidget(self._tasks_panel)
        # Wire task control signals (connected after grid is created below)

        self._splitter.addWidget(left_widget)

        self._bg_status.connect(self._on_status)

        self._grid = ThumbGrid(self._db, self._settings)
        self._grid.status_changed.connect(self._on_status)
        self._grid.scan_finished.connect(self._folder_panel.refresh_colors)
        self._grid.task_list_changed.connect(self._tasks_panel.update_tasks)
        self._grid.folder_loaded.connect(self._merge_thumbsplus_rows)
        self._grid.marks_changed.connect(self._on_marks_changed)   # re-query ONLY if a filter is active
        self._tasks_panel.quit_task.connect(self._grid.cancel_task)
        self._tasks_panel.quit_all_tasks.connect(self._grid.cancel_all_tasks)
        self._splitter.addWidget(self._grid)

        self._splitter.setSizes([220, 1180])
        self._splitter.setCollapsible(0, False)
        self._splitter.setCollapsible(1, False)

        parent.addWidget(self._splitter, stretch=1)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _build_statusbar(self, parent):
        bar = QFrame()
        bar.setFixedHeight(24)
        bar.setStyleSheet(
            f"QFrame{{background:{PAN};border-top:1px solid {MUT};}}")
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 10, 0)
        self._status_lbl = QLabel("Ready", bar)
        self._status_lbl.setStyleSheet(
            f"color:{SEC};font-family:{FONT};font-size:{FONT_SM}px;background:transparent;")
        h.addWidget(self._status_lbl)
        h.addStretch()
        parent.addWidget(bar)

    @staticmethod
    def _is_drive_root(path: str) -> bool:
        """Return True for bare drive roots (C:\, F:\, etc.)."""
        p = Path(path)
        return p == p.parent

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_files_dropped(self, paths: list, target_folder: str, is_copy: bool):
        """Drag-and-drop from the grid onto a folder in the tree: copy (or Ctrl=move) the
        files, keep the DB/thumbnail cache in sync, then refresh the source view + colors."""
        import os, shutil
        target = os.path.normpath(target_folder)
        if not os.path.isdir(target):
            return
        done, skipped = 0, []
        for src in paths:
            try:
                if not os.path.isfile(src):
                    continue
                if os.path.normcase(os.path.dirname(os.path.normpath(src))) == os.path.normcase(target):
                    continue   # already in the target folder
                dest = os.path.join(target, os.path.basename(src))
                if os.path.exists(dest):
                    skipped.append(os.path.basename(src)); continue
                if is_copy:
                    shutil.copy2(src, dest)
                else:
                    shutil.move(src, dest)
                    try:
                        self._db.move_path(src, dest)   # preserve cached thumbnail/metadata
                    except Exception:
                        pass
                done += 1
            except Exception as exc:
                skipped.append(f"{os.path.basename(src)} ({exc})")
        # Refresh AFTER the drag fully unwinds: this slot runs inside the source ThumbCard's
        # drag.exec(), so reloading the grid now would destroy that card mid-drag (crash).
        from PySide6.QtCore import QTimer

        def _refresh():
            if done and not is_copy and getattr(self, "_current_folder", ""):
                self._grid.load_folder(self._current_folder)   # moved thumbs vanish from source
            try:
                self._folder_panel.refresh_colors()
            except Exception:
                pass
        QTimer.singleShot(0, _refresh)
        verb = "Copied" if is_copy else "Moved"
        msg  = f"{verb} {done} image(s) → {target}"
        if skipped:
            msg += f"  ({len(skipped)} skipped — already present / not a file)"
        try:
            self.statusBar().showMessage(msg, 6000)
        except Exception:
            pass

    def _on_folder_selected(self, path: str):
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        self._current_folder = path
        self._reset_filter_ui()
        self._grid.load_folder(path)
        # ThumbsPlus Read-Only merge runs in _merge_thumbsplus_rows, fired by the
        # grid's folder_loaded signal once the async DB load completes (so the
        # merged rows aren't overwritten by the load result).

        if self._settings.get("remember_last_folder"):
            self._settings.set("last_folder", path)
        # No auto-scan on drive roots or in Read Only mode
        if (not self._is_drive_root(path)
                and self._settings.get("auto_scan", True)
                and self._settings.get("thumbsplus_mode") != "readonly"):
            self._grid.scan_folder(path)

        # Defer watcher rebuild so the folder renders before the blocking
        # iterdir() + addPaths() calls freeze the event loop.
        QTimer.singleShot(0, lambda p=path: self._update_watcher(p))

    # ── AI console handlers ──────────────────────────────────────────────────────
    def _on_ai_mode_changed(self, mode: str):
        """Internal (in-app JoyCaption) / Jarvis-driven / Off. Leaving Internal (or choosing Off) STOPS
        our own JoyCaption server so its VRAM is freed immediately — that's what keeps two copies of
        JoyCaption (~12 GB) from sitting on a 16 GB card at once."""
        self._settings.set("ai_mode", mode)
        # Always unload OUR server when leaving internal mode (never touches Jarvis's process).
        cap = getattr(self, "_captioner", None)
        if cap is not None:
            try:
                cap.stop()
            except Exception:
                pass
        self._captioner = None          # rebuild against the new endpoint on next use
        if mode == "internal":
            self._ai_console.log("Mode: INTERNAL — ThumbsAI runs its own JoyCaption (loads on first use).")
        elif mode == "jarvis":
            self._ai_console.log("Mode: JARVIS — reuses the Suite's caption server "
                                 "(start Jarvis + load JoyCaption). Ours is unloaded.")
        else:
            self._ai_console.log("Mode: OFF — JoyCaption unloaded, VRAM freed. "
                                 "Find dupes still works (it needs no model).")

    def _on_ai_tag_mode_changed(self, merge: bool):
        """Add/Replace dropdown flipped — persist it (same key as Settings ▸ AI 'Merge/Override')."""
        self._settings.set("ai_tag_merge", bool(merge))
        if merge:
            self._ai_console.log("Tag mode: ADD — new tags merge into existing; a re-run skips "
                                 "already-tagged images (resumes a big folder).")
        else:
            self._ai_console.log("Tag mode: REPLACE — Tag folder re-tags every image and OVERWRITES "
                                 "its tags.")

    def _on_ai_stop(self):
        """Request cancellation of the running AI job (workers poll this flag)."""
        self._ai_cancel = True
        w = getattr(self, "_ai_worker", None)
        if w is not None and w.isRunning():
            w.cancel()
        self._ai_console.log("Stopping…")

    def _ai_busy(self) -> bool:
        w = getattr(self, "_ai_worker", None)
        if w is not None and w.isRunning():
            self._ai_console.log("A job is already running — Stop it first.")
            return True
        return False

    def _ai_ready(self) -> bool:
        """Gate for the model-backed actions (tag / fetch). Off = refuse without loading anything, and
        warn if the Suite's JoyCaption is ALREADY resident (loading ours too would double the VRAM)."""
        if (self._settings.get("ai_mode", "internal") or "").lower() == "off":
            self._ai_console.log("AI is OFF — set the mode to Internal or Jarvis first. "
                                 "(Find dupes works with AI off.)")
            return False
        if getattr(self, "_captioner", None) is None:
            from caption_backend import Captioner
            self._captioner = Captioner(self._settings)
        if self._captioner.mode() == "internal" and self._captioner.jarvis_server_up():
            self._ai_console.log("NOTE: Jarvis's JoyCaption is already loaded. Switch to Jarvis mode to "
                                 "reuse it instead of loading a second copy into VRAM.")
        return True

    def _on_ai_find_dupes(self):
        folder = getattr(self, "_current_folder", "")
        if not folder:
            self._ai_console.log("Pick a folder first."); return
        if self._ai_busy():
            return
        self._ai_cancel = False
        self._ai_console.set_busy(True, "dupes")
        self._ai_console.log(f"Scanning {folder} for duplicates…")
        from ai_worker import DupeWorker
        self._ai_worker = DupeWorker(self._db, folder, recursive=False, threshold=5)
        self._ai_worker.progress.connect(self._ai_console.set_progress)
        self._ai_worker.log.connect(self._ai_console.log)
        self._ai_worker.done.connect(self._on_dupes_found)
        self._ai_worker.start()

    def _on_dupes_found(self, groups):
        self._ai_console.set_busy(False)
        if self._ai_cancel:
            self._ai_console.log("Cancelled."); return
        if not groups:
            self._ai_console.log("No duplicates found."); return
        n_imgs = sum(len(g["paths"]) for g in groups)
        n_exact = sum(len(g["exact"]) for g in groups)
        self._ai_console.log(f"Found {len(groups)} group(s), {n_imgs} images "
                             f"({n_exact} byte-identical).")
        import ai_ops
        flagged = ai_ops.mark_duplicate_rejects(self._db, groups)
        self._ai_console.log(f"Flagged {flagged} extra(s) as Reject — review in the grid, then cull.")
        if getattr(self, "_current_folder", ""):
            self._grid.load_folder(self._current_folder)

    @staticmethod
    def _fmt_dur(secs):
        if secs < 90:
            return f"~{int(secs)}s"
        if secs < 5400:
            return f"~{secs/60:.0f} min"
        return f"~{secs/3600:.1f} hours"

    def _refresh_tag_aliases(self):
        """Push the user's Settings ▸ AI alias-map text into ai_ops so the next tag/fetch pass uses it
        (layered over the built-in booru aliases). Cheap; safe to call before every AI operation."""
        try:
            import ai_ops
            ai_ops.set_user_aliases(ai_ops.parse_aliases(self._settings.get("ai_tag_aliases", "")))
        except Exception:
            pass

    def _on_ai_tag_folder(self):
        if self._ai_busy():
            return
        # Scope: the SELECTED images if any are selected, else the whole current folder.
        sel = self._grid.selected_paths()
        if sel:
            rows = [self._db.get(p) or {"filepath": p, "tags": ""} for p in sel]
            scope = f"{len(sel)} selected image(s)"
        else:
            folder = getattr(self, "_current_folder", "")
            if not folder:
                self._ai_console.log("Pick a folder, or select images, first."); return
            rows = self._db.filter_images(folder=folder, with_thumbnails=False, limit=200000)
            if not rows:
                self._ai_console.log("No indexed images here — scan the folder first."); return
            scope = "this folder"
        # Write mode (Settings ▸ AI ▸ "Merge … / OVERRIDE") decides what a SECOND run does:
        #   Merge    → skip images that already have tags, so a re-run RESUMES a big folder (additive,
        #              work saved as it goes).
        #   Override → re-tag EVERY image and REPLACE its tags — regenerate the whole folder after you
        #              change the persona or alias map. A second run here DELIBERATELY overrides.
        merge = bool(self._settings.get("ai_tag_merge", True))
        if merge:
            paths = [r["filepath"] for r in rows if not (r.get("tags") or "").strip()]
            if not paths:
                self._ai_console.log("Nothing to tag — every image here already has tags.  "
                                     "(Uncheck 'Merge' in Settings ▸ AI to re-tag / override.)"); return
        else:
            paths = [r["filepath"] for r in rows]
            if not paths:
                self._ai_console.log("No indexed images here — scan the folder first."); return
        noun = "untagged image(s)" if merge else "image(s) (OVERRIDE — replaces existing tags)"
        # Pre-flight: warn + estimate before a big job (JoyCaption is ~3 s/image, one at a time).
        if len(paths) > 300:
            est = self._fmt_dur(len(paths) * 3)
            tail = ("tagged images are saved and will be skipped if you run it again."
                    if merge else "this REPLACES the current tags on every image in scope.")
            m = QMessageBox(self)
            m.setWindowTitle("Auto-tag")
            m.setText(f"Tag {len(paths)} {noun} in {scope}?\n\n"
                      f"JoyCaption runs one at a time — roughly {est}. You can Stop anytime; {tail}")
            m.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            m.setStyleSheet(f"QMessageBox{{background:{BG};color:{PRI};}}QLabel{{color:{PRI};}}")
            if m.exec() != QMessageBox.Yes:
                self._ai_console.log("Tagging cancelled."); return
        if not self._ai_ready():
            return
        self._refresh_tag_aliases()          # pick up any Settings ▸ AI alias-map edits before this run
        self._ai_cancel = False
        self._ai_console.set_busy(True, "tag")
        self._ai_console.log(f"Auto-tagging {len(paths)} {noun}  ({self._fmt_dur(len(paths)*3)})…")
        from ai_worker import CaptionWorker
        self._ai_worker = CaptionWorker(self._db, self._captioner, paths, merge=merge)
        self._ai_worker.progress.connect(self._ai_console.set_progress)
        self._ai_worker.log.connect(self._ai_console.log)
        self._ai_worker.caption.connect(lambda name, tags: self._ai_console.log(f"  {name}: {tags}"))
        self._ai_worker.done.connect(self._on_captions_done)
        self._ai_worker.start()

    def _on_captions_done(self, n):
        self._ai_console.set_busy(False)
        if self._ai_cancel:
            self._ai_console.log(f"Cancelled — {n} tagged before stop.");
        else:
            self._ai_console.log(f"Done — tagged {n} image(s); now searchable by content.")
        if getattr(self, "_current_folder", ""):
            self._grid.load_folder(self._current_folder)

    def _on_ai_fetch(self):
        """Caption the SELECTED image, then search the library for others sharing its top concepts."""
        sel = self._grid.selected_paths()
        if not sel:
            self._ai_console.log("Select an image (or drop one on the console), then Fetch similar."); return
        self._start_fetch(sel[0])

    def _on_ai_drop_search(self, path):
        """An image was dropped on the console — search the library by it (may be an OUTSIDE file)."""
        self._start_fetch(path)

    def _start_fetch(self, path):
        """Caption `path` (grid selection or a dropped file) on a worker, then search by its concepts."""
        if self._ai_busy():
            return
        import os
        if not os.path.isfile(path):
            self._ai_console.log(f"Not a file: {path}"); return
        self._ai_last_image = path            # remember for the Ask box when nothing is grid-selected
        if not self._ai_ready():
            return
        self._refresh_tag_aliases()          # fetch derives concepts via _clean_tags → honour aliases too
        self._ai_cancel = False
        self._ai_console.set_busy(True, "fetch")
        self._ai_console.set_progress(0, 0)
        self._ai_console.log(f"Fetching images similar to {os.path.basename(path)}…")
        from ai_worker import FetchWorker
        self._ai_worker = FetchWorker(self._captioner, path)
        self._ai_worker.log.connect(self._ai_console.log)
        self._ai_worker.done.connect(self._on_fetch_done)
        self._ai_worker.start()

    @staticmethod
    def _looks_like_search(text: str) -> bool:
        """A console line is a library SEARCH (not a question about an image) if it opens with a search
        verb ('show me…', 'find…', 'search…') or contains a 'pictures of …' phrase."""
        import re
        return bool(
            re.match(r"\s*(please\s+)?(show|find|search|display|list|pull\s*up|get\s*me|look\s*for|"
                     r"give\s*me)\b", text, re.I)
            or re.search(r"\b(pictures?|images?|photos?|pics?|shots?)\s+(of|with|showing|containing)\b",
                         text, re.I))

    def _nl_to_query(self, text: str) -> str:
        """Turn a natural-language request into a tag query, e.g.
        'show me pictures of a blonde woman in a park' -> '1girl blonde park'. Strips the search
        lead-in, drops stopwords, and normalizes each remaining word to the SAME booru/alias vocabulary
        the tagger used (so 'woman' finds '1girl'-tagged images)."""
        import re
        import ai_ops
        t = re.sub(r"^\s*(please\s+)?(show me|show|find|search(\s+for)?|pull\s*up|get\s*me|display|"
                   r"list|give\s*me|look\s*for)\b[:,]?\s*", "", text.strip(), flags=re.I)
        t = re.sub(r"^\s*(all\s+)?(the\s+)?(pictures?|images?|photos?|pics?|shots?|thumbnails?)\s+"
                   r"(of|with|showing|containing|that\s+\w+)\s+", "", t, flags=re.I)
        stop = {"a", "an", "the", "of", "with", "in", "on", "at", "and", "or", "some", "any", "that",
                "this", "these", "those", "is", "are", "showing", "me", "all", "her", "his", "their"}
        terms, seen = [], set()
        for w in re.split(r"[\s,]+", t):
            n = ai_ops._normalize_tag(w)
            if not n or n in stop or n in seen:
                continue
            seen.add(n)
            terms.append(n)
        return " ".join(terms)

    def _run_library_search(self, prompt: str):
        """Search the tagged library from a console line, mirroring what the search box does."""
        self._refresh_tag_aliases()                     # search vocabulary must match the tag vocabulary
        query = self._nl_to_query(prompt)
        if not query:
            self._ai_console.log("Nothing to search for — try 'show me pictures of a blonde woman in a "
                                 "park'.  (Select an image first to ask JoyCaption ABOUT it.)")
            return
        self._ai_console.log(f"Search → {query}")
        self._search_edit.blockSignals(True)
        self._search_edit.setText(query)
        self._search_edit.blockSignals(False)
        self._grid.search(query)

    def _on_ai_ask(self, prompt: str):
        """Console text box, two jobs in one:
          • SEARCH — 'show me pictures of …' (or any line when no image is selected) searches the library.
          • ASK    — a question with an image selected is put to JoyCaption ABOUT that image (read-only,
                     never writes tags)."""
        if self._ai_busy():
            self._ai_console.log("Busy — wait for the current job (or Stop it) first."); return
        import os
        sel = self._grid.selected_paths()
        img = sel[0] if sel else getattr(self, "_ai_last_image", "")
        have_img = bool(img and os.path.isfile(img))
        # Route to library search on search phrasing, or whenever there's no image to interrogate.
        if self._looks_like_search(prompt) or not have_img:
            self._run_library_search(prompt)
            return
        # Otherwise it's a question about the selected/last image → ask JoyCaption.
        if not self._ai_ready():
            return
        self._ai_cancel = False
        self._ai_console.set_busy(True, "ask")
        self._ai_console.set_progress(0, 0)
        self._ai_console.log(f"You → {prompt}")
        self._ai_console.log(f"  (about {os.path.basename(img)})")
        from ai_worker import AskWorker
        self._ai_worker = AskWorker(self._captioner, prompt, img)
        self._ai_worker.log.connect(self._ai_console.log)
        self._ai_worker.done.connect(self._on_ask_done)
        self._ai_worker.start()

    def _on_ask_done(self, text: str):
        self._ai_console.set_busy(False)
        self._ai_console.log(f"JoyCaption → {text}" if text else "JoyCaption → (no reply)")

    def _on_fetch_done(self, text):
        self._ai_console.set_busy(False)
        if self._ai_cancel or not text:
            self._ai_console.log("Fetch cancelled or nothing to match."); return
        import ai_ops
        tags = ai_ops._clean_tags(text)
        if not tags:
            self._ai_console.log("Couldn't derive concepts from that image."); return
        # top 3 concepts (subject-first) AND-ed — a focused 'more like this'; editable in the search box
        query = " ".join(tags[:3])
        self._ai_console.log(f"Similar to: {query}")
        self._search_edit.blockSignals(True)
        self._search_edit.setText(query)
        self._search_edit.blockSignals(False)
        self._grid.search(query)

    def _reset_filter_ui(self):
        """Drop the rating/label facets back to 'all' (called when the folder changes)."""
        for cb in (getattr(self, "_filter_rating", None), getattr(self, "_filter_label", None)):
            if cb is not None:
                cb.blockSignals(True); cb.setCurrentIndex(0); cb.blockSignals(False)

    def _on_marks_changed(self):
        """A pick/reject/rating changed. Re-apply the filter ONLY if one is active — otherwise the in-place
        badge update is enough, and a full folder reload (which flickers the whole grid) is avoided."""
        if self._filter_rating.currentIndex() != 0 or self._filter_label.currentIndex() != 0:
            self._apply_filter()

    def _on_rating_op_toggled(self, checked: bool):
        """Flip the rating comparator ≥ <-> = and re-apply (only matters when a rating is selected)."""
        self._filter_rating_op.setText("=" if checked else "≥")
        if self._filter_rating.currentIndex() != 0:
            self._apply_filter()

    def _apply_filter(self):
        """Re-display the current folder filtered by the rating + pick/reject facets (folder-scoped)."""
        folder = getattr(self, "_current_folder", "")
        if not folder:
            return
        ri = self._filter_rating.currentIndex()                     # 0=any .. 5
        op = "=" if self._filter_rating_op.isChecked() else ">="    # exact vs 'and up'
        label = (None, 1, 2, 0)[self._filter_label.currentIndex()]  # all / picks / rejects / unlabeled
        if ri == 0 and label is None:
            self._grid.load_folder(folder)                          # no facets -> normal folder view
        else:
            self._grid.filter_view(rating_min=ri, rating_op=op, label=label)

    def _merge_thumbsplus_rows(self, folder: str):
        """Merge ThumbsPlus images not already in ThumbsAI (Read Only mode).
        Fired by ThumbGrid.folder_loaded once the async folder load completes,
        so the appended rows are not overwritten by the load result."""
        if (self._settings.get("thumbsplus_mode") != "readonly"
                or self._tp_reader is None):
            return
        ai_paths = set(self._db.cached_filepaths(folder).keys())
        tp_rows  = self._tp_reader.images_in_folder(folder)
        extra    = [r for r in tp_rows if r["filepath"] not in ai_paths]
        if extra:
            self._grid.append_rows(extra)

    def _on_refresh(self):
        self._grid.refresh()

    def _on_sort_changed(self):
        s1  = self._sort_combo.currentText().lower()
        d1  = "desc" if self._sort_dir_btn.isChecked()  else "asc"
        s2  = self._sort2_combo.currentText().lower()
        d2  = "desc" if self._sort2_dir_btn.isChecked() else "asc"
        s3  = self._sort3_combo.currentText().lower()
        d3  = "desc" if self._sort3_dir_btn.isChecked() else "asc"
        s2n = "" if s2 == "—" else s2
        s3n = "" if s3 == "—" else s3
        # Persist
        self._settings.set("sort1",     s1)
        self._settings.set("sort1_dir", d1)
        self._settings.set("sort2",     s2n)
        self._settings.set("sort2_dir", d2)
        self._settings.set("sort3",     s3n)
        self._settings.set("sort3_dir", d3)
        self._grid.set_sort(s1, d1, s2n, d2, s3n, d3)

    def _on_size_changed(self, value: int):
        self._size_lbl.setText(f"{value}px")
        self._grid.set_thumb_size(value)

    def _on_search(self):
        q = self._search_edit.text().strip()
        if q:
            self._grid.search(q)
        else:
            self._clear_search()

    def _on_search_changed(self, text: str):
        if not text.strip():
            self._clear_search()

    def _clear_search(self):
        self._search_edit.blockSignals(True)
        self._search_edit.clear()
        self._search_edit.blockSignals(False)
        if self._current_folder:
            self._grid.load_folder(self._current_folder)

    def _on_status(self, msg: str):
        self._status_lbl.setText(msg)

    def _on_batch_pipeline(self):
        paths = self._grid.selected_paths()
        if not paths:
            self._on_status("No images selected")
            return
        n, folder = image_ops.show_batch_pipeline(paths, self)
        if n:
            word = "file" if n == 1 else "files"
            self._on_status(f"Batch Pipeline: {n} {word} processed")
            self._grid.make_thumbs_for(paths)

    def _on_batch_save_as(self):
        paths = self._grid.selected_paths()
        if not paths:
            self._on_status("No images selected")
            return
        n, folder = image_ops.batch_save_as(paths, self)
        if n:
            word = "file" if n == 1 else "files"
            self._on_status(f"Batch Save As: {n} {word} saved to {folder}")

    def _on_copy_to(self):
        """Copy selected images to a chosen folder (background thread)."""
        paths = self._grid.selected_paths()
        if not paths:
            self._on_status("No images selected")
            return
        dest = QFileDialog.getExistingDirectory(self, "Copy To…")
        if not dest:
            return
        self._on_status(f"Copying {len(paths)} file(s)…")
        self._start_file_op(paths, dest, "copy")

    def _on_move_to(self):
        """Move selected images to a chosen folder (background thread)."""
        paths = self._grid.selected_paths()
        if not paths:
            self._on_status("No images selected")
            return
        dest = QFileDialog.getExistingDirectory(self, "Move To…")
        if not dest:
            return
        self._on_status(f"Moving {len(paths)} file(s)…")
        self._start_file_op(paths, dest, "move")

    def _start_file_op(self, paths: list[str], dest: str, mode: str):
        worker = _FileOpWorker(paths, dest, mode)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.error.connect(self._on_status)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        label = "Copied" if mode == "copy" else "Moved"
        folder = self._current_folder
        def _done(count: int, dest_name: str):
            self._on_status(f"{label} {count} file(s) to {dest_name}")
            if mode == "move" and count and folder:
                self._grid.load_folder(folder)
        worker.finished.connect(_done)
        thread.start()

    def _on_copy_to_clipboard(self):
        """Copy the first selected image to the system clipboard."""
        paths = self._grid.selected_paths()
        if not paths:
            self._on_status("No image selected")
            return
        img = QImage(paths[0])
        if img.isNull():
            self._on_status("Could not load image for clipboard")
            return
        QApplication.clipboard().setImage(img)
        self._on_status(f"Copied {Path(paths[0]).name} to clipboard")

    def _on_paste_from_clipboard(self):
        """Save clipboard image as PNG into the current folder, then refresh."""
        if not self._current_folder:
            self._on_status("No folder selected")
            return
        img = QApplication.clipboard().image()
        if img.isNull():
            self._on_status("Clipboard has no image")
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest  = Path(self._current_folder) / f"paste_{stamp}.png"
        if img.save(str(dest)):
            self._on_status(f"Saved clipboard image → {dest.name}")
            self._grid.scan_folder(self._current_folder)
        else:
            self._on_status("Failed to save clipboard image")

    def _on_sort_similar(self):
        paths = self._grid.selected_paths()
        if not paths:
            self._on_status("Select a reference image first, then click Sort Similar")
            return
        self._grid.sort_similar(paths[0])

    def _on_image_op(self, op_name: str):
        paths = self._grid.selected_paths()
        if not paths:
            self._on_status("Select one or more images first")
            return
        preserve = bool(self._settings.get("preserve_metadata_on_edit", True))
        n = image_ops.apply_op(op_name, paths, preserve, self)
        if n:
            word = "image" if n == 1 else "images"
            self._on_status(f"{op_name.replace('_', ' ').title()}: modified {n} {word}")
            # Regenerate thumbnails for the affected files
            self._grid.make_thumbs_for(paths)
        elif n == 0 and op_name not in ("enlarge", "reduce", "blur", "sharpen",
                                         "color_balance", "tint", "gamma", "hslc"):
            # Non-dialog op — something went wrong
            self._on_status("Image operation failed — check that files are writable")

    def _on_make_selected(self):
        self._grid.make_selected_thumb()

    def _on_scan_folder(self):
        if not self._current_folder:
            self._on_status("No folder selected")
            return
        self._grid.scan_folder(self._current_folder)

    def _on_remove_orphans_folder(self):
        if not self._current_folder:
            self._on_status("No folder selected")
            return
        self._on_status("Removing orphans…")
        folder = self._current_folder
        def _run():
            removed = self._db.delete_missing(folder)
            word = "entry" if removed == 1 else "entries"
            # Fast path only here (convert_if_needed=False): a quick folder cleanup must NOT trigger the
            # one-time whole-DB VACUUM. Space is reclaimed incrementally once the DB has been converted
            # (which happens on the deliberate "Remove All Orphans" sweep below).
            freed = self._db.reclaim(convert_if_needed=False) if removed else 0
            msg = f"Removed {removed} orphan DB {word}"
            if freed:
                msg += f" · reclaimed {freed/1048576:.0f} MB"
            self._bg_status.emit(msg)
            if removed:
                QTimer.singleShot(0, self._grid.refresh)
        import threading
        threading.Thread(target=_run, daemon=True).start()

    def _on_scan_disk(self):
        folders = self._db.all_folders()
        self._grid.scan_all_folders(folders)

    def _on_remove_orphans_disk(self):
        # Whole-database orphan sweep: every folder is checked and any row whose file no longer
        # exists on disk is deleted. Destructive across the ENTIRE library, so confirm first — and
        # warn that a currently-offline/unmounted drive (e.g. a disconnected NAS) reads as "missing"
        # and would have all its entries removed.
        from PySide6.QtWidgets import QMessageBox
        nf = len(self._db.all_folders())
        mb = QMessageBox(self)
        mb.setIcon(QMessageBox.Warning)
        mb.setWindowTitle("Remove All Orphans")
        mb.setText(f"Remove orphaned entries across the entire database ({nf} folders)?")
        mb.setInformativeText(
            "Any indexed file that is not found on disk right now will be deleted from the database.\n\n"
            "⚠  Files on drives that are currently offline or unmounted (e.g. a disconnected NAS) count "
            "as missing and WILL be removed. Only proceed if those drives are connected — or if you want "
            "those entries gone. Re-scanning restores them if the drive comes back.")
        mb.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        mb.setDefaultButton(QMessageBox.Cancel)
        if mb.exec() != QMessageBox.Yes:
            self._on_status("Remove All Orphans cancelled")
            return
        self._on_status("Scanning all folders for orphans…")
        def _run():
            folders = self._db.all_folders()
            nf      = len(folders)
            total   = 0
            for i, f in enumerate(folders, 1):
                total += self._db.delete_missing(f)
                if i % 10 == 0:
                    self._bg_status.emit(
                        f"Orphan scan: {i}/{nf} folders checked, {total} removed so far…")
            word = "entry" if total == 1 else "entries"
            # Reclaim disk unconditionally: the sweep is the deliberate maintenance action, so this is where
            # a legacy DB gets its one-time convert-to-incremental + VACUUM (also shrinks any pre-existing
            # free space). May take a minute on a large DB; it runs on this background thread so the UI is free.
            self._bg_status.emit("Reclaiming disk space (one-time compaction may take a minute)…")
            freed = self._db.reclaim()
            msg = f"Removed {total} orphan DB {word} across {nf} folder{'s' if nf != 1 else ''}"
            if freed:
                gb = freed / 1073741824
                msg += (f" · reclaimed {gb:.2f} GB" if gb >= 1 else f" · reclaimed {freed/1048576:.0f} MB")
            self._bg_status.emit(msg)
            if total or freed:
                QTimer.singleShot(0, self._grid.refresh)
        import threading
        threading.Thread(target=_run, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _vsep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        sep.setStyleSheet(f"background:{MUT};border:none;")
        return sep

    def _mk_icon_btn(self, icon: QIcon, tooltip: str = "") -> QPushButton:
        """Square icon-only button sized to fill the toolbar row."""
        btn = QPushButton()
        btn.setIcon(icon)
        btn.setIconSize(QSize(34, 34))
        btn.setFixedSize(40, 40)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:6px;padding:0;}}"
            f"QPushButton:hover{{background:{ACC};}}"
            f"QPushButton:pressed{{background:#185FA5;}}")
        return btn

    def _mk_icon_dropdown_btn(self, icon: QIcon, tooltip: str,
                               actions: list) -> QPushButton:
        """Square icon-only dropdown button."""
        btn = QPushButton()
        btn.setIcon(icon)
        btn.setIconSize(QSize(34, 34))
        btn.setFixedSize(40, 40)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            f"QPushButton{{background:{MUT};border:none;border-radius:4px;padding:0;}}"
            f"QPushButton:hover{{background:{ACC};}}"
            f"QPushButton:pressed{{background:#185FA5;}}")
        menu = QMenu(btn)
        menu.setStyleSheet(
            f"QMenu{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"font-family:{FONT};font-size:{FONT_SM}px;padding:2px;}}"
            f"QMenu::item{{padding:6px 16px;}}"
            f"QMenu::item:selected{{background:{ACC};}}")
        for label, slot in actions:
            menu.addAction(label, slot)
        btn.clicked.connect(
            lambda: menu.exec(btn.mapToGlobal(btn.rect().bottomLeft())))
        return btn

    def _mk_glyph_btn(self, glyph: str, tooltip: str = "") -> QPushButton:
        """Square glyph button matching icon button size."""
        btn = QPushButton(glyph)
        btn.setFixedSize(40, 40)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:6px;"
            f"padding:0;font-size:20px;color:{PRI};}}"
            f"QPushButton:hover{{background:{ACC};}}"
            f"QPushButton:pressed{{background:#185FA5;}}")
        return btn

    def _mk_btn(self, text: str, color: str, min_w: int = 80) -> QPushButton:
        btn = QPushButton(text)
        btn.setMinimumWidth(min_w)
        btn.setFixedHeight(26)
        hover = {ACC: "#185FA5", MUT: "#555577"}.get(color, "#555577")
        btn.setStyleSheet(
            f"QPushButton{{background:{color};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"font-weight:bold;padding:0 8px;}}"
            f"QPushButton:hover{{background:{hover};}}")
        return btn

    def _apply_ext_settings(self):
        disabled = set(self._settings.get("disabled_extensions") or [])
        enabled  = set(SUPPORTED_EXTS) - disabled
        self._grid.set_enabled_extensions(enabled)

    def _open_settings(self):
        dlg = _SettingsDialog(self._settings, self)
        accepted = dlg.exec() == QDialog.Accepted
        dlg.deleteLater()      # parented to the window — free it so dialogs don't accumulate over days
        if accepted:
            self._apply_ext_settings()
            self._rebuild_app_btns()
            self._tasks_panel.set_enabled(
                bool(self._settings.get("show_tasks_panel")))
            self._apply_db_mode()
            self._apply_font_settings()
            self._grid.rescan_plugins()
            self._update_watcher(self._current_folder)
            # Keep the console's Add/Replace dropdown in step with the Settings ▸ AI 'Merge' toggle.
            self._ai_console.set_tag_mode(bool(self._settings.get("ai_tag_merge", True)))

    def _apply_font_settings(self):
        self._folder_panel.set_font_size(
            int(self._settings.get("font_folder") or 9))
        self._grid.set_font_image(
            int(self._settings.get("font_image") or 9))
        self._grid.set_font_meta(
            int(self._settings.get("font_meta") or 9))
        self._grid.set_pool_buffer(
            int(self._settings.get("pool_buffer") or 4))

    def _bg_optimize(self):
        """Run PRAGMA optimize + WAL checkpoint on a background thread."""
        import threading
        threading.Thread(target=self._db.optimize, daemon=True).start()

    # ── Filesystem watcher ────────────────────────────────────────────────────

    _MAX_WATCH_PATHS = 120   # cap to avoid blocking addPaths() on deep trees

    def _update_watcher(self, folder: str):
        """Rebuild the watched path list.

        Tier 1 — current folder + its immediate subdirs (active browsing).
        Tier 2 — user-configured always-watched folders + their immediate subdirs.
        Clears and disables everything when auto_scan is off or in read-only mode.
        """
        existing = self._watcher.directories()
        if existing:
            self._watcher.removePaths(existing)
        self._watcher_pending.clear()
        self._watcher_timer.stop()

        # Read-Only mode disables all watching. Auto-scan-on-click only gates Tier 1
        # (the current folder); explicitly-marked Watched Folders (Tier 2) are always
        # monitored — that is the whole point of marking them.
        readonly  = self._settings.get("thumbsplus_mode") == "readonly"
        if readonly:
            return
        auto_scan = self._settings.get("auto_scan", True)

        to_watch: list[str] = []

        def _add_with_subdirs(root: str):
            if len(to_watch) >= self._MAX_WATCH_PATHS:
                return
            if not root or not os.path.isdir(root) or root in to_watch:
                return
            to_watch.append(root)
            try:
                with os.scandir(root) as it:
                    for entry in it:
                        if len(to_watch) >= self._MAX_WATCH_PATHS:
                            break
                        if entry.is_dir(follow_symlinks=False):
                            s = entry.path
                            if s not in to_watch:
                                to_watch.append(s)
            except OSError:
                pass

        # Tier 1: current folder (never watch a bare drive root) — only with auto-scan-on-click
        if auto_scan and not self._is_drive_root(folder):
            _add_with_subdirs(folder)

        # Tier 2: always-watched folders — monitored regardless of auto_scan
        for wf in (self._settings.get("watched_folders") or []):
            if len(to_watch) >= self._MAX_WATCH_PATHS:
                break
            _add_with_subdirs(wf)

        if to_watch:
            self._watcher.addPaths(to_watch)

    def _on_watch_toggled(self, _path: str, _active: bool):
        """Folder panel changed the always-watch list — rebuild watcher."""
        self._update_watcher(self._current_folder)

    def _on_dir_changed(self, path: str):
        """Called by QFileSystemWatcher when a watched directory changes."""
        # Defer subdirectory discovery to the flush so the main thread isn't
        # blocked iterating a large directory on every fs event.
        self._watcher_pending.add(path)
        self._watcher_timer.start()   # restart debounce window

    def _on_watcher_flush(self):
        """Triggered after 500 ms of quiet — scans every folder that changed."""
        if not self._watcher_pending:
            return
        pending  = set(self._watcher_pending)
        self._watcher_pending.clear()
        readonly = self._settings.get("thumbsplus_mode") == "readonly"
        if readonly:
            return
        for path in pending:
            if self._is_drive_root(path):
                continue
            # Watch any new subdirectories that appeared (deferred from _on_dir_changed)
            try:
                for p in Path(path).iterdir():
                    if p.is_dir() and str(p) not in self._watcher.directories():
                        self._watcher.addPath(str(p))
            except OSError:
                pass
            # Scan the changed folder; _on_worker_finished only refreshes the
            # grid when scanned == self._folder, so other folders update silently.
            if not self._is_drive_root(path):
                self._handle_folder_change(path)

    def _handle_folder_change(self, path: str):
        """A watched/current folder changed: if we're viewing it, show the new files at once
        as placeholder cards (ThumbsPlus-style), then scan to build their thumbnails."""
        try:
            cur = os.path.normcase(os.path.normpath(self._current_folder or ""))
            if cur and os.path.normcase(os.path.normpath(path)) == cur:
                self._grid.show_new_placeholders(path)
        except Exception:
            pass
        self._grid.scan_folder(path)

    def _poll_dirs(self) -> list[str]:
        """Watched-folder roots + their immediate subdirs (the scan scope), capped."""
        dirs: list[str] = []
        for wf in (self._settings.get("watched_folders") or []):
            if len(dirs) >= self._MAX_WATCH_PATHS:
                break
            if not wf or not os.path.isdir(wf) or wf in dirs:
                continue
            dirs.append(wf)
            try:
                with os.scandir(wf) as it:
                    for e in it:
                        if len(dirs) >= self._MAX_WATCH_PATHS:
                            break
                        try:
                            if e.is_dir(follow_symlinks=False) and e.path not in dirs:
                                dirs.append(e.path)
                        except OSError:
                            pass
            except OSError:
                pass
        return dirs

    def _poll_watched_folders(self):
        """Polling fallback for Watched Folders on network drives (where QFileSystemWatcher
        is silent). Diffs each folder's file list; queues changed ones through the same
        debounced flush as the watcher (set-dedup avoids double-scanning local folders)."""
        if self._settings.get("thumbsplus_mode") == "readonly":
            return
        for d in self._poll_dirs():
            try:
                names = frozenset(e.name for e in os.scandir(d) if e.is_file())
            except OSError:
                continue
            prev = self._poll_snapshot.get(d)
            self._poll_snapshot[d] = names
            if prev is not None and names != prev:
                self._watcher_pending.add(d)
                self._watcher_timer.start()

    def _on_tasks_panel_close(self):
        """User clicked ✕ on the tasks panel — hide it and save setting."""
        self._settings.set("show_tasks_panel", False)
        self._tasks_panel.set_enabled(False)

    def _rebuild_app_btns(self):
        """Clear and rebuild launch-app icon buttons on the right of icon bar."""
        while self._app_btns_layout.count():
            item = self._app_btns_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for app in self._settings.get("launch_apps") or []:
            icon = _app_icon_from_b64(app.get("icon_b64", ""))
            btn  = self._mk_icon_btn(icon, tooltip=app.get("name", "App"))
            btn.clicked.connect(lambda checked=False, a=app: self._launch_app(a))
            self._app_btns_layout.addWidget(btn)

    def _launch_app(self, app: dict):
        """Launch app with the currently-selected image (shared ShellExecute launcher).
        Selection lives in the grid's _selected_fps, exposed via selected_paths(); the old
        self._grid._selected attribute didn't exist, so the file was never passed."""
        from thumb_grid import _launch_app as _grid_launch_app
        try:
            paths = self._grid.selected_paths() or []
        except Exception:
            paths = []
        if not paths:
            self._on_status("Select an image first, then click the app button.")
            return
        _grid_launch_app(app, paths[0])

    def _restore_folder(self, folder: str):
        import os
        if os.path.isdir(folder):
            self._current_folder = folder
            self._folder_panel.navigate_to(folder)
            self._grid.load_folder(folder)

    def _apply_db_mode(self):
        """Open or close the ThumbsPlusReader based on the current mode setting."""
        if self._tp_reader is not None:
            self._tp_reader.close()
            self._tp_reader = None
        mode = self._settings.get("thumbsplus_mode") or "none"
        if mode in ("readonly", "import"):
            path = self._settings.get("thumbsplus_db_path") or ""
            if not path:
                return
            # Open on a background thread with a 4-second timeout so a slow or
            # offline drive (e.g. network share / external disk) never blocks startup.
            import threading
            result: list = []
            def _open():
                try:
                    result.append(_ThumbsPlusReader(path))
                except Exception:
                    result.append(None)
            t = threading.Thread(target=_open, daemon=True)
            t.start()
            t.join(timeout=4.0)
            self._tp_reader = result[0] if result else None

    def closeEvent(self, event):
        # Flush any pending debounced settings write immediately
        if self._settings._save_timer is not None:
            self._settings._save_timer.cancel()
        self._settings.save()

        # Stop any running AI job + our own JoyCaption server (INTERNAL mode) so it frees VRAM.
        w = getattr(self, "_ai_worker", None)
        if w is not None and w.isRunning():
            w.cancel(); w.wait(3000)
        if getattr(self, "_captioner", None) is not None:
            try:
                self._captioner.stop()
            except Exception:
                pass

        self._grid.shutdown()
        self._db.close()
        if self._tp_reader is not None:
            self._tp_reader.close()
        super().closeEvent(event)


# ── Watched Folders dialog ────────────────────────────────────────────────────

class _WatchedFoldersDialog(QDialog):
    """Lists always-watched folders and lets the user remove them."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("ThumbsAI — Watched Folders")
        self.resize(560, 340)
        self.setStyleSheet(f"background:{BG};color:{PRI};font-family:{FONT};")

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)

        note = QLabel(
            "These folders are monitored for new files whenever ThumbsAI is open,\n"
            "regardless of which folder you are currently browsing.\n"
            "Right-click any folder in the tree to add or remove it.")
        note.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        note.setWordWrap(True)
        v.addWidget(note)

        # ── Folder list ───────────────────────────────────────────────────────
        self._list = QWidget()
        self._list_layout = QVBoxLayout(self._list)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea{{border:1px solid {MUT};border-radius:4px;background:{CAR};}}")
        scroll.setWidget(self._list)
        scroll.setMinimumHeight(160)
        v.addWidget(scroll, stretch=1)

        self._empty_lbl = QLabel("No folders are being watched.")
        self._empty_lbl.setAlignment(Qt.AlignCenter)
        self._empty_lbl.setStyleSheet(
            f"color:{MUT};font-size:{FONT_SM}px;background:transparent;")
        self._list_layout.addWidget(self._empty_lbl)
        self._list_layout.addStretch()

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        _btn_ss = (
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"padding:4px 14px;}}"
            f"QPushButton:hover{{background:#555577;}}")

        btn_clear = QPushButton("Remove All")
        btn_clear.setStyleSheet(_btn_ss)
        btn_clear.clicked.connect(self._remove_all)
        btn_row.addWidget(btn_clear)
        btn_row.addStretch()

        btn_close = QPushButton("Close")
        btn_close.setStyleSheet(_btn_ss)
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        v.addLayout(btn_row)

        self._populate()

    def _populate(self):
        # Clear existing rows (keep empty label + stretch at end)
        while self._list_layout.count() > 2:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        watched = sorted(self._settings.get("watched_folders") or [])
        self._empty_lbl.setVisible(not watched)

        for path in watched:
            row = QWidget()
            row.setStyleSheet(
                f"background:{PAN};border-radius:4px;")
            rl = QHBoxLayout(row)
            rl.setContentsMargins(8, 4, 8, 4)
            rl.setSpacing(8)

            lbl = QLabel(path)
            lbl.setStyleSheet(
                f"color:{PRI};font-size:{FONT_SM}px;background:transparent;")
            lbl.setWordWrap(False)
            rl.addWidget(lbl, stretch=1)

            btn_rm = QPushButton("✕ Remove")
            btn_rm.setFixedHeight(22)
            btn_rm.setStyleSheet(
                f"QPushButton{{background:{RED};color:{PRI};border:none;"
                f"border-radius:3px;font-size:{FONT_SM}px;padding:0 8px;}}"
                f"QPushButton:hover{{background:#cc2222;}}")
            btn_rm.clicked.connect(lambda _=False, p=path: self._remove(p))
            rl.addWidget(btn_rm)

            self._list_layout.insertWidget(self._list_layout.count() - 2, row)

    def _remove(self, path: str):
        watched = list(self._settings.get("watched_folders") or [])
        if path in watched:
            watched.remove(path)
        self._settings.set("watched_folders", watched)
        self._populate()

    def _remove_all(self):
        self._settings.set("watched_folders", [])
        self._populate()


# ── Settings dialog ───────────────────────────────────────────────────────────

from PySide6.QtWidgets import (QListWidget, QListWidgetItem, QStackedWidget,
                               QScrollArea, QRadioButton, QButtonGroup,
                               QProgressBar, QTextEdit)

_CHK_STYLE = lambda: (
    f"QCheckBox{{color:{PRI};font-size:{FONT_MD}px;spacing:8px;"
    f"background:transparent;}}"
    f"QCheckBox::indicator{{width:15px;height:15px;"
    f"border:1px solid {MUT};border-radius:3px;background:{CAR};}}"
    f"QCheckBox::indicator:checked{{background:{ACC};border:1px solid {ACC};}}"
)


class _WheelGuard(QObject):
    """Stop the scroll-wheel from silently changing a spin-box / combo value on the settings page.
    Qt steals the wheel on hover, so scrolling the page nudges whatever field is under the cursor. With
    StrongFocus (set by the caller) hovering never grabs wheel focus, and this filter swallows any wheel
    event the field gets unless it's explicitly focused — so values only change on purpose."""
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel and not obj.hasFocus():
            return True
        return False


class _SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("ThumbsAI — Settings")
        self.resize(640, 480)
        self.setMinimumSize(560, 400)
        self.setStyleSheet(f"background:{BG};color:{PRI};font-family:{FONT};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Top area: list + stack ────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(
            f"QSplitter::handle{{background:{MUT};}}"
            f"QSplitter::handle:horizontal{{width:1px;}}")

        # Left category list
        self._cat_list = QListWidget()
        self._cat_list.setFixedWidth(150)
        self._cat_list.setStyleSheet(
            f"QListWidget{{background:{PAN};border:none;"
            f"font-family:{FONT};font-size:{FONT_MD}px;outline:none;}}"
            f"QListWidget::item{{padding:10px 14px;color:{SEC};}}"
            f"QListWidget::item:selected{{background:{ACC};color:{PRI};"
            f"border-radius:0px;}}"
            f"QListWidget::item:hover:!selected{{background:{MUT};}}")
        self._cat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Right stacked pages
        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background:{BG};")

        splitter.addWidget(self._cat_list)
        splitter.addWidget(self._stack)
        splitter.setSizes([150, 490])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        outer.addWidget(splitter, stretch=1)

        # ── Bottom button row ─────────────────────────────────────────────────
        btn_bar = QFrame()
        btn_bar.setFixedHeight(48)
        btn_bar.setStyleSheet(
            f"QFrame{{background:{PAN};border-top:1px solid {MUT};}}")
        bh = QHBoxLayout(btn_bar)
        bh.setContentsMargins(12, 0, 12, 0)
        bh.setSpacing(8)

        btn_ok = QPushButton("OK")
        btn_ok.setFixedSize(90, 28)
        btn_ok.setStyleSheet(
            f"QPushButton{{background:{ACC};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"font-weight:bold;}}"
            f"QPushButton:hover{{background:#185FA5;}}")
        btn_ok.clicked.connect(self._save_and_close)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedSize(90, 28)
        btn_cancel.setStyleSheet(
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"font-weight:bold;}}"
            f"QPushButton:hover{{background:#555577;}}")
        btn_cancel.clicked.connect(self.reject)

        bh.addStretch()
        bh.addWidget(btn_cancel)
        bh.addWidget(btn_ok)
        outer.addWidget(btn_bar)

        # ── Build pages ───────────────────────────────────────────────────────
        self._build_general_page()
        self._build_filetypes_page()
        self._build_viewer_page()
        self._build_apps_page()
        self._build_plugins_page()
        self._build_database_page()
        self._build_ai_page()

        self._cat_list.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._cat_list.setCurrentRow(0)

    # ── Page builders ─────────────────────────────────────────────────────────

    def _add_page(self, title: str) -> QVBoxLayout:
        """Add a category entry and return the scrollable page layout."""
        item = QListWidgetItem(title)
        self._cat_list.addItem(item)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{BG};border:none;}}")
        page = QWidget()
        page.setStyleSheet(f"background:{BG};")
        vl = QVBoxLayout(page)
        vl.setContentsMargins(24, 20, 24, 20)
        vl.setSpacing(10)

        # Page title
        ttl = QLabel(title)
        ttl.setStyleSheet(
            f"color:{PRI};font-size:{FONT_LG}px;font-weight:bold;"
            f"background:transparent;")
        vl.addWidget(ttl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{MUT};border:none;")
        sep.setFixedHeight(1)
        vl.addWidget(sep)

        scroll.setWidget(page)
        self._stack.addWidget(scroll)
        return vl

    def _section_label(self, layout: QVBoxLayout, text: str):
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;font-weight:bold;"
            f"background:transparent;margin-top:8px;")
        layout.addWidget(lbl)

    def _build_ai_page(self):
        """AI tab — the ThumbsAI persona (how JoyCaption tags) + JoyCaption's LLM parameters, mirroring
        Jarvis's per-model settings. Everything here feeds caption_backend.Captioner."""
        from settings import _DEFAULTS as _SD
        vl = self._add_page("AI")
        s = self._settings

        if not hasattr(self, "_ai_wheel_guard"):
            self._ai_wheel_guard = _WheelGuard(self)

        def _fld(w):
            w.setStyleSheet(f"background:{CAR};color:{PRI};border:1px solid {MUT};border-radius:3px;"
                            f"padding:3px 6px;font-family:{FONT};font-size:{FONT_SM}px;")
            # spin/combo: scrolling the page must not nudge the value
            if isinstance(w, (QSpinBox, QDoubleSpinBox, QComboBox)):
                w.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                w.installEventFilter(self._ai_wheel_guard)
            return w

        def _row(label, widget):
            r = QHBoxLayout(); r.setContentsMargins(0, 0, 0, 0)
            lb = QLabel(label); lb.setFixedWidth(150)
            lb.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
            r.addWidget(lb); r.addWidget(widget, 1)
            cont = QWidget(); cont.setLayout(r); vl.addWidget(cont)
            return widget

        def _browse_row(label, edit, is_dir=False, file_filter=""):
            def go():
                if is_dir:
                    p = QFileDialog.getExistingDirectory(self, "Select folder", edit.text().strip())
                else:
                    p, _ = QFileDialog.getOpenFileName(self, "Select file", edit.text().strip(), file_filter)
                if p:
                    edit.setText(p)
            r = QHBoxLayout(); r.setContentsMargins(0, 0, 0, 0)
            lb = QLabel(label); lb.setFixedWidth(150)
            lb.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
            b = QPushButton("Browse…"); b.clicked.connect(go)
            r.addWidget(lb); r.addWidget(edit, 1); r.addWidget(b)
            cont = QWidget(); cont.setLayout(r); vl.addWidget(cont)

        # ── Mode ───────────────────────────────────────────────────────────────
        self._section_label(vl, "BACKEND")
        self._ai_mode_combo = _fld(QComboBox())
        self._ai_mode_combo.addItem("Internal — ThumbsAI runs its own JoyCaption", "internal")
        self._ai_mode_combo.addItem("Jarvis — reuse the Suite's caption server", "jarvis")
        i = self._ai_mode_combo.findData(s.get("ai_mode", "internal"))
        self._ai_mode_combo.setCurrentIndex(i if i >= 0 else 0)
        _row("Mode", self._ai_mode_combo)

        # ── Persona (the captioner's job description) ───────────────────────────
        self._section_label(vl, "THUMBSAI PERSONA — how it tags")
        self._ai_instruction = QTextEdit()
        self._ai_instruction.setPlainText(s.get("ai_instruction", ""))
        self._ai_instruction.setMinimumHeight(96)
        self._ai_instruction.setStyleSheet(
            f"QTextEdit{{background:{CAR};color:{PRI};border:1px solid {MUT};border-radius:3px;"
            f"font-family:{FONT};font-size:{FONT_SM}px;padding:4px;}}")
        vl.addWidget(self._ai_instruction)
        rst = QPushButton("Reset persona to default")
        rst.clicked.connect(lambda: self._ai_instruction.setPlainText(_SD["ai_instruction"]))
        vl.addWidget(rst)

        # ── Model files ─────────────────────────────────────────────────────────
        self._section_label(vl, "MODEL FILES")
        self._ai_gguf = _fld(QLineEdit(s.get("ai_joycaption_gguf", "")))
        _browse_row("JoyCaption .gguf", self._ai_gguf, file_filter="GGUF (*.gguf)")
        self._ai_mmproj = _fld(QLineEdit(s.get("ai_joycaption_mmproj", "")))
        _browse_row("Vision mmproj", self._ai_mmproj, file_filter="GGUF (*.gguf)")
        self._ai_llama_dir = _fld(QLineEdit(s.get("ai_llama_dir", "")))
        _browse_row("llama-server dir", self._ai_llama_dir, is_dir=True)

        # ── LLM parameters (mirror Jarvis) ──────────────────────────────────────
        self._section_label(vl, "LLM PARAMETERS")
        self._ai_temp = _fld(QDoubleSpinBox()); self._ai_temp.setRange(0.0, 2.0)
        self._ai_temp.setSingleStep(0.05); self._ai_temp.setDecimals(2)
        self._ai_temp.setValue(float(s.get("ai_temperature", 0.4)))
        _row("Temperature", self._ai_temp)
        self._ai_maxtok = _fld(QSpinBox()); self._ai_maxtok.setRange(16, 2048)
        self._ai_maxtok.setValue(int(s.get("ai_max_tokens", 200)))
        _row("Max tokens", self._ai_maxtok)
        self._ai_nctx = _fld(QSpinBox()); self._ai_nctx.setRange(512, 131072)
        self._ai_nctx.setValue(int(s.get("ai_n_ctx", 4096)))
        _row("Context length", self._ai_nctx)
        self._ai_ngl = _fld(QSpinBox()); self._ai_ngl.setRange(-1, 999)
        self._ai_ngl.setValue(int(s.get("ai_n_gpu_layers", -1)))
        _row("GPU layers (-1=all)", self._ai_ngl)
        self._ai_fa = QCheckBox("Flash attention"); self._ai_fa.setChecked(bool(s.get("ai_flash_attn", True)))
        self._ai_fa.setStyleSheet(f"color:{PRI};font-size:{FONT_SM}px;")
        vl.addWidget(self._ai_fa)
        self._ai_ck = _fld(QComboBox()); self._ai_ck.addItems(["f16", "q8_0", "q4_0"])
        self._ai_ck.setCurrentText(s.get("ai_cache_type_k", "f16"))
        _row("KV cache K type", self._ai_ck)
        self._ai_cv = _fld(QComboBox()); self._ai_cv.addItems(["f16", "q8_0", "q4_0"])
        self._ai_cv.setCurrentText(s.get("ai_cache_type_v", "f16"))
        _row("KV cache V type", self._ai_cv)

        # ── Server + tagging ────────────────────────────────────────────────────
        self._section_label(vl, "SERVER & TAGGING")
        self._ai_int_port = _fld(QSpinBox()); self._ai_int_port.setRange(1024, 65535)
        self._ai_int_port.setValue(int(s.get("ai_internal_port", 8082)))
        _row("Internal port", self._ai_int_port)
        self._ai_jarvis_url = _fld(QLineEdit(s.get("ai_jarvis_caption_url", "")))
        _row("Jarvis caption URL", self._ai_jarvis_url)
        self._ai_merge = QCheckBox("Merge new tags with existing (uncheck = OVERRIDE / re-tag)")
        self._ai_merge.setChecked(bool(s.get("ai_tag_merge", True)))
        self._ai_merge.setStyleSheet(f"color:{PRI};font-size:{FONT_SM}px;")
        self._ai_merge.setToolTip(
            "Checked (Merge): add new tags to what's already there, and SKIP images that already have "
            "tags — so a re-run resumes a big folder instead of redoing it.\n"
            "Unchecked (Override): re-tag EVERY image and REPLACE its tags — use this to regenerate the "
            "whole folder after you change the persona or alias map.")
        vl.addWidget(self._ai_merge)

        # ── Tag alias map (canonical term swaps; add more anytime) ──────────────────
        self._section_label(vl, "TAG ALIAS MAP — canonical term swaps")
        hint = QLabel("One per line:  from = to   (e.g.  breasts = boobs).  Applied to every tag on top "
                      "of the built-in booru aliases; your pairs win. Blank lines and #comments ignored.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        vl.addWidget(hint)
        self._ai_aliases = QTextEdit()
        self._ai_aliases.setPlainText(s.get("ai_tag_aliases", ""))
        self._ai_aliases.setPlaceholderText("breasts = boobs\nbuttocks = butt\nabdomen = abs")
        self._ai_aliases.setMinimumHeight(80)
        self._ai_aliases.setStyleSheet(
            f"QTextEdit{{background:{CAR};color:{PRI};border:1px solid {MUT};border-radius:3px;"
            f"font-family:{FONT};font-size:{FONT_SM}px;padding:4px;}}")
        vl.addWidget(self._ai_aliases)
        vl.addStretch(1)

    def _build_general_page(self):
        vl = self._add_page("General")

        self._chk_remember = QCheckBox("Remember last folder on startup")
        self._chk_remember.setChecked(
            bool(self._settings.get("remember_last_folder")))
        self._chk_remember.setStyleSheet(_CHK_STYLE())
        vl.addWidget(self._chk_remember)

        self._chk_show_tasks = QCheckBox("Show Tasks panel")
        self._chk_show_tasks.setChecked(
            bool(self._settings.get("show_tasks_panel")))
        self._chk_show_tasks.setStyleSheet(_CHK_STYLE())
        vl.addWidget(self._chk_show_tasks)

        self._chk_auto_scan = QCheckBox("Auto Scan folder on click")
        self._chk_auto_scan.setChecked(
            self._settings.get("auto_scan", True))
        self._chk_auto_scan.setStyleSheet(_CHK_STYLE())
        self._chk_auto_scan.setToolTip(
            "Checked: automatically scan for new/changed files each time a folder is clicked.\n"
            "Unchecked: only show what is already in the database (faster).")
        vl.addWidget(self._chk_auto_scan)

        self._chk_confirm_delete = QCheckBox("Confirm before deleting files")
        self._chk_confirm_delete.setChecked(
            bool(self._settings.get("confirm_delete")))
        self._chk_confirm_delete.setStyleSheet(_CHK_STYLE())
        vl.addWidget(self._chk_confirm_delete)

        self._chk_preserve_meta = QCheckBox("Preserve Metadata on edit")
        self._chk_preserve_meta.setChecked(
            self._settings.get("preserve_metadata_on_edit", True))
        self._chk_preserve_meta.setStyleSheet(_CHK_STYLE())
        self._chk_preserve_meta.setToolTip(
            "Checked: keep original EXIF/metadata when saving edited images.\n"
            "Unchecked: strip metadata on save.")
        vl.addWidget(self._chk_preserve_meta)

        # ── External Tools ────────────────────────────────────────────────────
        self._section_label(vl, "External Tools")

        ffmpeg_row = QWidget()
        ffmpeg_row.setStyleSheet("background:transparent;")
        ffmpeg_h = QHBoxLayout(ffmpeg_row)
        ffmpeg_h.setContentsMargins(0, 0, 0, 0)
        ffmpeg_h.setSpacing(8)
        ffmpeg_lbl = QLabel("ffmpeg.exe:")
        ffmpeg_lbl.setStyleSheet(
            f"color:{PRI};font-family:{FONT};font-size:{FONT_SM}px;background:transparent;")
        ffmpeg_lbl.setFixedWidth(90)
        self._ffmpeg_edit = QLineEdit()
        self._ffmpeg_edit.setText(self._settings.get("ffmpeg_exe", ""))
        self._ffmpeg_edit.setPlaceholderText("Leave blank to auto-detect")
        self._ffmpeg_edit.setStyleSheet(
            f"QLineEdit{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"border-radius:4px;padding:3px 8px;"
            f"font-family:{FONT};font-size:{FONT_SM}px;}}")
        btn_ffmpeg = QPushButton("Browse…")
        btn_ffmpeg.setFixedWidth(72)
        btn_ffmpeg.setStyleSheet(
            f"QPushButton{{background:{MUT};color:{PRI};border:none;border-radius:4px;"
            f"font-family:{FONT};font-size:{FONT_SM}px;padding:3px 8px;}}"
            f"QPushButton:hover{{background:#555577;}}")
        btn_ffmpeg.clicked.connect(self._browse_ffmpeg)
        ffmpeg_h.addWidget(ffmpeg_lbl)
        ffmpeg_h.addWidget(self._ffmpeg_edit, stretch=1)
        ffmpeg_h.addWidget(btn_ffmpeg)
        vl.addWidget(ffmpeg_row)

        # ── Font Sizes ────────────────────────────────────────────────────────
        self._section_label(vl, "Font Sizes")

        _spin_ss = (
            f"QSpinBox{{background:{CAR};color:{PRI};"
            f"border:1px solid {MUT};border-radius:4px;"
            f"padding:2px 6px;font-family:{FONT};font-size:{FONT_SM}px;}}"
            f"QSpinBox::up-button,QSpinBox::down-button{{"
            f"width:16px;background:{MUT};border:none;border-radius:2px;}}"
            f"QSpinBox::up-button:hover,QSpinBox::down-button:hover{{background:{ACC};}}")

        def _font_row(label_text: str, setting_key: str):
            row_w = QWidget()
            row_w.setStyleSheet("background:transparent;")
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(
                f"color:{PRI};font-family:{FONT};font-size:{FONT_SM}px;"
                f"background:transparent;")
            lbl.setFixedWidth(160)
            spin = QSpinBox()
            spin.setRange(7, 20)
            spin.setValue(int(self._settings.get(setting_key) or 9))
            spin.setSuffix(" px")
            spin.setFixedWidth(72)
            spin.setStyleSheet(_spin_ss)
            row.addWidget(lbl)
            row.addWidget(spin)
            row.addStretch()
            vl.addWidget(row_w)
            return spin

        self._spin_font_folder = _font_row("Folder tree font size",  "font_folder")
        self._spin_font_image  = _font_row("Image label font size",  "font_image")
        self._spin_font_meta   = _font_row("Metadata panel font size", "font_meta")

        vl.addStretch()

    def _build_filetypes_page(self):
        vl = self._add_page("File Types")

        lbl_desc = QLabel(
            "Checked formats appear in the thumbnail grid.\n"
            "Uncheck a format to hide those files.")
        lbl_desc.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        lbl_desc.setWordWrap(True)
        vl.addWidget(lbl_desc)

        disabled = set(self._settings.get("disabled_extensions") or [])
        self._ext_checks: dict[str, QCheckBox] = {}

        for group_name, entries in FILE_TYPE_GROUPS:
            self._section_label(vl, group_name)
            for ext, label in entries:
                chk = QCheckBox(f"{label}  ({ext})")
                chk.setChecked(ext not in disabled)
                chk.setStyleSheet(_CHK_STYLE())
                vl.addWidget(chk)
                self._ext_checks[ext] = chk

        vl.addStretch()

    def _build_plugins_page(self):
        vl = self._add_page("Plugins")

        _btn_ss = (
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"padding:4px 12px;}}"
            f"QPushButton:hover{{background:#555577;}}")

        desc = QLabel(
            "Add folders containing Photoshop .8bf filter plugins.\n"
            "64-bit plugins only — 32-bit plugins are detected but skipped at runtime.")
        desc.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        desc.setWordWrap(True)
        vl.addWidget(desc)

        self._plugin_dirs_data: list[str] = list(
            self._settings.get("plugin_dirs") or [])
        self._plugin_dirs_list = QListWidget()
        self._plugin_dirs_list.setFixedHeight(160)
        self._plugin_dirs_list.setStyleSheet(
            f"QListWidget{{background:{CAR};border:1px solid {MUT};"
            f"font-family:{FONT};font-size:{FONT_SM}px;color:{PRI};}}"
            f"QListWidget::item{{padding:5px 8px;}}"
            f"QListWidget::item:selected{{background:{ACC};}}")
        for d in self._plugin_dirs_data:
            self._plugin_dirs_list.addItem(d)
        vl.addWidget(self._plugin_dirs_list)

        btn_row_w = QWidget()
        btn_row_w.setStyleSheet("background:transparent;")
        btn_row = QHBoxLayout(btn_row_w)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        btn_add_dir = QPushButton("Add Folder…")
        btn_add_dir.setStyleSheet(_btn_ss)
        btn_rem_dir = QPushButton("Remove")
        btn_rem_dir.setStyleSheet(_btn_ss)
        btn_row.addWidget(btn_add_dir)
        btn_row.addWidget(btn_rem_dir)
        btn_row.addStretch()
        vl.addWidget(btn_row_w)

        vl.addStretch()

        btn_add_dir.clicked.connect(self._add_plugin_dir)
        btn_rem_dir.clicked.connect(self._remove_plugin_dir)

    def _add_plugin_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Plugin Folder")
        if d and d not in self._plugin_dirs_data:
            self._plugin_dirs_data.append(d)
            self._plugin_dirs_list.addItem(d)

    def _remove_plugin_dir(self):
        row = self._plugin_dirs_list.currentRow()
        if 0 <= row < len(self._plugin_dirs_data):
            self._plugin_dirs_data.pop(row)
            self._plugin_dirs_list.takeItem(row)

    def _build_database_page(self):
        vl = self._add_page("Database")

        _edit_ss = (
            f"QLineEdit{{background:{CAR};color:{PRI};"
            f"border:1px solid {MUT};border-radius:4px;"
            f"padding:3px 8px;font-family:{FONT};font-size:{FONT_SM}px;}}")
        _btn_ss = (
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"padding:4px 12px;}}"
            f"QPushButton:hover{{background:#555577;}}")
        _radio_style = (
            f"QRadioButton{{color:{PRI};font-size:{FONT_MD}px;spacing:8px;"
            f"background:transparent;}}"
            f"QRadioButton::indicator{{width:14px;height:14px;"
            f"border:1px solid {MUT};border-radius:7px;background:{CAR};}}"
            f"QRadioButton::indicator:checked{{background:{ACC};"
            f"border:1px solid {ACC};}}")

        desc = QLabel(
            "Connect to an existing ThumbsPlus 10 catalog to browse or import its data.")
        desc.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        desc.setWordWrap(True)
        vl.addWidget(desc)

        # ── Database path ──────────────────────────────────────────────────
        self._section_label(vl, "Database Path  (.tpdb8s)")

        path_row_w = QWidget()
        path_row_w.setStyleSheet("background:transparent;")
        path_row = QHBoxLayout(path_row_w)
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(6)

        self._tp_path_edit = QLineEdit()
        self._tp_path_edit.setStyleSheet(_edit_ss)
        self._tp_path_edit.setPlaceholderText(r"e.g.  G:\ThumbsDB\Thumbs.tpdb8s")
        self._tp_path_edit.setText(
            self._settings.get("thumbsplus_db_path") or "")

        btn_browse_tp = QPushButton("Browse…")
        btn_browse_tp.setStyleSheet(_btn_ss)
        btn_browse_tp.clicked.connect(self._browse_thumbsplus_db)

        path_row.addWidget(self._tp_path_edit, stretch=1)
        path_row.addWidget(btn_browse_tp)
        vl.addWidget(path_row_w)

        test_row_w = QWidget()
        test_row_w.setStyleSheet("background:transparent;")
        test_row = QHBoxLayout(test_row_w)
        test_row.setContentsMargins(0, 0, 0, 0)
        test_row.setSpacing(10)

        btn_test = QPushButton("Test Connection")
        btn_test.setStyleSheet(_btn_ss)
        btn_test.clicked.connect(self._test_thumbsplus_connection)

        self._tp_status_lbl = QLabel("")
        self._tp_status_lbl.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        self._tp_status_lbl.setWordWrap(True)

        test_row.addWidget(btn_test)
        test_row.addWidget(self._tp_status_lbl, stretch=1)
        vl.addWidget(test_row_w)

        # ── Access Mode ────────────────────────────────────────────────────
        self._section_label(vl, "Access Mode")

        self._db_mode_group = QButtonGroup(self)
        cur_mode = self._settings.get("thumbsplus_mode") or "none"

        self._radio_db_none = QRadioButton(
            "Not configured  —  ThumbsAI uses its own database only")
        self._radio_db_none.setChecked(cur_mode == "none")
        self._radio_db_none.setStyleSheet(_radio_style)

        self._radio_db_readonly = QRadioButton(
            "Read Only  —  browse ThumbsPlus catalog without copying data")
        self._radio_db_readonly.setChecked(cur_mode == "readonly")
        self._radio_db_readonly.setStyleSheet(_radio_style)

        self._radio_db_import = QRadioButton(
            "Import  —  copy ThumbsPlus catalog into ThumbsAI (non-destructive)")
        self._radio_db_import.setChecked(cur_mode == "import")
        self._radio_db_import.setStyleSheet(_radio_style)

        self._db_mode_group.addButton(self._radio_db_none)
        self._db_mode_group.addButton(self._radio_db_readonly)
        self._db_mode_group.addButton(self._radio_db_import)

        vl.addWidget(self._radio_db_none)
        vl.addWidget(self._radio_db_readonly)
        vl.addWidget(self._radio_db_import)

        # ── Import section (visible only when Import mode is selected) ─────
        self._import_frame = QFrame()
        self._import_frame.setStyleSheet(
            f"QFrame{{background:transparent;border:1px solid {MUT};"
            f"border-radius:4px;}}")
        iv = QVBoxLayout(self._import_frame)
        iv.setContentsMargins(12, 10, 12, 10)
        iv.setSpacing(8)

        import_desc = QLabel(
            "Phase 1 — copies paths, dimensions, ratings, keywords and thumbnails.\n"
            "Phase 2 — reads AI metadata (prompt, model, seed…) from PNG files.\n"
            "Existing ThumbsAI data is never overwritten.")
        import_desc.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        import_desc.setWordWrap(True)
        iv.addWidget(import_desc)

        btn_start = QPushButton("Start Import…")
        btn_start.setFixedWidth(140)
        btn_start.setStyleSheet(
            f"QPushButton{{background:{ACC};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"font-weight:bold;padding:5px 12px;}}"
            f"QPushButton:hover{{background:#185FA5;}}")
        btn_start.clicked.connect(self._start_import)
        iv.addWidget(btn_start)

        vl.addWidget(self._import_frame)
        self._import_frame.setVisible(cur_mode == "import")
        self._radio_db_import.toggled.connect(self._import_frame.setVisible)

        vl.addStretch()

    def _browse_ffmpeg(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ffmpeg.exe", "",
            "ffmpeg executable (ffmpeg.exe);;All Files (*)")
        if path:
            self._ffmpeg_edit.setText(path)

    def _browse_thumbsplus_db(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select ThumbsPlus Database", "",
            "ThumbsPlus Database (*.tpdb8s *.tpdb);;All Files (*)")
        if path:
            self._tp_path_edit.setText(path)

    def _test_thumbsplus_connection(self):
        path = self._tp_path_edit.text().strip()
        if not path:
            self._tp_status_lbl.setText("No path specified.")
            return
        import sqlite3 as _sq
        try:
            con   = _sq.connect(f"file:{path}?mode=ro", uri=True)
            count = con.execute("SELECT COUNT(*) FROM Thumbnail").fetchone()[0]
            vols  = con.execute("SELECT COUNT(*) FROM Volume").fetchone()[0]
            con.close()
            self._tp_status_lbl.setStyleSheet(
                f"color:{GRN};font-size:{FONT_SM}px;background:transparent;")
            self._tp_status_lbl.setText(
                f"Connected  ·  {count:,} images  ·  {vols} volume(s)")
        except Exception as e:
            self._tp_status_lbl.setStyleSheet(
                f"color:{RED};font-size:{FONT_SM}px;background:transparent;")
            self._tp_status_lbl.setText(f"Error: {e}")

    def _start_import(self):
        path = self._tp_path_edit.text().strip()
        if not path:
            self._tp_status_lbl.setText("Set the database path first.")
            return
        main_win = self.parent()
        # Block a second simultaneous import
        existing = getattr(main_win, '_import_dlg', None)
        if existing is not None and not existing._done:
            existing.raise_()
            existing.activateWindow()
            self._tp_status_lbl.setText("An import is already running.")
            return
        from database import THUMBS_DB
        dlg = _ImportProgressDialog(path, str(THUMBS_DB), main_win)
        main_win._import_dlg = dlg
        dlg.show()

    def _build_viewer_page(self):
        vl = self._add_page("Image Viewer")

        _radio_style = (
            f"QRadioButton{{color:{PRI};font-size:{FONT_MD}px;spacing:8px;"
            f"background:transparent;}}"
            f"QRadioButton::indicator{{width:14px;height:14px;"
            f"border:1px solid {MUT};border-radius:7px;background:{CAR};}}"
            f"QRadioButton::indicator:checked{{background:{ACC};"
            f"border:1px solid {ACC};}}")

        # Default zoom
        self._section_label(vl, "Default Zoom")
        self._zoom_group = QButtonGroup(self)
        cur_zoom = self._settings.get("viewer_default_zoom") or "fit"

        self._radio_fit = QRadioButton("Fit to window")
        self._radio_fit.setChecked(cur_zoom == "fit")
        self._radio_fit.setStyleSheet(_radio_style)

        self._radio_100 = QRadioButton("100% (actual size)")
        self._radio_100.setChecked(cur_zoom == "100")
        self._radio_100.setStyleSheet(_radio_style)

        self._zoom_group.addButton(self._radio_fit)
        self._zoom_group.addButton(self._radio_100)
        vl.addWidget(self._radio_fit)
        vl.addWidget(self._radio_100)

        self._chk_resize_on_zoom = QCheckBox("Resize window to fit image on open")
        self._chk_resize_on_zoom.setChecked(
            bool(self._settings.get("viewer_resize_on_zoom")))
        self._chk_resize_on_zoom.setStyleSheet(_CHK_STYLE())
        vl.addWidget(self._chk_resize_on_zoom)

        # Monitor placement
        self._chk_same_monitor = QCheckBox("Display images on same monitor as application")
        self._chk_same_monitor.setChecked(
            bool(self._settings.get("viewer_same_monitor", True)))
        self._chk_same_monitor.setStyleSheet(_CHK_STYLE())
        vl.addWidget(self._chk_same_monitor)

        # Metadata panel
        self._section_label(vl, "Metadata Panel")
        self._chk_show_meta = QCheckBox("Open with metadata panel visible")
        self._chk_show_meta.setChecked(
            bool(self._settings.get("viewer_show_meta")))
        self._chk_show_meta.setStyleSheet(_CHK_STYLE())
        vl.addWidget(self._chk_show_meta)

        # Window size
        self._section_label(vl, "Window Size")
        self._img_view_group = QButtonGroup(self)
        cur_size = self._settings.get("viewer_size_mode") or "fit"

        self._radio_iv_fit = QRadioButton("Fit to image  —  resize window to match each image")
        self._radio_iv_fit.setChecked(cur_size == "fit")
        self._radio_iv_fit.setStyleSheet(_radio_style)

        self._radio_iv_remember = QRadioButton("Remember size  —  keep last window dimensions")
        self._radio_iv_remember.setChecked(cur_size == "remember")
        self._radio_iv_remember.setStyleSheet(_radio_style)

        self._img_view_group.addButton(self._radio_iv_fit)
        self._img_view_group.addButton(self._radio_iv_remember)
        vl.addWidget(self._radio_iv_fit)
        vl.addWidget(self._radio_iv_remember)

        # Default file type for Save Close
        self._section_label(vl, "Default File Type")
        desc2 = QLabel("File format used by Save Close.")
        desc2.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        vl.addWidget(desc2)

        _combo_ss = (
            f"QComboBox{{background:{CAR};color:{PRI};"
            f"border:2px solid {MUT};border-radius:6px;"
            f"padding:4px 8px;font-family:{FONT};font-size:{FONT_MD}px;"
            f"min-height:28px;}}"
            f"QComboBox:focus{{border-color:{ACC};}}"
            f"QComboBox::drop-down{{border:none;width:24px;}}"
            f"QComboBox QAbstractItemView{{background:{CAR};color:{PRI};"
            f"border:1px solid {ACC};selection-background-color:{ACC};"
            f"outline:none;}}")
        self._save_fmt_combo = QComboBox()
        self._save_fmt_combo.setStyleSheet(_combo_ss)
        self._save_fmt_combo.setFixedWidth(200)
        for label, key in [
            ("PNG  (.png)",  "png"),
            ("JPEG (.jpg)",  "jpg"),
            ("WebP (.webp)", "webp"),
            ("BMP  (.bmp)",  "bmp"),
            ("TIFF (.tiff)", "tiff"),
        ]:
            self._save_fmt_combo.addItem(label, key)
        cur_fmt = self._settings.get("viewer_save_format") or "png"
        idx = self._save_fmt_combo.findData(cur_fmt)
        if idx >= 0:
            self._save_fmt_combo.setCurrentIndex(idx)
        vl.addWidget(self._save_fmt_combo)

        # Row Preloading
        self._section_label(vl, "Row Preloading")
        preload_desc = QLabel(
            "Rows of thumbnails buffered above and below the visible area.\n"
            "Higher values reduce blank flashes while scrolling fast.\n"
            "Warning: more rows require more RAM.")
        preload_desc.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        preload_desc.setWordWrap(True)
        vl.addWidget(preload_desc)

        _spin_ss = (
            f"QSpinBox{{background:{CAR};color:{PRI};"
            f"border:2px solid {MUT};border-radius:6px;"
            f"padding:4px 8px;font-family:{FONT};font-size:{FONT_MD}px;"
            f"min-height:28px;min-width:80px;}}"
            f"QSpinBox:focus{{border-color:{ACC};}}"
            f"QSpinBox::up-button,QSpinBox::down-button{{"
            f"border:none;width:18px;background:{MUT};}}"
            f"QSpinBox::up-button:hover,QSpinBox::down-button:hover{{background:{ACC};}}")
        self._spin_pool_buffer = QSpinBox()
        self._spin_pool_buffer.setStyleSheet(_spin_ss)
        self._spin_pool_buffer.setRange(1, 20)
        self._spin_pool_buffer.setValue(
            int(self._settings.get("pool_buffer") or 4))
        self._spin_pool_buffer.setSuffix("  rows")
        vl.addWidget(self._spin_pool_buffer)

        vl.addStretch()

    def _build_apps_page(self):
        vl = self._add_page("Applications")

        _btn_ss = (
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"padding:4px 12px;}}"
            f"QPushButton:hover{{background:#555577;}}")
        _edit_ss = (
            f"QLineEdit{{background:{CAR};color:{PRI};"
            f"border:1px solid {MUT};border-radius:4px;"
            f"padding:3px 8px;font-family:{FONT};font-size:{FONT_SM}px;}}")

        desc = QLabel(
            "Register external apps to open images.\n"
            "Use  %1  in the args field as a placeholder for the image filepath.")
        desc.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        desc.setWordWrap(True)
        vl.addWidget(desc)

        # App list
        self._apps_data: list[dict] = list(
            self._settings.get("launch_apps") or [])
        self._apps_list = QListWidget()
        self._apps_list.setFixedHeight(140)
        self._apps_list.setStyleSheet(
            f"QListWidget{{background:{CAR};border:1px solid {MUT};"
            f"font-family:{FONT};font-size:{FONT_SM}px;color:{PRI};}}"
            f"QListWidget::item{{padding:5px 8px;}}"
            f"QListWidget::item:selected{{background:{ACC};}}")
        for app in self._apps_data:
            self._apps_list.addItem(app.get("name", "App"))
        vl.addWidget(self._apps_list)

        # Add / Remove row
        btn_row_w = QWidget()
        btn_row_w.setStyleSheet("background:transparent;")
        btn_row = QHBoxLayout(btn_row_w)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.setSpacing(6)
        btn_add = QPushButton("Add App…")
        btn_add.setStyleSheet(_btn_ss)
        btn_rem = QPushButton("Remove")
        btn_rem.setStyleSheet(_btn_ss)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_rem)
        btn_row.addStretch()
        vl.addWidget(btn_row_w)

        # Edit fields
        self._section_label(vl, "Selected App")

        lbl_name = QLabel("Name:")
        lbl_name.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        vl.addWidget(lbl_name)
        self._app_name_edit = QLineEdit()
        self._app_name_edit.setStyleSheet(_edit_ss)
        vl.addWidget(self._app_name_edit)

        lbl_exe = QLabel("Executable:")
        lbl_exe.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        vl.addWidget(lbl_exe)
        exe_row_w = QWidget()
        exe_row_w.setStyleSheet("background:transparent;")
        exe_row = QHBoxLayout(exe_row_w)
        exe_row.setContentsMargins(0, 0, 0, 0)
        exe_row.setSpacing(6)
        self._app_exe_edit = QLineEdit()
        self._app_exe_edit.setStyleSheet(_edit_ss)
        btn_browse = QPushButton("Browse…")
        btn_browse.setStyleSheet(_btn_ss)
        exe_row.addWidget(self._app_exe_edit, stretch=1)
        exe_row.addWidget(btn_browse)
        vl.addWidget(exe_row_w)

        lbl_args = QLabel("Args  (use %1 for image path):")
        lbl_args.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        vl.addWidget(lbl_args)
        self._app_args_edit = QLineEdit()
        self._app_args_edit.setPlaceholderText('e.g.  "%1"')
        self._app_args_edit.setStyleSheet(_edit_ss)
        vl.addWidget(self._app_args_edit)

        vl.addStretch()

        # Connections
        self._apps_list.currentRowChanged.connect(self._load_app_fields)
        btn_add.clicked.connect(self._add_app)
        btn_rem.clicked.connect(self._remove_app)
        btn_browse.clicked.connect(self._browse_app_exe)
        self._app_name_edit.textChanged.connect(self._sync_app_field)
        self._app_exe_edit.textChanged.connect(self._sync_app_field)
        self._app_args_edit.textChanged.connect(self._sync_app_field)

    def _load_app_fields(self, row: int):
        if 0 <= row < len(self._apps_data):
            app = self._apps_data[row]
            self._app_name_edit.blockSignals(True)
            self._app_exe_edit.blockSignals(True)
            self._app_args_edit.blockSignals(True)
            self._app_name_edit.setText(app.get("name", ""))
            self._app_exe_edit.setText(app.get("exe", ""))
            self._app_args_edit.setText(app.get("args", "%1"))
            self._app_name_edit.blockSignals(False)
            self._app_exe_edit.blockSignals(False)
            self._app_args_edit.blockSignals(False)

    def _sync_app_field(self):
        row = self._apps_list.currentRow()
        if 0 <= row < len(self._apps_data):
            self._apps_data[row]["name"] = self._app_name_edit.text().strip()
            self._apps_data[row]["exe"]  = self._app_exe_edit.text().strip()
            self._apps_data[row]["args"] = self._app_args_edit.text().strip() or "%1"
            item = self._apps_list.item(row)
            if item:
                item.setText(self._apps_data[row]["name"] or "App")

    def _add_app(self):
        new_app = {"name": "New App", "exe": "", "args": '"%1"', "icon_b64": ""}
        self._apps_data.append(new_app)
        self._apps_list.addItem(new_app["name"])
        self._apps_list.setCurrentRow(len(self._apps_data) - 1)

    def _remove_app(self):
        row = self._apps_list.currentRow()
        if 0 <= row < len(self._apps_data):
            self._apps_data.pop(row)
            self._apps_list.takeItem(row)
            self._app_name_edit.clear()
            self._app_exe_edit.clear()
            self._app_args_edit.clear()

    def _browse_app_exe(self):
        import os
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Executable", "",
            "Executables (*.exe *.bat *.cmd);;All Files (*)")
        if not path:
            return
        self._app_exe_edit.setText(path)
        row = self._apps_list.currentRow()
        if 0 <= row < len(self._apps_data):
            # Auto-fill name from exe if name is still default
            name = self._apps_data[row].get("name", "")
            if not name or name == "New App":
                auto = os.path.splitext(os.path.basename(path))[0]
                self._app_name_edit.setText(auto)
            # Extract and store icon
            try:
                self._apps_data[row]["icon_b64"] = _extract_exe_icon_b64(path)
            except Exception:
                pass

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save_and_close(self):
        self._settings.set("remember_last_folder",
                           self._chk_remember.isChecked())
        self._settings.set("show_tasks_panel",
                           self._chk_show_tasks.isChecked())
        self._settings.set("confirm_delete",
                           self._chk_confirm_delete.isChecked())
        self._settings.set("auto_scan",
                           self._chk_auto_scan.isChecked())
        self._settings.set("preserve_metadata_on_edit",
                           self._chk_preserve_meta.isChecked())
        self._settings.set("ffmpeg_exe", self._ffmpeg_edit.text().strip())
        self._settings.set("font_folder", self._spin_font_folder.value())
        self._settings.set("font_image",  self._spin_font_image.value())
        self._settings.set("font_meta",   self._spin_font_meta.value())
        disabled = [ext for ext, chk in self._ext_checks.items()
                    if not chk.isChecked()]
        self._settings.set("disabled_extensions", disabled)

        self._settings.set("viewer_size_mode",
                           "remember" if self._radio_iv_remember.isChecked() else "fit")
        self._settings.set("viewer_default_zoom",
                           "100" if self._radio_100.isChecked() else "fit")
        self._settings.set("viewer_resize_on_zoom",
                           self._chk_resize_on_zoom.isChecked())
        self._settings.set("viewer_same_monitor",
                           self._chk_same_monitor.isChecked())
        self._settings.set("viewer_show_meta",
                           self._chk_show_meta.isChecked())
        self._settings.set("viewer_save_format",
                           self._save_fmt_combo.currentData())
        self._settings.set("pool_buffer",
                           self._spin_pool_buffer.value())
        self._settings.set("thumbsplus_db_path",
                           self._tp_path_edit.text().strip())
        if self._radio_db_readonly.isChecked():
            db_mode = "readonly"
        elif self._radio_db_import.isChecked():
            db_mode = "import"
        else:
            db_mode = "none"
        self._settings.set("thumbsplus_mode", db_mode)

        # Applications — sync any pending edits in the fields first
        row = self._apps_list.currentRow()
        if 0 <= row < len(self._apps_data):
            self._apps_data[row]["name"] = self._app_name_edit.text().strip()
            self._apps_data[row]["exe"]  = self._app_exe_edit.text().strip()
            self._apps_data[row]["args"] = self._app_args_edit.text().strip() or "%1"
        # Drop any entries with no exe
        self._settings.set("launch_apps",
                           [a for a in self._apps_data if a.get("exe")])
        self._settings.set("plugin_dirs", list(self._plugin_dirs_data))

        # ── AI page ──────────────────────────────────────────────────────────
        self._settings.set("ai_mode", self._ai_mode_combo.currentData())
        self._settings.set("ai_instruction", self._ai_instruction.toPlainText().strip())
        self._settings.set("ai_joycaption_gguf", self._ai_gguf.text().strip())
        self._settings.set("ai_joycaption_mmproj", self._ai_mmproj.text().strip())
        self._settings.set("ai_llama_dir", self._ai_llama_dir.text().strip())
        self._settings.set("ai_temperature", round(float(self._ai_temp.value()), 2))
        self._settings.set("ai_max_tokens", int(self._ai_maxtok.value()))
        self._settings.set("ai_n_ctx", int(self._ai_nctx.value()))
        self._settings.set("ai_n_gpu_layers", int(self._ai_ngl.value()))
        self._settings.set("ai_flash_attn", self._ai_fa.isChecked())
        self._settings.set("ai_cache_type_k", self._ai_ck.currentText())
        self._settings.set("ai_cache_type_v", self._ai_cv.currentText())
        self._settings.set("ai_internal_port", int(self._ai_int_port.value()))
        self._settings.set("ai_jarvis_caption_url", self._ai_jarvis_url.text().strip())
        self._settings.set("ai_tag_merge", self._ai_merge.isChecked())
        self._settings.set("ai_tag_aliases", self._ai_aliases.toPlainText().strip())
        self.accept()
