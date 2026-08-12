"""
settings.py — Persistent JSON settings for ThumbsAI
Designed by: Zero  |  Built by: Jarvis
"""
from __future__ import annotations
import json
import threading

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

    # ── AI (auto-tagging via JoyCaption) ──────────────────────────────────────────
    # INTERNAL mode runs JoyCaption on a bundled/referenced llama-server; JARVIS mode reuses the
    # Suite's caption server. Paths default to the transplanted Q4_K model; edit for your own build.
    "ai_mode":                 "internal",     # "internal" | "jarvis"
    # Vulkan b10012 build travels WITH the app (llama_cpp/vulkan_b10012/) — a relative path resolved
    # against the ThumbsAI dir (caption_backend._resolve), so a fresh standalone install is fully
    # self-contained (no dependency on the Suite). Vulkan (not ROCm) is required: ROCm dropped Polaris,
    # so only the Vulkan build sees the RX 580 — see AI_Library/reference/vulkan-vs-rocm-7800xt.md.
    # An ABSOLUTE path here still works (e.g. point at the Suite's or VC Core's shared build to save disk).
    "ai_llama_dir":            "llama_cpp/vulkan_b10012",
    # Relative to the ThumbsAI folder -> its own copy travels with the app (model independence).
    # Swap in your finetuned JoyCaption here later.
    # NeoJoy V4 — the finetuned JoyCaption (Q5_K_M, fits the RX 580 with flash-attn). Its OWN copy lives
    # under models/joycaption/ — a relative path that resolves against the ThumbsAI app dir (_APP_DIR),
    # so the model travels with the standalone. The mmproj is unchanged (vision frozen during finetuning).
    "ai_joycaption_gguf":      "models/joycaption/NeoJoy-V4-Q5_K_M.gguf",
    "ai_joycaption_mmproj":    "models/joycaption/llama-joycaption-beta-one-llava-mmproj-model-f16.gguf",
    "ai_internal_port":        8082,           # ThumbsAI's own JoyCaption server (avoid Jarvis 8080/8081)
    # Pin JoyCaption to a specific Vulkan GPU (GGML_VK_VISIBLE_DEVICES). "1" = the RX 580 on this box
    # (Vulkan0=RX 7800 XT, Vulkan1=RX 580), so captioning stays off the main render GPU. "" = don't pin.
    "ai_vulkan_device":        "1",
    "ai_jarvis_caption_url":   "http://127.0.0.1:8081",   # Jarvis's caption server (JARVIS mode)
    "ai_tag_merge":            True,           # merge AI tags with existing tags (vs overwrite)
    # User tag-alias additions (Settings ▸ AI ▸ Tag alias map), layered over the built-in booru aliases
    # in ai_ops._TAG_ALIASES. One 'from = to' per line; blank/'#'-comment lines ignored. Add terms here
    # anytime — a tag matching 'from' is rewritten to 'to' on every pass. Empty = built-ins only.
    "ai_tag_aliases":          "",
    # ThumbsAI persona — the captioner's job description (how it should tag). Editable in Settings ▸ AI.
    # BOORU style: underscore_separated single-token tags = reliable, consistent search atoms.
    "ai_instruction": (
        "Tag this image in DANBOORU (booru) style: a comma-separated list of concise lowercase tags "
        "with underscores instead of spaces — e.g. 1girl, solo, long_hair, red_hair, blue_eyes, "
        "large_breasts, denim_jacket, outdoors, park, night, standing, looking_at_viewer, "
        "photorealistic. Order roughly: subject count (1girl / 1boy / solo / 2girls) first, then "
        "body and appearance (hair, eyes, build), then clothing or state of dress, then setting or "
        "location, then action or pose, then art style or medium. Prefer established booru tags you "
        "already know. Output ONLY the tags separated by commas — lowercase, underscores_between_words, "
        "no sentences, no articles, no explanations."),
    # LLM parameters (JoyCaption load preset + sampling) — mirrors Jarvis's per-model fields.
    "ai_temperature":          0.4,
    "ai_max_tokens":           200,
    "ai_n_ctx":                4096,
    "ai_n_gpu_layers":         -1,             # -1 = all layers on GPU
    "ai_flash_attn":           True,
    "ai_cache_type_k":         "f16",          # f16 | q8_0 | q4_0
    "ai_cache_type_v":         "f16",
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
