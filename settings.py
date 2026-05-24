"""
settings.py — Persistent JSON settings for ThumbsAI
Designed by: Zero  |  Built by: Jarvis
"""
from __future__ import annotations
import json
import threading
from pathlib import Path

from database import DATA_DIR

_SETTINGS_FILE = DATA_DIR / "settings.json"

_DEFAULTS: dict = {
    "remember_last_folder": False,
    "last_folder": "",
    "disabled_extensions": [],   # list of exts to hide, e.g. [".psd", ".exr"]
    "launch_apps": [],           # list of {name, exe, args, icon_b64}
    "favorites":   [],           # list of favorited folder paths
    "show_tasks_panel": True,    # show/hide the Tasks panel below the folder tree
    "confirm_delete": True,      # ask before sending to Recycle Bin
    # Font sizes (px)
    "font_folder":  9,
    "font_image":   9,
    "font_meta":    9,
    # Sort state
    "sort1":     "numeric name",
    "sort1_dir": "asc",
    "sort2":     "",
    "sort2_dir": "asc",
    "sort3":     "",
    "sort3_dir": "asc",
    # Icon bar button order (list of IDs)
    "icon_bar_order": [],

    # Image Viewer
    "pool_buffer":             4,          # rows pre-loaded above/below viewport
    "viewer_default_zoom":     "fit",      # "fit" | "100"
    "viewer_resize_on_zoom":   False,      # resize window to fit image on zoom
    "viewer_show_meta":        False,      # open with metadata panel visible
    "viewer_same_monitor":     True,       # open viewer on same monitor as main window
    "viewer_remember_size":    False,
    "viewer_width":            1200,
    "viewer_height":           800,
    # Image View — window sizing mode
    "viewer_size_mode":        "fit",      # "fit" | "remember"
    # Save Close default format
    "viewer_save_format":      "png",      # "png" | "jpg" | "webp" | "bmp" | "tiff"
    # Behaviour
    "auto_scan":                    True,   # scan folder on click
    "preserve_metadata_on_edit":    True,   # keep EXIF when saving edits
    # External tools
    "ffmpeg_exe":                   "",     # path to ffmpeg.exe; empty = auto-detect
    # ThumbsPlus integration
    "thumbsplus_db_path":      "",
    "thumbsplus_mode":         "none",   # "none" | "readonly" | "import"
    # Photoshop .8bf plugin directories
    "plugin_dirs":             [],       # list of folder paths to scan for .8bf plugins
    # Recent folders for copy/move (up to 10 each)
    "recent_copy_dirs":        [],
    "recent_move_dirs":        [],
    # Always-watched folders (survive restarts)
    "watched_folders":         [],
}


class AppSettings:
    def __init__(self):
        self._data: dict = dict(_DEFAULTS)
        self._save_timer: threading.Timer | None = None
        self._load()

    def _load(self):
        if _SETTINGS_FILE.exists():
            try:
                loaded = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
                self._data.update({k: v for k, v in loaded.items() if k in _DEFAULTS})
            except Exception:
                pass

    def save(self):
        try:
            _SETTINGS_FILE.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, _DEFAULTS.get(key, default))

    def set(self, key: str, value):
        self._data[key] = value
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.3, self.save)
        self._save_timer.daemon = True
        self._save_timer.start()
