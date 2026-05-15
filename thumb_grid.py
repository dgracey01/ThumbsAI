"""
thumb_grid.py — Thumbnail grid, cards, worker, and image viewer for ThumbsAI
Designed by: Zero  |  Built by: Jarvis

Fast browsing via SQLite thumbnail cache:
  - First visit: thumbnails generated in background, stored as JPEG BLOBs
  - Subsequent visits: loaded directly from DB — millisecond display
"""
from __future__ import annotations
import os
import base64
import shutil
import subprocess
from collections import deque
from datetime import datetime
from io    import BytesIO
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QHBoxLayout, QGridLayout, QMainWindow,
    QTextEdit, QLineEdit, QSizePolicy, QSplitter, QGraphicsView,
    QGraphicsScene, QGraphicsPixmapItem, QMenu, QMessageBox,
    QProgressBar, QApplication, QDialog, QFileDialog, QInputDialog,
    QRubberBand, QSpinBox, QCheckBox, QSlider,
)
from PySide6.QtCore  import Qt, Signal, QObject, QThread, QTimer, QRectF, QPoint, QUrl, QRect, QSize, QBuffer, QIODevice, QThreadPool, QRunnable
from PySide6.QtGui   import QPixmap, QImage, QColor, QPainter, QFont, QWheelEvent, QKeyEvent, QContextMenuEvent, QIcon, QDrag, QTransform, QPolygon

from theme import (
    BG, PAN, CAR, ACC, GRN, RED, MUT, AMB,
    PRI, SEC, FONT, FONT_SM, FONT_MD, FONT_LG,
)
from database    import ThumbsDB
from ai_metadata import parse_png_metadata

_ASSETS = Path(__file__).parent / "assets"

def _asset_icon(filename: str) -> QIcon:
    p = _ASSETS / filename
    return QIcon(str(p)) if p.exists() else QIcon()

SUPPORTED_EXTS = {
    # Common
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
    # TIFF
    ".tiff", ".tif",
    # High-quality / lossless
    ".avif", ".heic", ".heif", ".jxl",
    # Legacy
    ".ppm", ".pgm", ".pbm", ".pnm", ".ico", ".cur",
    # Photoshop / layered (Pillow reads flattened)
    ".psd",
    # HDR / EXR (if Pillow-HDR or OpenEXR installed)
    ".hdr", ".exr",
    # Text (caption / prompt sidecar files)
    ".txt",
    # ZIP archives containing images
    ".zip",
    # AI model weights — no image, shows metadata readout
    ".safetensors",
    # Video — listed and opened with default system player
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".vob",
    ".3gp", ".ogv", ".divx", ".rmvb",
}

VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".vob",
    ".3gp", ".ogv", ".divx", ".rmvb",
}

# Grouped extension list used by the File Types settings page.
# Each entry: (group_label, [(ext, display_name), ...])
FILE_TYPE_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Common", [
        (".png",  "PNG  —  Portable Network Graphics"),
        (".jpg",  "JPG  —  JPEG"),
        (".jpeg", "JPEG  —  JPEG (alternate extension)"),
        (".webp", "WebP"),
        (".gif",  "GIF  —  Graphics Interchange Format"),
        (".bmp",  "BMP  —  Windows Bitmap"),
    ]),
    ("TIFF", [
        (".tiff", "TIFF  —  Tagged Image File Format"),
        (".tif",  "TIF  —  TIFF (alternate extension)"),
    ]),
    ("High Quality / Lossless", [
        (".avif", "AVIF  —  AV1 Image File Format"),
        (".heic", "HEIC  —  High Efficiency Image"),
        (".heif", "HEIF  —  High Efficiency Image (alternate extension)"),
        (".jxl",  "JXL  —  JPEG XL"),
    ]),
    ("Legacy", [
        (".ppm", "PPM  —  Portable Pixmap"),
        (".pgm", "PGM  —  Portable Graymap"),
        (".pbm", "PBM  —  Portable Bitmap"),
        (".pnm", "PNM  —  Portable Any Map"),
        (".ico", "ICO  —  Windows Icon"),
        (".cur", "CUR  —  Windows Cursor"),
    ]),
    ("Photoshop", [
        (".psd", "PSD  —  Photoshop Document (flattened via Pillow)"),
    ]),
    ("HDR / EXR", [
        (".hdr", "HDR  —  High Dynamic Range"),
        (".exr", "EXR  —  OpenEXR (requires OpenEXR library)"),
    ]),
    ("Other", [
        (".txt", "TXT  —  Text / caption sidecar"),
        (".zip", "ZIP  —  ZIP archive (images inside)"),
    ]),
    ("AI Models", [
        (".safetensors", "Safetensors  —  AI model weights (metadata readout)"),
    ]),
    ("Video", [
        (".mp4",  "MP4  —  MPEG-4 Video"),
        (".mkv",  "MKV  —  Matroska Video"),
        (".avi",  "AVI  —  Audio Video Interleave"),
        (".mov",  "MOV  —  QuickTime Movie"),
        (".wmv",  "WMV  —  Windows Media Video"),
        (".flv",  "FLV  —  Flash Video"),
        (".webm", "WebM  —  WebM Video"),
        (".m4v",  "M4V  —  iTunes Video"),
        (".mpg",  "MPG  —  MPEG Video"),
        (".mpeg", "MPEG  —  MPEG Video (alternate extension)"),
        (".ts",   "TS  —  MPEG Transport Stream"),
        (".mts",  "MTS  —  AVCHD Video"),
        (".m2ts", "M2TS  —  Blu-ray Video"),
        (".vob",  "VOB  —  DVD Video"),
        (".3gp",  "3GP  —  3GPP Mobile Video"),
        (".ogv",  "OGV  —  Ogg Video"),
        (".divx", "DIVX  —  DivX Video"),
        (".rmvb", "RMVB  —  RealMedia Variable Bitrate"),
    ]),
]
DEFAULT_THUMB  = 180
MIN_THUMB      = 80
MAX_THUMB      = 320


# ── ffmpeg path resolution ───────────────────────────────────────────────────

_FFMPEG_EXE: str = ""   # overridden by ThumbGrid.__init__ from settings


def _find_ffmpeg() -> str:
    """Return path to ffmpeg.exe, or '' if not found.
    Order: settings override → system PATH → common Windows locations → env vars."""
    if _FFMPEG_EXE and Path(_FFMPEG_EXE).is_file():
        return _FFMPEG_EXE

    found = shutil.which("ffmpeg")
    if found:
        return found

    candidates: list[str] = []

    # Env-var hints
    for var in ("FFMPEG_HOME", "FFMPEG_DIR", "FFMPEG_PATH"):
        val = os.environ.get(var, "")
        if val:
            candidates += [
                str(Path(val) / "ffmpeg.exe"),
                str(Path(val) / "bin" / "ffmpeg.exe"),
            ]

    # Common fixed locations
    candidates += [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
    ]

    # Per-user package managers (scoop, winget)
    user = os.environ.get("USERPROFILE", "")
    if user:
        candidates += [
            str(Path(user) / "scoop" / "apps" / "ffmpeg" / "current" / "bin" / "ffmpeg.exe"),
            str(Path(user) / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe"),
        ]

    for c in candidates:
        if Path(c).is_file():
            return c
    return ""


# ── Video thumbnail extractor ─────────────────────────────────────────────────

def _extract_video_thumb(path: str, size: int) -> tuple[bytes | None, int, int]:
    """Return (jpeg_bytes, width, height) for the middle frame of a video.
    Tries cv2 → PyAV → system ffmpeg in order."""
    # ── cv2 ──────────────────────────────────────────────────────────────────
    try:
        import cv2
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            return None, 0, 0
        w     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 2))
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            return None, w, h
        from PIL import Image as _PIL
        from io import BytesIO as _BytesIO
        img = _PIL.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        img.thumbnail((size, size), _PIL.LANCZOS)
        buf = _BytesIO()
        img.save(buf, "JPEG", quality=82)
        return buf.getvalue(), w, h
    except ImportError:
        pass
    except Exception:
        pass
    # ── PyAV (ships bundled ffmpeg — pip install av) ──────────────────────────
    result = _extract_video_thumb_av(path, size)
    if result[0]:
        return result
    # ── system ffmpeg ─────────────────────────────────────────────────────────
    return _extract_video_thumb_ffmpeg(path, size)


def _extract_video_thumb_av(path: str, size: int) -> tuple[bytes | None, int, int]:
    """PyAV backend — no system ffmpeg needed, installs as: pip install av"""
    try:
        import av
        from io import BytesIO as _BytesIO
        from PIL import Image as _PIL
        container = av.open(path)
        video = container.streams.video[0]
        w = video.codec_context.width  or 0
        h = video.codec_context.height or 0
        # container.duration is in AV_TIME_BASE units (microseconds)
        dur = float(container.duration or 0) / 1_000_000
        if dur > 1.0:
            container.seek(int(dur / 2 * 1_000_000))
        for frame in container.decode(video=0):
            img = frame.to_image()
            w   = w or img.width
            h   = h or img.height
            img.thumbnail((size, size), _PIL.LANCZOS)
            buf = _BytesIO()
            img.save(buf, "JPEG", quality=82)
            container.close()
            return buf.getvalue(), w, h
        container.close()
        return None, w, h
    except ImportError:
        return None, 0, 0
    except Exception:
        return None, 0, 0


def _extract_video_thumb_ffmpeg(path: str, size: int) -> tuple[bytes | None, int, int]:
    """ffmpeg fallback: probe duration/dimensions, then seek to middle frame.
    Works with ffmpeg alone — ffprobe is used when available but not required."""
    import re, tempfile
    ff = _find_ffmpeg()
    if not ff:
        return None, 0, 0

    w, h, dur = 0, 0, 0.0

    # Prefer ffprobe for clean JSON output; check same dir as ffmpeg first
    _ffprobe_sib = Path(ff).with_name("ffprobe.exe")
    ffprobe = (str(_ffprobe_sib) if _ffprobe_sib.is_file()
               else shutil.which("ffprobe"))
    if ffprobe:
        try:
            import json
            probe = subprocess.run(
                [ffprobe, "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-show_format", "-select_streams", "v:0", path],
                capture_output=True, timeout=15)
            info   = json.loads(probe.stdout)
            stream = (info.get("streams") or [{}])[0]
            w   = int(stream.get("width",  0) or 0)
            h   = int(stream.get("height", 0) or 0)
            dur = float(stream.get("duration")
                        or info.get("format", {}).get("duration") or 0)
        except Exception:
            pass

    # Fall back to parsing ffmpeg -i stderr (works when only ffmpeg is installed)
    if dur == 0:
        try:
            result = subprocess.run(
                [ff, "-i", path], capture_output=True, timeout=10)
            stderr = result.stderr.decode("utf-8", errors="replace")
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", stderr)
            if m:
                dur = (int(m.group(1)) * 3600
                       + int(m.group(2)) * 60
                       + float(m.group(3)))
            if w == 0:
                dm = re.search(r",\s*(\d{2,5})x(\d{2,5})", stderr)
                if dm:
                    w, h = int(dm.group(1)), int(dm.group(2))
        except Exception:
            pass

    mid = max(0.5, dur / 2) if dur > 0 else 10.0
    tmp = None
    try:
        from PIL import Image as _PIL
        from io import BytesIO as _BytesIO
        fd, tmp = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        subprocess.run(
            [ff, "-ss", f"{mid:.3f}", "-i", path,
             "-vframes", "1", "-q:v", "2", "-y", tmp],
            capture_output=True, timeout=30)
        if not os.path.getsize(tmp):
            return None, w, h
        img = _PIL.open(tmp)
        img.load()
        w   = w or img.width
        h   = h or img.height
        img.thumbnail((size, size), _PIL.LANCZOS)
        buf = _BytesIO()
        img.save(buf, "JPEG", quality=82)
        return buf.getvalue(), w, h
    except Exception:
        return None, w, h
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ── Perceptual hash helpers (for Sort by Similarity) ─────────────────────────

def _ahash(img_bytes: bytes) -> int:
    """8×8 average hash — returns 64-bit int. Fast, PIL-only."""
    from PIL import Image
    import io
    img     = Image.open(io.BytesIO(img_bytes)).convert("L").resize((8, 8), Image.LANCZOS)
    pixels  = list(img.getdata())
    avg     = sum(pixels) / 64
    return int("".join("1" if p >= avg else "0" for p in pixels), 2)


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _read_safetensors_meta(filepath: str) -> str:
    """
    Read the JSON header from a safetensors file and return a human-readable
    summary: tensor count, dtype breakdown, total parameter count, and any
    __metadata__ key/value pairs.
    """
    import json, struct
    try:
        with open(filepath, "rb") as f:
            raw_len = f.read(8)
            if len(raw_len) < 8:
                return "Invalid safetensors file"
            header_len = struct.unpack_from("<Q", raw_len)[0]
            if header_len > 100_000_000:   # sanity cap: 100 MB header
                return "Header too large"
            header_bytes = f.read(header_len)
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception as ex:
        return f"Read error:\n{ex}"

    meta      = header.get("__metadata__", {})
    tensors   = {k: v for k, v in header.items() if k != "__metadata__"}
    dtype_cnt: dict[str, int] = {}
    total_params = 0
    for info in tensors.values():
        if not isinstance(info, dict):
            continue
        dtype = info.get("dtype", "?")
        dtype_cnt[dtype] = dtype_cnt.get(dtype, 0) + 1
        shape = info.get("shape", [])
        if shape:
            p = 1
            for d in shape:
                p *= d
            total_params += p

    lines = [
        f"Tensors : {len(tensors)}",
        f"Params  : {total_params/1e6:.1f} M" if total_params else "Params  : ?",
        "─" * 18,
    ]
    for dtype, cnt in sorted(dtype_cnt.items()):
        lines.append(f"  {dtype:<10} ×{cnt}")
    if meta:
        lines.append("─" * 18)
        for k, v in list(meta.items())[:8]:   # cap at 8 meta keys
            v_str = str(v)
            if len(v_str) > 18:
                v_str = v_str[:16] + "…"
            lines.append(f"{k[:10]}: {v_str}")
    return "\n".join(lines)
THUMB_PADDING  = 12    # px around image inside card
LABEL_HEIGHT   = 46    # px for filename + info text below image
CARD_GAP       = 6     # spacing between cards (px)
CARD_MARGIN    = 6     # outer grid margin (px)
POOL_BUFFER    = 2     # extra rows above/below viewport kept in pool
MAX_CACHE_PX   = 256   # max decoded QPixmaps held in LRU cache

SOURCE_COLORS = {
    "A1111":    "#1f6feb",
    "ComfyUI":  "#2ea043",
    "NovelAI":  "#9b59b6",
    "InvokeAI": "#EF9F27",
}


# ── Lazy recursive file iterator (used by _ThumbWorker) ──────────────────────

def _iter_folder(root: "Path", exts: frozenset) -> "Iterator[tuple[Path, float]]":
    """
    Recursively yield (Path, mtime) for every matching file under root.
    Uses os.scandir DirEntry.stat() which on Windows is cached from the
    FindNextFile syscall — no extra stat() round-trip per file.
    """
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        yield from _iter_folder(Path(entry.path), exts)
                    elif entry.is_file(follow_symlinks=False):
                        if Path(entry.name).suffix.lower() in exts:
                            yield Path(entry.path), entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
    except OSError:
        pass


# ── Thumbnail generation worker ───────────────────────────────────────────────

class _ThumbWorker(QThread):
    """
    Background worker: scans a folder, generates thumbnails and reads AI
    metadata for any file not yet in the DB (or whose file has been modified).
    Emits one signal per completed image so the UI updates progressively.
    """
    image_ready = Signal(str, dict)   # filepath, db_row dict
    progress    = Signal(str)         # status message
    finished    = Signal(int, int)    # added, skipped

    def __init__(self, folder: str, thumb_size: int, db: ThumbsDB,
                 enabled_exts: set | None = None):
        super().__init__()
        self._folder       = folder
        self._thumb_size   = thumb_size
        self._db           = db
        self._enabled_exts = enabled_exts if enabled_exts is not None else SUPPORTED_EXTS

    # ── Image extensions recognised inside ZIP archives ───────────────────────
    _ZIP_IMG_EXTS = {
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
        ".tiff", ".tif", ".avif", ".heic", ".heif", ".jxl",
        ".ppm", ".pgm", ".pbm", ".pnm",
    }

    # Number of threads used for parallel thumbnail/hash/metadata work.
    # I/O-bound work benefits from more threads than CPU cores; cap at 16.
    _WORKERS = min(16, max(4, (os.cpu_count() or 4) * 2))

    # Rows are accumulated and written in batches to amortise SQLite commit cost.
    # Larger batches → fewer commits, but first visible results appear later.
    _BATCH_SIZE = 32

    def run(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from database import _file_hash

        folder  = self._folder
        t       = self._thumb_size
        # cached must cover all sub-folders — query DB for the root prefix
        cached  = self._db.cached_filepaths_recursive(folder)
        added   = 0
        skipped = 0

        # ── Single lazy pass: enumerate files + mtime, separate ZIPs, filter ──
        # _iter_folder uses os.scandir DirEntry.stat() — mtime is cached from
        # the directory listing syscall on Windows, no extra stat() per file.
        zip_files:  list[Path]               = []
        to_process: list[tuple[Path, float]] = []

        for fp, mtime in _iter_folder(Path(folder), frozenset(self._enabled_exts)):
            if self.isInterruptionRequested():
                break
            sfx = fp.suffix.lower()
            if sfx == ".zip":
                zip_files.append(fp)
                continue
            path_str = str(fp)
            cached_mtime, _cached_hash, cached_has_thumb = cached.get(
                path_str, (None, None, False))
            if cached_mtime == mtime:
                is_video_no_thumb = sfx in VIDEO_EXTS and not cached_has_thumb
                if not is_video_no_thumb:
                    skipped += 1
                    continue
            to_process.append((fp, mtime))

        # ── Process ZIPs sequentially ─────────────────────────────────────────
        for fp in zip_files:
            if self.isInterruptionRequested():
                break
            a, s = self._process_zip(fp, t, cached)
            added   += a
            skipped += s

        if self.isInterruptionRequested():
            self.finished.emit(added, skipped)
            return

        n_work = len(to_process)
        if n_work == 0:
            self.finished.emit(added, skipped)
            return

        self.progress.emit(f"Processing {n_work:,} new/changed files…")

        # ── Worker function — runs in thread pool ─────────────────────────────
        def _process_one(fp: Path, mtime: float):
            """Return a ready-to-upsert dict, or None on skip/error."""
            if self.isInterruptionRequested():
                return None
            path_str = str(fp)
            fp_sfx   = fp.suffix.lower()
            try:
                fsize = fp.stat().st_size
            except OSError:
                return None

            if fp_sfx == ".safetensors":
                thumb_bytes, w, h = None, 0, 0
                file_hash = _file_hash(path_str)
                meta      = {}
            elif fp_sfx in VIDEO_EXTS:
                thumb_bytes, w, h = _extract_video_thumb(path_str, t)
                file_hash = ""
                meta      = {}
            else:
                try:
                    from PIL import Image as _PIL
                    with _PIL.open(path_str) as img:
                        w, h = img.size
                        thumb_img = img.copy()
                        thumb_img.thumbnail((t, t), _PIL.LANCZOS)
                        buf = BytesIO()
                        thumb_img.save(buf, format="JPEG", quality=82)
                        thumb_bytes = buf.getvalue()
                except Exception:
                    return None
                # Hash and metadata parse run concurrently with the next PIL open
                file_hash = _file_hash(path_str)
                meta      = parse_png_metadata(path_str)

            return {
                "filepath":        path_str,
                "thumbnail":       thumb_bytes,
                "width":           w,
                "height":          h,
                "filesize":        fsize,
                "modified_at":     mtime,
                "file_hash":       file_hash,
                "prompt":          meta.get("prompt",          ""),
                "negative_prompt": meta.get("negative_prompt", ""),
                "seed":            meta.get("seed",            ""),
                "model":           meta.get("model",           ""),
                "sampler":         meta.get("sampler",         ""),
                "cfg_scale":       meta.get("cfg_scale",       ""),
                "steps":           meta.get("steps",           ""),
                "source":          meta.get("source",          ""),
                "raw_meta":        meta.get("raw_meta",        ""),
            }

        # ── Parallel execution with batch DB writes ────────────────────────────
        batch: list[dict] = []
        done  = 0

        with ThreadPoolExecutor(max_workers=self._WORKERS) as pool:
            futures = {
                pool.submit(_process_one, fp, mtime): (fp, mtime)
                for fp, mtime in to_process
            }
            for fut in as_completed(futures):
                if self.isInterruptionRequested():
                    break
                done += 1
                rec = fut.result()
                if rec is None:
                    skipped += 1
                    continue

                batch.append(rec)

                # Flush batch to DB and notify UI
                if len(batch) >= self._BATCH_SIZE:
                    self._flush_batch(batch)
                    added += len(batch)
                    batch = []
                    self.progress.emit(
                        f"Processing…  {done:,}/{n_work:,}  ({added:,} new)")

        # Flush any remaining records
        if batch and not self.isInterruptionRequested():
            self._flush_batch(batch)
            added += len(batch)

        self.finished.emit(added, skipped)

    def _flush_batch(self, batch: list[dict]) -> None:
        """Write a batch of processed records to the DB and emit image_ready signals."""
        self._db.batch_upsert(batch, commit=True)
        for rec in batch:
            fp  = rec["filepath"]
            row = dict(rec)
            row.setdefault("filename",  Path(fp).name)
            row.setdefault("folder",    str(Path(fp).parent))
            row.setdefault("added_at",  "")
            row.setdefault("rating",    0)
            row.setdefault("tags",      "")
            self.image_ready.emit(fp, row)

    def _process_zip(self, zip_path: Path, t: int, cached: dict) -> tuple[int, int]:
        """Process images inside a ZIP — virtual paths: zip_path::member_name"""
        import zipfile
        added   = 0
        skipped = 0
        try:
            zip_mtime = zip_path.stat().st_mtime
            zip_str   = str(zip_path)
            with zipfile.ZipFile(zip_str, "r") as zf:
                for name in zf.namelist():
                    if Path(name).suffix.lower() not in self._ZIP_IMG_EXTS:
                        continue
                    if self.isInterruptionRequested():
                        break
                    virtual = f"{zip_str}::{name}"
                    # Use zip mtime as cache key for members
                    cached_mtime, _, _has_thumb = cached.get(virtual, (None, None, False))
                    if cached_mtime == zip_mtime:
                        skipped += 1
                        continue
                    try:
                        from PIL import Image as _PIL
                        data = zf.read(name)
                        with _PIL.open(BytesIO(data)) as img:
                            w, h = img.size
                            thumb_img = img.copy()
                            thumb_img.thumbnail((t, t), _PIL.LANCZOS)
                            buf = BytesIO()
                            thumb_img.save(buf, format="JPEG", quality=82)
                            thumb_bytes = buf.getvalue()
                        meta = parse_png_metadata(None, raw_bytes=data)
                    except Exception:
                        skipped += 1
                        continue
                    self._db.upsert(virtual,
                        thumbnail       = thumb_bytes,
                        width           = w,
                        height          = h,
                        filesize        = len(data),
                        modified_at     = zip_mtime,
                        prompt          = meta.get("prompt",          ""),
                        negative_prompt = meta.get("negative_prompt", ""),
                        seed            = meta.get("seed",            ""),
                        model           = meta.get("model",           ""),
                        sampler         = meta.get("sampler",         ""),
                        cfg_scale       = meta.get("cfg_scale",       ""),
                        steps           = meta.get("steps",           ""),
                        source          = meta.get("source",          ""),
                        raw_meta        = meta.get("raw_meta",        ""),
                    )
                    self.image_ready.emit(virtual, {
                        "filepath":        virtual,
                        "filename":        name,
                        "folder":          str(zip_path.parent),
                        "thumbnail":       thumb_bytes,
                        "width":           w,
                        "height":          h,
                        "filesize":        len(data),
                        "modified_at":     zip_mtime,
                        "prompt":          meta.get("prompt",          ""),
                        "negative_prompt": meta.get("negative_prompt", ""),
                        "seed":            meta.get("seed",            ""),
                        "model":           meta.get("model",           ""),
                        "sampler":         meta.get("sampler",         ""),
                        "cfg_scale":       meta.get("cfg_scale",       ""),
                        "steps":           meta.get("steps",           ""),
                        "source":          meta.get("source",          ""),
                        "raw_meta":        meta.get("raw_meta",        ""),
                        "added_at":        "",
                        "rating":          0,
                        "tags":            "",
                    })
                    added += 1
        except Exception:
            pass
        return added, skipped


# ── Zoom-capable image view ───────────────────────────────────────────────────

class _ImageView(QGraphicsView):
    context_menu_requested = Signal(QPoint)   # global pos
    zoom_changed           = Signal()          # emitted after every wheel zoom step
    crop_confirmed         = Signal(QRectF)    # scene-space crop rect on mouse release

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item: QGraphicsPixmapItem | None = None
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setStyleSheet(f"background:{BG};border:none;")
        self._zoom = 1.0
        self._crop_mode   = False
        self._crop_origin: QPoint | None = None
        self._rubber_band: QRubberBand | None = None

    def set_pixmap(self, px: QPixmap):
        self._scene.clear()
        self._item = QGraphicsPixmapItem(px)
        self._item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.addItem(self._item)
        self._scene.setSceneRect(QRectF(px.rect()))
        self.resetTransform()
        self._zoom = 1.0
        self.fit_in_view()

    def fit_in_view(self):
        if self._item:
            self.fitInView(self._scene.sceneRect(), Qt.KeepAspectRatio)

    def update_pixmap(self, px: QPixmap):
        """Replace the displayed pixmap without resetting zoom or scroll."""
        if self._item:
            self._item.setPixmap(px)
            self._scene.setSceneRect(QRectF(px.rect()))
        else:
            self.set_pixmap(px)

    def display_size(self) -> tuple[float, float]:
        """Return the current on-screen pixel dimensions of the image."""
        r = self._scene.sceneRect()
        t = self.transform()
        return r.width() * t.m11(), r.height() * abs(t.m22())

    def set_crop_mode(self, enabled: bool):
        self._crop_mode = enabled
        if enabled:
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)
        else:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.setCursor(Qt.ArrowCursor)
            if self._rubber_band:
                self._rubber_band.hide()
            self._crop_origin = None

    def mousePressEvent(self, e):
        if self._crop_mode and e.button() == Qt.LeftButton:
            self._crop_origin = e.pos()
            if self._rubber_band is None:
                self._rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
            self._rubber_band.setGeometry(QRect(self._crop_origin, QSize()))
            self._rubber_band.show()
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._crop_mode and self._crop_origin is not None:
            self._rubber_band.setGeometry(
                QRect(self._crop_origin, e.pos()).normalized())
        else:
            super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._crop_mode and e.button() == Qt.LeftButton and self._crop_origin is not None:
            view_rect = QRect(self._crop_origin, e.pos()).normalized()
            tl = self.mapToScene(view_rect.topLeft())
            br = self.mapToScene(view_rect.bottomRight())
            self._crop_origin = None
            if self._rubber_band:
                self._rubber_band.hide()
            self.crop_confirmed.emit(QRectF(tl, br))
        else:
            super().mouseReleaseEvent(e)

    def wheelEvent(self, e: QWheelEvent):
        if self._crop_mode:
            return
        factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self._zoom *= factor
        self.scale(factor, factor)
        self.zoom_changed.emit()

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() == Qt.Key_0:
            self.fit_in_view()
        elif e.key() == Qt.Key_Escape and self._crop_mode:
            self.set_crop_mode(False)
        else:
            super().keyPressEvent(e)

    def contextMenuEvent(self, e: QContextMenuEvent):
        if not self._crop_mode:
            self.context_menu_requested.emit(e.globalPos())


# ── Resize dialog ────────────────────────────────────────────────────────────

class _ResizeDialog(QDialog):
    def __init__(self, width: int, height: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Resize Image")
        self.setFixedSize(280, 160)
        self.setStyleSheet(
            f"background:{BG};color:{PRI};font-family:{FONT};font-size:{FONT_MD}px;")
        self._orig_w = width
        self._orig_h = height
        self._lock   = True

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 12, 16, 12)
        v.setSpacing(8)

        _spin_ss = (f"QSpinBox{{background:{CAR};color:{PRI};border:2px solid {MUT};"
                    f"border-radius:4px;padding:2px 6px;font-size:{FONT_MD}px;}}"
                    f"QSpinBox:focus{{border-color:{ACC};}}"
                    f"QSpinBox::up-button,QSpinBox::down-button{{width:16px;}}")

        row_w = QWidget(); row_w.setStyleSheet("background:transparent;")
        rh = QHBoxLayout(row_w); rh.setContentsMargins(0,0,0,0); rh.setSpacing(8)
        rh.addWidget(QLabel("W:"))
        self._w_spin = QSpinBox(); self._w_spin.setRange(1, 65535)
        self._w_spin.setValue(width); self._w_spin.setStyleSheet(_spin_ss)
        rh.addWidget(self._w_spin)
        rh.addWidget(QLabel("H:"))
        self._h_spin = QSpinBox(); self._h_spin.setRange(1, 65535)
        self._h_spin.setValue(height); self._h_spin.setStyleSheet(_spin_ss)
        rh.addWidget(self._h_spin)
        v.addWidget(row_w)

        _chk_ss = (f"QCheckBox{{color:{PRI};font-size:{FONT_SM}px;spacing:6px;"
                   f"background:transparent;}}"
                   f"QCheckBox::indicator{{width:14px;height:14px;"
                   f"border:1px solid {MUT};border-radius:3px;background:{CAR};}}"
                   f"QCheckBox::indicator:checked{{background:{ACC};border-color:{ACC};}}")
        self._lock_chk = QCheckBox("Lock aspect ratio")
        self._lock_chk.setChecked(True)
        self._lock_chk.setStyleSheet(_chk_ss)
        v.addWidget(self._lock_chk)

        btn_row = QWidget(); btn_row.setStyleSheet("background:transparent;")
        bh = QHBoxLayout(btn_row); bh.setContentsMargins(0,0,0,0); bh.setSpacing(8)
        bh.addStretch()
        _btn_ss = (f"QPushButton{{background:{MUT};color:{PRI};border:none;"
                   f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
                   f"font-weight:bold;padding:4px 14px;}}"
                   f"QPushButton:hover{{background:{ACC};}}")
        ok  = QPushButton("Apply"); ok.setStyleSheet(_btn_ss)
        cxl = QPushButton("Cancel"); cxl.setStyleSheet(_btn_ss)
        ok.clicked.connect(self.accept)
        cxl.clicked.connect(self.reject)
        bh.addWidget(ok); bh.addWidget(cxl)
        v.addWidget(btn_row)

        self._w_spin.valueChanged.connect(self._on_w_changed)
        self._h_spin.valueChanged.connect(self._on_h_changed)
        self._lock_chk.toggled.connect(lambda c: setattr(self, '_lock', c))

    def _on_w_changed(self, val: int):
        if self._lock and self._orig_w:
            self._h_spin.blockSignals(True)
            self._h_spin.setValue(max(1, round(val * self._orig_h / self._orig_w)))
            self._h_spin.blockSignals(False)

    def _on_h_changed(self, val: int):
        if self._lock and self._orig_h:
            self._w_spin.blockSignals(True)
            self._w_spin.setValue(max(1, round(val * self._orig_w / self._orig_h)))
            self._w_spin.blockSignals(False)

    def new_size(self) -> tuple[int, int]:
        return self._w_spin.value(), self._h_spin.value()


_EDIT_W = 300   # edit panel width in pixels
_META_W = 320   # meta panel width in pixels

# ── Full-size image viewer dialog ─────────────────────────────────────────────

class ImageViewer(QMainWindow):
    def __init__(self, images: list[dict], start_idx: int = 0,
                 settings=None, db=None, on_thumb_changed=None, parent=None):
        super().__init__(parent)
        self._images           = images
        self._idx              = start_idx
        self._settings         = settings
        self._db               = db
        self._on_thumb_changed = on_thumb_changed
        self._font_meta   = int((settings.get("font_meta") or FONT_SM)
                                if settings else FONT_SM)
        self._modified_px:  QPixmap | None = None   # committed in-memory state
        self._edit_base_px: QPixmap | None = None   # base for current edit session
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self._compute_preview)
        self.setWindowTitle("ThumbsAI — Image Viewer")
        self.setMinimumSize(480, 360)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(f"background:{BG};color:{PRI};font-family:{FONT};")

        _root = QWidget()
        _root.setStyleSheet(f"background:{BG};")
        self.setCentralWidget(_root)

        v = QVBoxLayout(_root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # ── Navigation toolbar ────────────────────────────────────────────────
        bar = QFrame()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f"background:{PAN};border-bottom:1px solid {MUT};")
        bh = QHBoxLayout(bar)
        bh.setContentsMargins(8, 0, 8, 0)
        bh.setSpacing(6)

        # ── Save group ────────────────────────────────────────────────────────
        self._save_btn       = self._mk_btn("Save",       MUT)
        self._save_as_btn    = self._mk_btn("Save As",    MUT)
        self._save_close_btn = self._mk_btn("Save Close", MUT)
        self._save_btn.setToolTip("Overwrite original file with current state")
        self._save_as_btn.setToolTip("Save current state to a new file")
        self._save_close_btn.setToolTip("Save and close viewer")
        self._save_btn.clicked.connect(self._save)
        self._save_as_btn.clicked.connect(self._save_as)
        self._save_close_btn.clicked.connect(self._save_close)

        def _vsep():
            s = QFrame(); s.setFrameShape(QFrame.Shape.VLine)
            s.setStyleSheet(f"color:{MUT};"); s.setFixedHeight(20)
            return s

        # ── Navigation ────────────────────────────────────────────────────────
        self._prev_btn = self._mk_btn("← Prev", MUT)
        self._next_btn = self._mk_btn("Next →",  MUT)
        self._fit_btn  = self._mk_btn("Fit",     MUT)
        self._100_btn  = self._mk_btn("100%",    MUT)
        self._prev_btn.clicked.connect(self._prev)
        self._next_btn.clicked.connect(self._next)
        self._fit_btn.clicked.connect(lambda: None)   # rewired after _view exists
        self._100_btn.clicked.connect(self._zoom_100)

        # ── Panel toggles ─────────────────────────────────────────────────────
        self._meta_btn = self._mk_btn("Meta", MUT)
        self._meta_btn.setCheckable(True)
        self._meta_btn.setToolTip("Show / hide metadata panel")
        self._meta_btn.toggled.connect(self._toggle_meta)

        self._edit_btn = self._mk_btn("Edit", MUT)
        self._edit_btn.setCheckable(True)
        self._edit_btn.setToolTip("Show / hide edit panel")
        self._edit_btn.toggled.connect(self._toggle_edit)

        bh.addWidget(self._save_btn)
        bh.addWidget(self._save_as_btn)
        bh.addWidget(self._save_close_btn)
        bh.addWidget(_vsep())
        bh.addWidget(self._prev_btn)
        bh.addWidget(self._next_btn)
        bh.addWidget(self._fit_btn)
        bh.addWidget(self._100_btn)
        bh.addWidget(_vsep())
        bh.addWidget(self._meta_btn)
        bh.addWidget(self._edit_btn)
        bh.addStretch()
        v.addWidget(bar)

        # ── Splitter: edit | image | meta ─────────────────────────────────────
        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.setStyleSheet(f"background:{BG};")

        self._edit_panel = self._build_edit_panel()
        self._edit_panel.setVisible(False)
        self._splitter.addWidget(self._edit_panel)

        self._view = _ImageView()
        self._view.context_menu_requested.connect(self._on_view_context_menu)
        self._view.zoom_changed.connect(self._on_zoom_changed)
        self._view.crop_confirmed.connect(self._apply_crop)
        self._splitter.addWidget(self._view)

        self._fit_btn.clicked.disconnect()
        self._fit_btn.clicked.connect(self._view.fit_in_view)

        self._meta_panel = self._build_meta_panel()
        self._meta_panel.setVisible(False)
        self._splitter.addWidget(self._meta_panel)
        self._splitter.setSizes([0, 1120, 0])
        v.addWidget(self._splitter, stretch=1)

        # ── Filename footnote ─────────────────────────────────────────────────
        footnote = QFrame()
        footnote.setFixedHeight(22)
        footnote.setStyleSheet("background:#000000;")
        fh = QHBoxLayout(footnote)
        fh.setContentsMargins(8, 0, 8, 0)
        fh.setSpacing(0)
        self._title_lbl = QLabel("", footnote)
        self._title_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignHCenter)
        self._title_lbl.setStyleSheet(
            f"color:{SEC};font-family:{FONT};font-size:{FONT_SM}px;background:transparent;")
        fh.addWidget(self._title_lbl)
        v.addWidget(footnote)

        self._load(self._idx)
        self._apply_viewer_settings()

    # ── Edit panel ───────────────────────────────────────────────────────────

    def _build_edit_panel(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(_EDIT_W)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{CAR};border:none;"
            f"border-right:1px solid {MUT};}}")

        w = QWidget()
        w.setStyleSheet(f"background:{CAR};")
        vl = QVBoxLayout(w)
        vl.setContentsMargins(10, 10, 10, 10)
        vl.setSpacing(2)

        _sec_ss = (f"color:{SEC};font-size:{FONT_SM}px;font-weight:bold;"
                   f"background:transparent;margin-top:8px;margin-bottom:2px;")
        _btn_ss = (f"QPushButton{{background:{MUT};color:{PRI};border:none;"
                   f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
                   f"font-weight:bold;padding:4px 10px;}}"
                   f"QPushButton:hover{{background:{ACC};}}"
                   f"QPushButton:checked{{background:{ACC};}}"
                   f"QPushButton:disabled{{background:{MUT};color:{SEC};}}")

        def _sec(name):
            lbl = QLabel(name, w)
            lbl.setStyleSheet(_sec_ss)
            vl.addWidget(lbl)

        # ── Color ─────────────────────────────────────────────────────────────
        _sec("Color")
        self._brightness_sld = self._edit_slider(vl, w, "Brightness", -100, 100, 0)
        self._contrast_sld   = self._edit_slider(vl, w, "Contrast",   -100, 100, 0)
        self._saturation_sld = self._edit_slider(vl, w, "Saturation", -100, 100, 0)

        # ── Tone ──────────────────────────────────────────────────────────────
        _sec("Tone")
        self._gamma_sld = self._edit_slider(vl, w, "Gamma", 10, 300, 100, fmt="gamma")

        # ── Effects ───────────────────────────────────────────────────────────
        _sec("Effects")
        self._blur_sld    = self._edit_slider(vl, w, "Blur",    0, 100, 0, fmt="float1")
        self._sharpen_sld = self._edit_slider(vl, w, "Sharpen", 0, 100, 0, fmt="float1")

        # ── Transform ─────────────────────────────────────────────────────────
        _sec("Transform")

        row1 = QWidget(w); row1.setStyleSheet("background:transparent;")
        r1h  = QHBoxLayout(row1); r1h.setContentsMargins(0,2,0,2); r1h.setSpacing(4)
        self._ep_crop_btn = QPushButton("Crop",   row1)
        ep_resize_btn     = QPushButton("Resize", row1)
        self._ep_crop_btn.setCheckable(True)
        self._ep_crop_btn.setStyleSheet(_btn_ss)
        ep_resize_btn.setStyleSheet(_btn_ss)
        self._ep_crop_btn.setToolTip("Draw a crop rectangle on the image  (Esc to cancel)")
        ep_resize_btn.setToolTip("Scale to new dimensions")
        r1h.addWidget(self._ep_crop_btn)
        r1h.addWidget(ep_resize_btn)
        r1h.addStretch()
        vl.addWidget(row1)

        row2 = QWidget(w); row2.setStyleSheet("background:transparent;")
        r2h  = QHBoxLayout(row2); r2h.setContentsMargins(0,2,0,2); r2h.setSpacing(4)
        ep_fliph_btn = QPushButton("Flip H", row2)
        ep_flipv_btn = QPushButton("Flip V", row2)
        ep_fliph_btn.setStyleSheet(_btn_ss)
        ep_flipv_btn.setStyleSheet(_btn_ss)
        ep_fliph_btn.setToolTip("Flip left ↔ right")
        ep_flipv_btn.setToolTip("Flip top ↕ bottom")
        r2h.addWidget(ep_fliph_btn)
        r2h.addWidget(ep_flipv_btn)
        r2h.addStretch()
        vl.addWidget(row2)

        self._ep_crop_btn.toggled.connect(self._on_crop_toggled)
        ep_resize_btn.clicked.connect(self._open_resize_dialog)
        ep_fliph_btn.clicked.connect(self._flip_h)
        ep_flipv_btn.clicked.connect(self._flip_v)

        # ── Separator ─────────────────────────────────────────────────────────
        sep = QFrame(w); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"background:{MUT};"); sep.setFixedHeight(1)
        vl.addSpacing(6)
        vl.addWidget(sep)
        vl.addSpacing(4)

        # ── Actions ───────────────────────────────────────────────────────────
        act_w = QWidget(w); act_w.setStyleSheet("background:transparent;")
        ah    = QHBoxLayout(act_w); ah.setContentsMargins(0,0,0,0); ah.setSpacing(6)
        revert_btn = QPushButton("Revert", act_w)
        apply_btn  = QPushButton("Apply",  act_w)
        revert_btn.setStyleSheet(_btn_ss)
        apply_btn.setStyleSheet(
            _btn_ss.replace(f"background:{MUT}", f"background:{ACC}", 1))
        revert_btn.setToolTip("Discard current adjustments")
        apply_btn.setToolTip("Commit adjustments to working image")
        revert_btn.clicked.connect(self._revert_edits)
        apply_btn.clicked.connect(self._apply_edits)
        ah.addWidget(revert_btn)
        ah.addStretch()
        ah.addWidget(apply_btn)
        vl.addWidget(act_w)

        vl.addStretch()
        scroll.setWidget(w)
        return scroll

    def _edit_slider(self, parent_vl, parent_w, label: str,
                     min_v: int, max_v: int, default: int,
                     fmt: str = "int") -> QSlider:
        row = QWidget(parent_w); row.setStyleSheet("background:transparent;")
        rh  = QHBoxLayout(row);  rh.setContentsMargins(0, 2, 0, 2); rh.setSpacing(6)

        lbl = QLabel(label, row)
        lbl.setFixedWidth(68)
        lbl.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")

        sld = QSlider(Qt.Horizontal, row)
        sld.setRange(min_v, max_v)
        sld.setValue(default)

        val_lbl = QLabel(self._fmt_edit_val(default, fmt), row)
        val_lbl.setFixedWidth(38)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        val_lbl.setStyleSheet(
            f"color:{PRI};font-size:{FONT_SM}px;background:transparent;")

        sld.valueChanged.connect(
            lambda v, vl=val_lbl, f=fmt: vl.setText(self._fmt_edit_val(v, f)))
        sld.valueChanged.connect(self._schedule_preview)

        rh.addWidget(lbl)
        rh.addWidget(sld, stretch=1)
        rh.addWidget(val_lbl)
        parent_vl.addWidget(row)
        return sld

    @staticmethod
    def _fmt_edit_val(val: int, fmt: str) -> str:
        if fmt == "gamma":  return f"{val/100:.1f}x"
        if fmt == "float1": return f"{val/10:.1f}"
        return str(val)

    def _build_meta_panel(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumWidth(380)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{CAR};border:none;border-left:1px solid {MUT};}}")

        w = QWidget()
        w.setStyleSheet(f"background:{CAR};")
        vl = QVBoxLayout(w)
        vl.setContentsMargins(12, 12, 12, 12)
        vl.setSpacing(8)

        fs = self._font_meta

        # "Browse Image" button — visible only for video / safetensors files
        self._btn_browse_thumb = QPushButton("Browse Image…", w)
        self._btn_browse_thumb.setVisible(False)
        self._btn_browse_thumb.setStyleSheet(
            f"QPushButton{{background:{MUT};color:{PRI};border:none;"
            f"border-radius:4px;padding:5px 12px;"
            f"font-family:{FONT};font-size:{fs}px;font-weight:bold;}}"
            f"QPushButton:hover{{background:{ACC};}}")
        self._btn_browse_thumb.clicked.connect(self._on_browse_thumb)
        vl.addWidget(self._btn_browse_thumb)

        self._meta_source = QLabel("", w)
        self._meta_source.setWordWrap(True)
        self._meta_dim    = QLabel("", w)
        self._meta_dim.setStyleSheet(f"color:{SEC};font-size:{fs}px;")
        self._meta_file   = QLabel("", w)
        self._meta_file.setWordWrap(True)
        self._meta_file.setStyleSheet(f"color:{MUT};font-size:{fs}px;")

        def _field(label: str):
            lbl = QLabel(label, w)
            lbl.setStyleSheet(
                f"color:{SEC};font-family:{FONT};font-size:{fs}px;"
                f"font-weight:bold;background:transparent;margin-top:4px;")
            txt = QTextEdit(w)
            txt.setReadOnly(True)
            txt.setMaximumHeight(90)
            txt.setStyleSheet(
                f"background:{BG};color:{PRI};border:1px solid {MUT};"
                f"border-radius:4px;font-family:{FONT};font-size:{fs}px;"
                f"padding:4px;")
            return lbl, txt

        vl.addWidget(self._meta_source)
        vl.addWidget(self._meta_dim)
        vl.addWidget(self._meta_file)

        self._meta_lbl_prompt, self._f_prompt = _field("Prompt")
        self._meta_lbl_neg,    self._f_neg    = _field("Negative Prompt")
        vl.addWidget(self._meta_lbl_prompt)
        vl.addWidget(self._f_prompt)
        vl.addWidget(self._meta_lbl_neg)
        vl.addWidget(self._f_neg)

        # Compact key-value pairs
        self._kv_labels:     dict[str, QLabel] = {}
        self._kv_key_labels: dict[str, QLabel] = {}
        for key in ("Model", "Sampler", "Seed", "Steps", "CFG Scale"):
            row_w = QWidget(w)
            rh = QHBoxLayout(row_w)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(6)
            k_lbl = QLabel(f"{key}:", row_w)
            k_lbl.setFixedWidth(70)
            k_lbl.setStyleSheet(
                f"color:{SEC};font-size:{fs}px;background:transparent;")
            v_lbl = QLabel("", row_w)
            v_lbl.setWordWrap(True)
            v_lbl.setStyleSheet(
                f"color:{PRI};font-size:{fs}px;background:transparent;")
            rh.addWidget(k_lbl)
            rh.addWidget(v_lbl, stretch=1)
            vl.addWidget(row_w)
            self._kv_labels[key]     = v_lbl
            self._kv_key_labels[key] = k_lbl

        vl.addStretch()
        scroll.setWidget(w)
        return scroll

    def set_font_meta(self, size: int):
        self._font_meta = size
        hdr_ss  = (f"color:{SEC};font-family:{FONT};font-size:{size}px;"
                   f"font-weight:bold;background:transparent;margin-top:4px;")
        txt_ss  = (f"background:{BG};color:{PRI};border:1px solid {MUT};"
                   f"border-radius:4px;font-family:{FONT};font-size:{size}px;padding:4px;")
        dim_ss  = f"color:{SEC};font-size:{size}px;"
        file_ss = f"color:{MUT};font-size:{size}px;"
        kk_ss   = f"color:{SEC};font-size:{size}px;background:transparent;"
        kv_ss   = f"color:{PRI};font-size:{size}px;background:transparent;"
        self._meta_dim.setStyleSheet(dim_ss)
        self._meta_file.setStyleSheet(file_ss)
        self._meta_lbl_prompt.setStyleSheet(hdr_ss)
        self._meta_lbl_neg.setStyleSheet(hdr_ss)
        self._f_prompt.setStyleSheet(txt_ss)
        self._f_neg.setStyleSheet(txt_ss)
        for k_lbl in self._kv_key_labels.values():
            k_lbl.setStyleSheet(kk_ss)
        for v_lbl in self._kv_labels.values():
            v_lbl.setStyleSheet(kv_ss)

    @staticmethod
    def _load_pixmap(fp: str) -> QPixmap:
        """Load any supported image as QPixmap. Falls back to PIL for formats
        Qt can't handle natively (PSD, HDR, EXR, AVIF, HEIC, …)."""
        if fp and os.path.isfile(fp):
            px = QPixmap(fp)
            if not px.isNull():
                return px
            # PIL fallback — raw bytes → QImage avoids a PNG encode/decode round-trip
            try:
                from PIL import Image as _PIL
                with _PIL.open(fp) as img:
                    if img.mode not in ("RGB", "RGBA"):
                        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
                    raw  = img.tobytes("raw", img.mode)
                    fmt  = (QImage.Format.Format_RGBA8888 if img.mode == "RGBA"
                            else QImage.Format.Format_RGB888)
                    qimg = QImage(raw, img.width, img.height, fmt)
                    px2  = QPixmap.fromImage(qimg)
                    if not px2.isNull():
                        return px2
            except Exception:
                pass
        # Last resort: grey placeholder
        px = QPixmap(400, 400)
        px.fill(QColor(CAR))
        return px

    def _load(self, idx: int):
        if not self._images:
            return
        self._idx = max(0, min(idx, len(self._images) - 1))
        row = self._images[self._idx]
        fp  = row.get("filepath", "")

        # Load full image (Qt native first, PIL fallback for PSD/HDR/etc.)
        self._modified_px  = None
        self._edit_base_px = None
        self._view.set_pixmap(self._load_pixmap(fp))

        # Resize window to match new image when in fit mode (deferred so
        # fit_in_view runs first and the transform is up to date)
        s = self._settings
        if s and s.get("viewer_size_mode", "fit") == "fit":
            QTimer.singleShot(0, lambda: self._fit_window_to_image(center=False))

        # Show "Browse Image" button for files that have no generatable thumbnail
        _ext = Path(fp).suffix.lower()
        self._btn_browse_thumb.setVisible(
            self._db is not None and
            (_ext in VIDEO_EXTS or _ext == ".safetensors"))

        # Navigation state
        self._prev_btn.setEnabled(self._idx > 0)
        self._next_btn.setEnabled(self._idx < len(self._images) - 1)
        n = len(self._images)
        self._title_lbl.setText(
            f"{Path(fp).name}   [{self._idx+1} / {n}]")

        # Populate metadata panel
        src   = row.get("source", "") or ""
        color = SOURCE_COLORS.get(src, ACC)
        self._meta_source.setText(
            f'<span style="background:{color};color:#fff;padding:2px 8px;'
            f'border-radius:3px;font-weight:bold;font-size:{FONT_SM}px;">'
            f'{src or "Unknown"}</span>' if src else "No AI metadata")
        self._meta_source.setTextFormat(Qt.RichText)

        w, h = row.get("width") or 0, row.get("height") or 0
        sz   = row.get("filesize") or 0
        self._meta_dim.setText(
            f"{w} × {h}  ·  {sz/1024:.1f} KB")
        self._meta_file.setText(fp)

        self._f_prompt.setPlainText(row.get("prompt", "") or "")
        self._f_neg.setPlainText(row.get("negative_prompt", "") or "")
        self._kv_labels["Model"].setText(row.get("model", "") or "")
        self._kv_labels["Sampler"].setText(row.get("sampler", "") or "")
        self._kv_labels["Seed"].setText(row.get("seed", "") or "")
        self._kv_labels["Steps"].setText(row.get("steps", "") or "")
        self._kv_labels["CFG Scale"].setText(row.get("cfg_scale", "") or "")

    def _center_on_screen(self):
        screen = QApplication.primaryScreen().availableGeometry()
        geo    = self.frameGeometry()
        geo.moveCenter(screen.center())
        self.move(geo.topLeft())

    def _fit_window_to_image(self, center: bool = False):
        """Resize the window to match the current displayed image size.
        When center=True the window is placed at screen centre (initial open).
        Otherwise the current window centre is preserved so zooming
        expands/contracts in place rather than jumping to a corner."""
        if not self._view._item:
            return
        img_w, img_h = self._view.display_size()
        screen   = QApplication.primaryScreen().availableGeometry()
        chrome_h = 36 + 4   # toolbar row + small margin
        meta_w   = 320 if self._meta_panel.isVisible() else 0
        max_w    = screen.width()  - 40
        max_h    = screen.height() - 40
        win_w    = max(480, min(int(img_w) + meta_w + 20, max_w))
        win_h    = max(360, min(int(img_h) + chrome_h + 20, max_h))

        if center:
            self.resize(win_w, win_h)
            self._center_on_screen()
        else:
            # Keep current centre; clamp so the window stays fully on-screen
            old_cx = self.frameGeometry().center().x()
            old_cy = self.frameGeometry().center().y()
            self.resize(win_w, win_h)
            new_x  = max(screen.left(),
                         min(old_cx - win_w // 2,
                             screen.right() - win_w))
            new_y  = max(screen.top(),
                         min(old_cy - win_h // 2,
                             screen.bottom() - win_h))
            self.move(new_x, new_y)

    def _on_zoom_changed(self):
        s = self._settings
        if s and s.get("viewer_size_mode", "fit") == "fit":
            self._fit_window_to_image(center=False)

    def _apply_viewer_settings(self):
        s = self._settings
        if s is None:
            return

        if s.get("viewer_show_meta"):
            self._meta_btn.setChecked(True)

        mode = s.get("viewer_size_mode", "fit")
        if mode == "fit":
            # Defer so the view has its final layout before we measure; center on first open
            QTimer.singleShot(0, lambda: self._fit_window_to_image(center=True))
        elif mode == "remember":
            self.resize(s.get("viewer_width") or 1200,
                        s.get("viewer_height") or 800)
            QTimer.singleShot(0, self._center_on_screen)
        else:
            self.resize(1200, 800)
            QTimer.singleShot(0, self._center_on_screen)

        # Zoom default (deferred so the view has its final size first)
        if s.get("viewer_default_zoom") == "100":
            QTimer.singleShot(0, self._zoom_100)
        else:
            QTimer.singleShot(0, self._view.fit_in_view)

    def closeEvent(self, event):
        s = self._settings
        if s and s.get("viewer_size_mode", "fit") == "remember":
            s.set("viewer_width",  self.width())
            s.set("viewer_height", self.height())
        super().closeEvent(event)

    def _toggle_meta(self, checked: bool):
        self._meta_panel.setVisible(checked)
        self._resize_by(_META_W if checked else -_META_W)
        self._sync_splitter()

    def _toggle_edit(self, checked: bool):
        self._edit_panel.setVisible(checked)
        if checked:
            self._edit_base_px = self._working_pixmap()
        self._resize_by(_EDIT_W if checked else -_EDIT_W)
        self._sync_splitter()

    def _resize_by(self, delta: int):
        screen = QApplication.primaryScreen().availableGeometry()
        new_w  = max(480, min(self.width() + delta, screen.width() - 40))
        cx     = self.frameGeometry().center().x()
        self.resize(new_w, self.height())
        geo    = self.frameGeometry()
        cx     = max(screen.left() + new_w // 2,
                     min(cx, screen.right() - new_w // 2))
        geo.moveCenter(QPoint(cx, geo.center().y()))
        geo.moveTop(max(screen.top(),
                        min(geo.top(), screen.bottom() - geo.height())))
        self.move(geo.topLeft())

    def _sync_splitter(self):
        edit_w = _EDIT_W if self._edit_panel.isVisible() else 0
        meta_w = _META_W if self._meta_panel.isVisible() else 0
        view_w = max(100, self.width() - edit_w - meta_w)
        self._splitter.setSizes([edit_w, view_w, meta_w])

    def _on_view_context_menu(self, global_pos: QPoint):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"font-family:{FONT};font-size:{FONT_MD}px;padding:2px;}}"
            f"QMenu::item{{padding:6px 20px;}}"
            f"QMenu::item:selected{{background:{ACC};}}"
            f"QMenu::separator{{height:1px;background:{MUT};margin:3px 8px;}}")
        menu.addAction("Metadata", self._show_meta_dialog)
        menu.addSeparator()
        menu.addAction("Fit",  self._view.fit_in_view)
        menu.addAction("100%", self._zoom_100)
        menu.addSeparator()
        menu.addAction("← Prev", self._prev)
        menu.addAction("Next →", self._next)
        menu.exec(global_pos)

    def _show_meta_dialog(self):
        if self._images:
            dlg = _PropertiesDialog(self._images[self._idx], db=self._db, parent=self)
            dlg.exec()
            if dlg.saved:
                self._load(self._idx)

    def _on_browse_thumb(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        if not self._images or not self._db:
            return
        row = self._images[self._idx]
        fp  = row.get("filepath", "")
        if not fp:
            return
        img_fp, _ = QFileDialog.getOpenFileName(
            self, "Select Thumbnail Image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif)")
        if not img_fp:
            return
        try:
            from PIL import Image as _PIL
            from io import BytesIO as _BytesIO
            with _PIL.open(img_fp) as img:
                img = img.convert("RGB")
                img.thumbnail((DEFAULT_THUMB, DEFAULT_THUMB), _PIL.LANCZOS)
                buf = _BytesIO()
                img.save(buf, "JPEG", quality=85)
                thumb_bytes = buf.getvalue()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not load image:\n{exc}")
            return
        self._db.upsert(fp, thumbnail=thumb_bytes)
        row["thumbnail"] = thumb_bytes
        if self._on_thumb_changed:
            self._on_thumb_changed(fp, thumb_bytes)

    def _prev(self):
        self._load(self._idx - 1)

    def _next(self):
        self._load(self._idx + 1)

    def _zoom_100(self):
        self._view.resetTransform()
        self._view._zoom = 1.0

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() in (Qt.Key_Right, Qt.Key_Space):
            self._next()
        elif e.key() == Qt.Key_Left:
            self._prev()
        elif e.key() == Qt.Key_Escape:
            if self._ep_crop_btn.isChecked():
                self._ep_crop_btn.setChecked(False)
            else:
                self.close()
        else:
            super().keyPressEvent(e)

    # ── Image state helpers ───────────────────────────────────────────────────

    def _working_pixmap(self) -> QPixmap:
        """Current image state — modified if edits have been applied, else original."""
        if self._modified_px is not None:
            return self._modified_px
        if self._view._item:
            return self._view._item.pixmap()
        return QPixmap()

    def _set_modified(self, px: QPixmap):
        self._modified_px = px
        self._view.set_pixmap(px)
        QTimer.singleShot(0, self._view.fit_in_view)

    # ── Save ─────────────────────────────────────────────────────────────────

    def _write_pixmap(self, px: QPixmap, fp: str) -> bool:
        """Write px to fp, preserving PNG metadata when the setting is on."""
        suffix = Path(fp).suffix.lower()
        if suffix == ".png" and self._settings and self._settings.get("preserve_metadata_on_edit", True):
            try:
                from PIL import Image as _PIL
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                px.save(buf, "PNG")
                buf.close()
                new_img = _PIL.open(BytesIO(bytes(buf.data())))
                try:
                    orig     = _PIL.open(fp)
                    png_info = {k: v for k, v in orig.info.items()}
                    orig.close()
                except Exception:
                    png_info = {}
                new_img.save(fp, format="PNG", **png_info)
                new_img.close()
                return True
            except Exception:
                pass
        return px.save(fp)

    def _save(self):
        if self._modified_px is None:
            return
        fp = self._images[self._idx].get("filepath", "") if self._images else ""
        if fp:
            self._write_pixmap(self._modified_px, fp)
            self._modified_px = None

    def _save_as(self):
        if not self._images:
            return
        fp = self._images[self._idx].get("filepath", "")
        new_fp, _ = QFileDialog.getSaveFileName(
            self, "Save As", fp,
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif)")
        if not new_fp:
            return
        self._write_pixmap(self._working_pixmap(), new_fp)

    def _save_close(self):
        if not self._images:
            self.close()
            return
        fp = self._images[self._idx].get("filepath", "")
        if not fp:
            self.close()
            return
        px = self._working_pixmap()
        if px.isNull():
            self.close()
            return

        s      = self._settings
        fmt    = (s.get("viewer_save_format") or "png") if s else "png"
        # Extension map (key → file extension)
        _ext   = {"png": "png", "jpg": "jpg", "webp": "webp",
                  "bmp": "bmp", "tiff": "tiff"}
        suffix = _ext.get(fmt, "png")

        orig_suffix = Path(fp).suffix.lower().lstrip(".")
        # Normalise jpeg → jpg for comparison
        norm = {"jpeg": "jpg", "tif": "tiff"}.get(orig_suffix, orig_suffix)
        if norm == suffix:
            out_fp = fp
        else:
            out_fp = str(Path(fp).with_suffix(f".{suffix}"))

        self._write_pixmap(px, out_fp)
        self._modified_px = None
        self.close()

    # ── Flip ─────────────────────────────────────────────────────────────────

    def _flip_h(self):
        self._set_modified(
            self._working_pixmap().transformed(QTransform().scale(-1, 1)))

    def _flip_v(self):
        self._set_modified(
            self._working_pixmap().transformed(QTransform().scale(1, -1)))

    # ── Crop ─────────────────────────────────────────────────────────────────

    def _on_crop_toggled(self, checked: bool):
        self._view.set_crop_mode(checked)

    def _apply_crop(self, scene_rect: QRectF):
        self._ep_crop_btn.setChecked(False)
        px = self._working_pixmap()
        if px.isNull() or not scene_rect.isValid():
            return
        clamped = QRectF(0, 0, px.width(), px.height()).intersected(scene_rect)
        if clamped.isEmpty():
            return
        self._set_modified(px.copy(clamped.toAlignedRect()))

    # ── Resize ───────────────────────────────────────────────────────────────

    def _open_resize_dialog(self):
        px = self._working_pixmap()
        if px.isNull():
            return
        dlg = _ResizeDialog(px.width(), px.height(), self)
        if dlg.exec() == QDialog.Accepted:
            nw, nh = dlg.new_size()
            self._set_modified(
                px.scaled(nw, nh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))

    # ── Edit / PIL helpers ────────────────────────────────────────────────────

    def _schedule_preview(self):
        self._preview_timer.start()

    def _qpixmap_to_pil(self, px: QPixmap):
        from PIL import Image as _PIL
        buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly)
        px.save(buf, "PNG"); buf.close()
        return _PIL.open(BytesIO(bytes(buf.data())))

    def _pil_to_qpixmap(self, img) -> QPixmap:
        buf = BytesIO(); img.save(buf, format="PNG")
        px  = QPixmap(); px.loadFromData(buf.getvalue())
        return px

    def _apply_pil_edits(self, img):
        from PIL import Image, ImageEnhance, ImageFilter
        bri = self._brightness_sld.value()
        con = self._contrast_sld.value()
        sat = self._saturation_sld.value()
        gam = self._gamma_sld.value()       # 10–300 (100 = neutral)
        blr = self._blur_sld.value()        # 0–100
        shp = self._sharpen_sld.value()     # 0–100

        if bri != 0:
            img = ImageEnhance.Brightness(img).enhance(
                max(0.0, 1.0 + bri / 100.0))
        if con != 0:
            img = ImageEnhance.Contrast(img).enhance(
                max(0.0, 1.0 + con / 100.0))
        if sat != 0:
            img = ImageEnhance.Color(img).enhance(
                max(0.0, 1.0 + sat / 100.0))
        if gam != 100:
            inv = 1.0 / (gam / 100.0)
            lut = bytes([int(255 * (i / 255.0) ** inv) for i in range(256)])
            channels = img.split()
            alpha    = channels[3] if img.mode == "RGBA" else None
            rgb      = img.convert("RGB")
            r, g, b  = rgb.split()
            img = Image.merge("RGB", (r.point(lut), g.point(lut), b.point(lut)))
            if alpha:
                img = img.convert("RGBA")
                img.putalpha(alpha)
        if blr > 0:
            img = img.filter(ImageFilter.GaussianBlur(radius=blr / 10.0))
        if shp > 0:
            img = ImageEnhance.Sharpness(img).enhance(1.0 + shp / 50.0)
        return img

    def _compute_preview(self):
        base = self._edit_base_px or self._working_pixmap()
        if base.isNull():
            return
        try:
            img = self._qpixmap_to_pil(base)
            img = self._apply_pil_edits(img)
            self._view.update_pixmap(self._pil_to_qpixmap(img))
        except Exception:
            pass

    def _apply_edits(self):
        base = self._edit_base_px or self._working_pixmap()
        if base.isNull():
            return
        try:
            img = self._qpixmap_to_pil(base)
            img = self._apply_pil_edits(img)
            px  = self._pil_to_qpixmap(img)
        except Exception:
            return
        self._modified_px  = px
        self._edit_base_px = px
        self._reset_edit_sliders()
        self._view.set_pixmap(px)
        QTimer.singleShot(0, self._view.fit_in_view)

    def _revert_edits(self):
        self._reset_edit_sliders()
        base = self._edit_base_px or self._working_pixmap()
        if not base.isNull():
            self._view.update_pixmap(base)

    def _reset_edit_sliders(self):
        for sld, default in [
            (self._brightness_sld,   0),
            (self._contrast_sld,     0),
            (self._saturation_sld,   0),
            (self._gamma_sld,      100),
            (self._blur_sld,         0),
            (self._sharpen_sld,      0),
        ]:
            sld.blockSignals(True)
            sld.setValue(default)
            sld.blockSignals(False)
        # Refresh all value labels by triggering the connected lambda once each
        for sld in (self._brightness_sld, self._contrast_sld,
                    self._saturation_sld, self._gamma_sld,
                    self._blur_sld, self._sharpen_sld):
            sld.valueChanged.emit(sld.value())

    def _mk_btn(self, text: str, color: str) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setFixedHeight(24)
        btn.setStyleSheet(
            f"QPushButton{{background:{color};color:{PRI};border:none;"
            f"border-radius:4px;font-family:{FONT};font-size:{FONT_SM}px;"
            f"font-weight:bold;padding:0 10px;}}"
            f"QPushButton:hover{{background:{ACC};}}"
            f"QPushButton:checked{{background:{ACC};}}"
            f"QPushButton:disabled{{background:{MUT};color:{SEC};}}")
        return btn


# ── Recycle Bin helper (Windows) ─────────────────────────────────────────────

def _send_to_recycle(path: str) -> bool:
    """Move a file to the Windows Recycle Bin. Returns True on success."""
    try:
        import ctypes, ctypes.wintypes as wt

        class _SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd",                  wt.HWND),
                ("wFunc",                 wt.UINT),
                ("pFrom",                 ctypes.c_wchar_p),
                ("pTo",                   ctypes.c_wchar_p),
                ("fFlags",                wt.WORD),
                ("fAnyOperationsAborted", wt.BOOL),
                ("hNameMappings",         ctypes.c_void_p),
                ("lpszProgressTitle",     ctypes.c_wchar_p),
            ]

        FO_DELETE          = 0x0003
        FOF_ALLOWUNDO      = 0x0040
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT         = 0x0004

        op         = _SHFILEOPSTRUCTW()
        op.wFunc   = FO_DELETE
        op.pFrom   = path + "\0"      # double-null terminated
        op.fFlags  = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


# ── Properties dialog ─────────────────────────────────────────────────────────

class _PropertiesDialog(QDialog):
    def __init__(self, row: dict, db=None, parent=None):
        super().__init__(parent)
        self._db     = db
        self._fields: dict[str, "QWidget"] = {}
        self.saved   = False
        fp   = row.get("filepath", "")
        name = Path(fp).name if fp else "Unknown"
        self.setWindowTitle(f"Properties — {name}")
        self.setMinimumWidth(580)
        self.setMinimumHeight(520)
        self.setStyleSheet(
            f"background:{BG};color:{PRI};font-family:{FONT};")

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 12)
        v.setSpacing(8)

        # ── Scrollable field area ─────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{BG};border:none;}}")
        inner = QWidget()
        inner.setStyleSheet(f"background:{BG};")
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 6, 0)
        iv.setSpacing(4)

        _lbl_style = (
            f"color:{SEC};font-size:{FONT_SM}px;font-weight:bold;"
            f"background:transparent;margin-top:4px;")
        _edit_style = (
            f"background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"border-radius:4px;font-size:{FONT_SM}px;padding:2px 6px;")
        _text_style = (
            f"background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"border-radius:4px;font-size:{FONT_SM}px;padding:4px;")
        _edit_active = (
            f"background:{CAR};color:{PRI};border:1px solid {ACC};"
            f"border-radius:4px;font-size:{FONT_SM}px;padding:2px 6px;")
        _text_active = (
            f"background:{CAR};color:{PRI};border:1px solid {ACC};"
            f"border-radius:4px;font-size:{FONT_SM}px;padding:4px;")

        def _field(label: str, value: str, multiline: bool = False,
                   height: int = 80, key: str = ""):
            lbl = QLabel(label, inner)
            lbl.setStyleSheet(_lbl_style)
            iv.addWidget(lbl)
            if multiline:
                w = QTextEdit(inner)
                w.setReadOnly(True)
                w.setPlainText(value or "")
                w.setFixedHeight(height)
                w.setStyleSheet(_text_style)
            else:
                w = QLineEdit(value or "", inner)
                w.setReadOnly(True)
                w.setStyleSheet(_edit_style)
            iv.addWidget(w)
            if key:
                self._fields[key] = w

        # ── File section ──────────────────────────────────────────────────────
        fsize   = row.get("filesize") or 0
        sz_str  = (f"{fsize/1024/1024:.2f} MB" if fsize >= 1024*1024
                   else f"{fsize/1024:.1f} KB")
        w_px    = row.get("width")  or 0
        h_px    = row.get("height") or 0
        mtime   = row.get("modified_at")
        mtime_s = (datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                   if mtime else "")
        added_s = row.get("added_at", "") or ""
        src     = row.get("source", "") or ""
        rating  = row.get("rating") or 0

        _field("File Path",   fp)
        _field("Dimensions",  f"{w_px} × {h_px} px" if w_px and h_px else "")
        _field("File Size",   sz_str)
        _field("Modified",    mtime_s)
        _field("Added to DB", added_s)
        _field("Source",      src)
        if rating:
            _field("Rating", str(rating))

        # ── AI Metadata section ───────────────────────────────────────────────
        prompt  = row.get("prompt", "")          or ""
        neg     = row.get("negative_prompt", "") or ""
        model   = row.get("model", "")           or ""
        sampler = row.get("sampler", "")         or ""
        seed    = row.get("seed", "")            or ""
        steps   = row.get("steps", "")           or ""
        cfg     = row.get("cfg_scale", "")       or ""
        tags    = row.get("tags", "")            or ""
        raw     = row.get("raw_meta", "")        or ""

        _field("Prompt",          prompt,  multiline=True, height=90,  key="prompt")
        _field("Negative Prompt", neg,     multiline=True, height=70,  key="negative_prompt")
        _field("Model",   model,   key="model")
        _field("Sampler", sampler, key="sampler")
        _field("Seed",    seed,    key="seed")
        _field("Steps",   steps,   key="steps")
        _field("CFG Scale", cfg,   key="cfg_scale")
        if tags:
            _field("Tags", tags, key="tags")
        if raw:
            _field("Raw Metadata", raw, multiline=True, height=100, key="raw_meta")

        iv.addStretch()
        scroll.setWidget(inner)
        v.addWidget(scroll, stretch=1)

        # ── Button row ────────────────────────────────────────────────────────
        bh = QHBoxLayout()
        bh.setSpacing(8)

        def _copy_all():
            lines = [
                f"File:           {fp}",
                f"Dimensions:     {w_px} × {h_px} px",
                f"File Size:      {sz_str}",
                f"Modified:       {mtime_s}",
                f"Added to DB:    {added_s}",
                f"Source:         {src}",
                f"Prompt:         {prompt}",
                f"Negative:       {neg}",
                f"Model:          {model}",
                f"Sampler:        {sampler}",
                f"Seed:           {seed}",
                f"Steps:          {steps}",
                f"CFG Scale:      {cfg}",
            ]
            if tags:
                lines.append(f"Tags:           {tags}")
            QApplication.clipboard().setText("\n".join(lines))

        def _enter_edit():
            for w in self._fields.values():
                w.setReadOnly(False)
                w.setStyleSheet(_text_active if isinstance(w, QTextEdit) else _edit_active)
            btn_edit.setEnabled(False)
            btn_save.setEnabled(True)

        def _save():
            if self._db is None:
                return
            data = {}
            for k, w in self._fields.items():
                data[k] = w.toPlainText() if isinstance(w, QTextEdit) else w.text()

            # 1. Write to database
            self._db.upsert(fp, **data)
            self.saved = True

            # 2. Write back to the image file so DB and file stay in sync.
            #    PNG → A1111 'parameters' tEXt chunk (atomic temp-rename).
            #    JPEG/WebP/other → DB-only with an informational note.
            from ai_metadata import write_metadata_to_file
            warning = write_metadata_to_file(fp, data)

            # Reset widgets to read-only view mode
            for w in self._fields.values():
                w.setReadOnly(True)
                w.setStyleSheet(_text_style if isinstance(w, QTextEdit) else _edit_style)
            btn_edit.setEnabled(True)
            btn_save.setEnabled(False)

            if warning:
                from PySide6.QtWidgets import QMessageBox
                mb = QMessageBox(self)
                mb.setWindowTitle("Metadata")
                mb.setIcon(QMessageBox.Icon.Information)
                mb.setText("Saved to database.")
                mb.setInformativeText(warning)
                mb.exec()

        _btn_base = (
            f"border:none;border-radius:4px;font-family:{FONT};"
            f"font-size:{FONT_SM}px;font-weight:bold;padding:0 14px;")

        btn_copy = QPushButton("Copy All")
        btn_copy.setFixedHeight(28)
        btn_copy.setStyleSheet(
            f"QPushButton{{background:{ACC};color:{PRI};{_btn_base}}}"
            f"QPushButton:hover{{background:#185FA5;}}")
        btn_copy.clicked.connect(_copy_all)

        btn_edit = QPushButton("Edit")
        btn_edit.setFixedHeight(28)
        btn_edit.setEnabled(self._db is not None)
        btn_edit.setStyleSheet(
            f"QPushButton{{background:#2a5a2a;color:{PRI};{_btn_base}}}"
            f"QPushButton:hover{{background:#336633;}}"
            f"QPushButton:disabled{{background:{MUT};color:#666688;}}")
        btn_edit.clicked.connect(_enter_edit)

        btn_save = QPushButton("Save")
        btn_save.setFixedHeight(28)
        btn_save.setEnabled(False)
        btn_save.setStyleSheet(
            f"QPushButton{{background:#5a3a00;color:{PRI};{_btn_base}}}"
            f"QPushButton:hover{{background:#7a5000;}}"
            f"QPushButton:disabled{{background:{MUT};color:#666688;}}")
        btn_save.clicked.connect(_save)

        btn_close = QPushButton("Close")
        btn_close.setFixedHeight(28)
        btn_close.setStyleSheet(
            f"QPushButton{{background:{MUT};color:{PRI};{_btn_base}}}"
            f"QPushButton:hover{{background:#555577;}}")
        btn_close.clicked.connect(self.accept)

        bh.addWidget(btn_copy)
        bh.addWidget(btn_edit)
        bh.addWidget(btn_save)
        bh.addStretch()
        bh.addWidget(btn_close)
        v.addLayout(bh)


# ── Launch-app helpers ───────────────────────────────────────────────────────

def _icon_from_b64(b64: str) -> QIcon:
    """Decode base64 PNG string to QIcon."""
    if not b64:
        return QIcon()
    try:
        data = base64.b64decode(b64)
        px   = QPixmap()
        px.loadFromData(data)
        return QIcon(px)
    except Exception:
        return QIcon()


def _launch_app(app: dict, filepath: str = "") -> None:
    """Launch an external app, substituting %1 with filepath."""
    exe  = app.get("exe", "")
    args = app.get("args", "%1")
    if not exe:
        return
    parts = [exe] + [a.replace("%1", filepath) for a in args.split()]
    try:
        subprocess.Popen(parts)
    except Exception:
        pass


# ── Single thumbnail card ─────────────────────────────────────────────────────

class ThumbCard(QFrame):
    def __init__(self, row: dict, thumb_size: int,
                 on_select, on_open,
                 on_remove=None, on_rename=None,
                 settings=None,
                 on_get_selection=None,
                 font_size: int = FONT_SM,
                 defer_thumb: bool = False,
                 on_set_thumb=None,
                 on_get_plugins=None,
                 on_run_plugin=None,
                 parent=None):
        super().__init__(parent)
        self._row              = dict(row)
        self._on_select        = on_select
        self._on_open          = on_open
        self._on_remove        = on_remove        # callable(card, fp)
        self._on_rename        = on_rename        # callable(card, old_fp, new_fp)
        self._on_set_thumb     = on_set_thumb     # callable(card, thumb_bytes)
        self._on_get_plugins   = on_get_plugins   # callable() → list[PluginInfo]
        self._on_run_plugin    = on_run_plugin     # callable(PluginInfo, filepath)
        self._settings         = settings
        self._on_get_selection = on_get_selection # callable() → list[ThumbCard]
        self._selected         = False
        self._drag_start: QPoint | None = None
        self._font_size        = font_size

        card_w = thumb_size + THUMB_PADDING
        card_h = thumb_size + LABEL_HEIGHT + THUMB_PADDING
        self.setFixedSize(card_w, card_h)
        self._set_border(False)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_NativeWindow)   # required for OLE drag to browsers

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 2)
        v.setSpacing(2)
        v.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        # ── Thumbnail (or safetensors / video placeholder) ───────────────────
        fp_ext = Path(row.get("filepath", "")).suffix.lower()
        self._img = QLabel(self)
        self._img.setFixedSize(thumb_size, thumb_size)
        self._img.setAlignment(Qt.AlignCenter)
        if fp_ext == ".safetensors":
            self._render_safetensors(row.get("filepath", ""), thumb_size)
        elif fp_ext in VIDEO_EXTS and not row.get("thumbnail"):
            self._render_video(fp_ext, thumb_size)   # no frame extracted — filmstrip
        elif defer_thumb:
            self._set_thumb(None)   # placeholder — real decode queued to thread pool
        else:
            self._set_thumb(row.get("thumbnail"))
        v.addWidget(self._img)

        # ── Filename ──────────────────────────────────────────────────────────
        name  = Path(row.get("filepath", "")).name
        short = name[:22] + "…" if len(name) > 24 else name
        self._name_lbl = QLabel(short, self)
        self._name_lbl.setAlignment(Qt.AlignCenter)
        self._name_lbl.setStyleSheet(
            f"color:{PRI};font-family:{FONT};font-size:{font_size}px;background:transparent;")
        self._name_lbl.setFixedWidth(thumb_size)
        self._name_lbl.setToolTip(name)
        v.addWidget(self._name_lbl)

        # ── Dimensions / source badge ─────────────────────────────────────────
        w = row.get("width") or 0
        h = row.get("height") or 0
        src = row.get("source", "") or ""
        src_color = SOURCE_COLORS.get(src, MUT)
        dim_txt = f"{w}×{h}" if w and h else ""
        src_txt = src if src else ""
        info_txt = f"{dim_txt}  {src_txt}".strip() if dim_txt else src_txt

        self._info_lbl = QLabel(info_txt, self)
        self._info_lbl.setAlignment(Qt.AlignCenter)
        self._info_lbl.setStyleSheet(
            f"color:{src_color if src else SEC};"
            f"font-family:{FONT};font-size:{font_size}px;background:transparent;")
        self._info_lbl.setFixedWidth(thumb_size)
        v.addWidget(self._info_lbl)

    def _set_thumb(self, data: bytes | None):
        if data:
            px = QPixmap()
            px.loadFromData(data)
            if not px.isNull():
                size = self._img.width()
                self._img.setPixmap(
                    px.scaled(size, size, Qt.KeepAspectRatio,
                               Qt.SmoothTransformation))
                return
        # Placeholder
        size = self._img.width()
        px = QPixmap(size, size)
        px.fill(QColor(CAR))
        p = QPainter(px)
        p.setPen(QColor(MUT))
        p.setFont(QFont(FONT, FONT_SM))
        p.drawText(px.rect(), Qt.AlignCenter, "No Image")
        p.end()
        self._img.setPixmap(px)

    def _render_safetensors(self, filepath: str, size: int):
        """Draw safetensors metadata summary into self._img."""
        meta = _read_safetensors_meta(filepath)
        px   = QPixmap(size, size)
        px.fill(QColor(BG))
        p = QPainter(px)
        p.setPen(QColor(PRI))
        p.setFont(QFont(FONT, max(7, FONT_SM - 1)))
        margin, line_h, y = 6, 14, 8
        for line in meta.splitlines():
            if y + line_h > size - 4:
                break
            p.drawText(margin, y + line_h, line)
            y += line_h
        p.end()
        self._img.setPixmap(px)

    def _render_video(self, ext: str, size: int):
        """Draw a video-file placeholder with a play button and extension label."""
        px = QPixmap(size, size)
        px.fill(QColor("#1a1a2e"))
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        # Film-strip side bars
        bar_w = max(8, size // 10)
        p.fillRect(0, 0, bar_w, size, QColor("#111"))
        p.fillRect(size - bar_w, 0, bar_w, size, QColor("#111"))
        sprocket_h = max(6, size // 12)
        sprocket_w = max(4, bar_w - 4)
        p.setBrush(QColor("#333"))
        p.setPen(Qt.NoPen)
        for row_y in range(sprocket_h, size - sprocket_h * 2, sprocket_h * 2):
            p.drawRoundedRect(2, row_y, sprocket_w, sprocket_h - 2, 2, 2)
            p.drawRoundedRect(size - bar_w + 2, row_y, sprocket_w, sprocket_h - 2, 2, 2)
        # Play button triangle
        tri = size // 3
        cx, cy = size // 2, size // 2
        p.setBrush(QColor(ACC))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygon([
            QPoint(cx - tri // 2, cy - tri // 2),
            QPoint(cx - tri // 2, cy + tri // 2),
            QPoint(cx + tri // 2, cy),
        ]))
        # Extension label
        p.setPen(QColor(SEC))
        p.setFont(QFont(FONT, max(7, FONT_SM - 1), QFont.Bold))
        p.drawText(0, size - max(14, size // 8), size, max(14, size // 8),
                   Qt.AlignCenter, ext.upper().lstrip("."))
        p.end()
        self._img.setPixmap(px)

    def update_thumb(self, data: bytes):
        self._set_thumb(data)

    def set_font_size(self, size: int):
        self._font_size = size
        self._name_lbl.setStyleSheet(
            f"color:{PRI};font-family:{FONT};font-size:{size}px;background:transparent;")
        src = self._row.get("source", "") or ""
        src_color = SOURCE_COLORS.get(src, MUT)
        self._info_lbl.setStyleSheet(
            f"color:{src_color if src else SEC};"
            f"font-family:{FONT};font-size:{size}px;background:transparent;")

    def assign_row(self, row: dict):
        """Swap card content to a new row (pool reuse — no widget recreation)."""
        self._row  = dict(row)
        fp         = row.get("filepath", "")
        fp_ext     = Path(fp).suffix.lower() if fp else ""
        size       = self._img.width()

        # Image area
        if fp_ext == ".safetensors":
            self._render_safetensors(fp, size)
        elif fp_ext in VIDEO_EXTS and not row.get("thumbnail"):
            self._render_video(fp_ext, size)
        else:
            self._set_thumb(None)   # placeholder; _assign_pool sets real pixmap

        # Filename label
        name  = Path(fp).name if fp else ""
        short = name[:22] + "…" if len(name) > 24 else name
        self._name_lbl.setText(short)
        self._name_lbl.setToolTip(name)

        # Info label
        w   = row.get("width")  or 0
        h   = row.get("height") or 0
        src = row.get("source", "") or ""
        src_color = SOURCE_COLORS.get(src, MUT)
        dim_txt   = f"{w}×{h}" if w and h else ""
        info_txt  = f"{dim_txt}  {src}".strip() if dim_txt else src
        self._info_lbl.setText(info_txt)
        self._info_lbl.setStyleSheet(
            f"color:{src_color if src else SEC};"
            f"font-family:{FONT};font-size:{self._font_size}px;background:transparent;")

        # Reset selection border
        self._selected = False
        self._set_border(False)

    def _set_border(self, selected: bool):
        color = ACC if selected else MUT
        self.setStyleSheet(
            f"ThumbCard{{background:{CAR};border:2px solid {color};"
            f"border-radius:6px;}}")

    def set_selected(self, val: bool):
        self._selected = val
        self._set_border(val)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_start = e.pos()
        self._on_select(self, e.modifiers())
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if (self._drag_start is not None
                and (e.buttons() & Qt.LeftButton)
                and (e.pos() - self._drag_start).manhattanLength()
                    >= QApplication.startDragDistance()):
            self._drag_start = None
            self._start_external_drag()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_start = None
        super().mouseReleaseEvent(e)

    def _start_external_drag(self):
        selection = (self._on_get_selection() if self._on_get_selection else []) or [self]
        if self not in selection:
            selection = [self]
        fps = [c._row.get("filepath", "") for c in selection
               if c._row.get("filepath") and os.path.isfile(c._row["filepath"])]
        if not fps:
            return

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(fp) for fp in fps])
        # Explicit text/uri-list for browsers that read it directly
        mime.setData("text/uri-list",
                     ("\r\n".join(QUrl.fromLocalFile(fp).toString()
                                  for fp in fps) + "\r\n").encode())

        drag = QDrag(self)
        drag.setMimeData(mime)

        # Drag thumbnail pixmap — hotspot at top-left so image sits under cursor
        px = self._img.pixmap()
        if px and not px.isNull():
            scaled = px.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            if len(fps) > 1:
                badge = QPixmap(scaled.width() + 16, scaled.height() + 16)
                badge.fill(Qt.transparent)
                painter = QPainter(badge)
                painter.setOpacity(0.85)
                painter.drawPixmap(0, 0, scaled)
                painter.setOpacity(1.0)
                painter.setBrush(QColor(ACC))
                painter.setPen(Qt.NoPen)
                painter.drawEllipse(scaled.width() - 2, 0, 18, 18)
                painter.setPen(QColor("#000"))
                painter.setFont(QFont(FONT, 8, QFont.Bold))
                painter.drawText(scaled.width() - 2, 0, 18, 18,
                                 Qt.AlignCenter, str(len(fps)))
                painter.end()
                drag.setPixmap(badge)
            else:
                drag.setPixmap(scaled)
            drag.setHotSpot(QPoint(8, 8))

        drag.exec(Qt.CopyAction)

    def mouseDoubleClickEvent(self, e):
        self._on_open(self)
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        # Select this card only if not already in a multi-selection
        if not self._selected:
            self._on_select(self, Qt.NoModifier)
        _menu_ss = (
            f"QMenu{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"font-family:{FONT};font-size:{FONT_MD}px;padding:2px;}}"
            f"QMenu::item{{padding:6px 20px;}}"
            f"QMenu::item:selected{{background:{ACC};}}"
            f"QMenu::separator{{height:1px;background:{MUT};margin:3px 8px;}}")
        menu = QMenu(self)
        menu.setStyleSheet(_menu_ss)
        menu.addAction("Metadata",           self._show_properties)
        menu.addSeparator()
        menu.addAction("Open",              lambda: self._on_open(self))
        menu.addAction("Open in Explorer",  self._open_explorer)
        # "Open With →" submenu — populated from settings.launch_apps
        apps = (self._settings.get("launch_apps") or []) if self._settings else []
        if apps:
            open_with = QMenu("Open With", menu)
            open_with.setStyleSheet(_menu_ss)
            for app in apps:
                icon = _icon_from_b64(app.get("icon_b64", ""))
                act  = open_with.addAction(icon, app["name"])
                fp   = self._row.get("filepath", "")
                act.triggered.connect(
                    lambda checked=False, a=app, f=fp: _launch_app(a, f))
            menu.addMenu(open_with)
        # "Apply Filter →" submenu — populated from .8bf plugins
        _fp         = self._row.get("filepath", "")
        _fp_ext     = Path(_fp).suffix.lower()
        _plugins    = (self._on_get_plugins() if self._on_get_plugins else [])
        _plugin_dirs = (self._settings.get("plugin_dirs") or []) if self._settings else []
        if _plugin_dirs and _fp_ext not in VIDEO_EXTS and \
                _fp_ext not in {".txt", ".zip", ".safetensors", ""}:
            filters_menu = QMenu("Apply Filter", menu)
            filters_menu.setStyleSheet(_menu_ss)
            if _plugins:
                by_cat: dict[str, list] = {}
                for _p in _plugins:
                    by_cat.setdefault(_p.category or "Uncategorized", []).append(_p)
                if len(by_cat) == 1:
                    for _p in next(iter(by_cat.values())):
                        _act = filters_menu.addAction(_p.display_name)
                        _act.triggered.connect(
                            lambda checked=False, pi=_p, f=_fp: self._on_run_plugin(pi, f))
                else:
                    for _cat, _ps in sorted(by_cat.items()):
                        _sub = QMenu(_cat, filters_menu)
                        _sub.setStyleSheet(_menu_ss)
                        for _p in _ps:
                            _act = _sub.addAction(_p.display_name)
                            _act.triggered.connect(
                                lambda checked=False, pi=_p, f=_fp: self._on_run_plugin(pi, f))
                        filters_menu.addMenu(_sub)
            else:
                _na = filters_menu.addAction("No plugins found — check status bar")
                _na.setEnabled(False)
            menu.addMenu(filters_menu)
        menu.addSeparator()
        copy_menu = self._build_recent_menu("Copy to", "recent_copy_dirs",
                                            self._copy_file, "copy to.png")
        menu.addMenu(copy_menu)
        move_menu = self._build_recent_menu("Move to", "recent_move_dirs",
                                            self._move_file, "move to.png")
        menu.addMenu(move_menu)
        menu.addAction("Rename…",           self._rename_file)
        fp_ext = Path(self._row.get("filepath", "")).suffix.lower()
        if fp_ext in VIDEO_EXTS or fp_ext == ".safetensors":
            menu.addSeparator()
            menu.addAction("Set Thumbnail…", self._browse_set_thumb)
        menu.addSeparator()
        menu.addAction("Delete  (Recycle Bin)", self._delete_file)
        menu.exec(e.globalPos())

    # ── Context-menu actions ──────────────────────────────────────────────────

    def _show_properties(self):
        dlg = _PropertiesDialog(self._row, db=self._db, parent=self)
        dlg.exec()

    def _open_explorer(self):
        fp = self._row.get("filepath", "")
        if fp and os.path.isfile(fp):
            import subprocess
            subprocess.Popen(["explorer", "/select,", fp.replace("/", "\\")])

    def _browse_set_thumb(self):
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        img_fp, _ = QFileDialog.getOpenFileName(
            self, "Select Thumbnail Image", "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tiff *.tif)")
        if not img_fp:
            return
        try:
            from PIL import Image as _PIL
            from io import BytesIO as _BytesIO
            size = self._img.width()
            with _PIL.open(img_fp) as img:
                img = img.convert("RGB")
                img.thumbnail((size, size), _PIL.LANCZOS)
                buf = _BytesIO()
                img.save(buf, "JPEG", quality=85)
                thumb_bytes = buf.getvalue()
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not load image:\n{exc}")
            return
        if self._on_set_thumb:
            self._on_set_thumb(self, thumb_bytes)

    # ── Recent-folder helpers ─────────────────────────────────────────────────

    def _get_recent_dirs(self, key: str) -> list[str]:
        if not self._settings:
            return []
        return list(self._settings.get(key) or [])

    def _add_recent_dir(self, key: str, path: str):
        if not self._settings:
            return
        dirs = self._get_recent_dirs(key)
        if path in dirs:
            dirs.remove(path)
        dirs.insert(0, path)
        self._settings.set(key, dirs[:10])

    def _build_recent_menu(self, label: str, key: str,
                           action_fn, icon_name: str) -> "QMenu":
        from PySide6.QtWidgets import QMenu
        m = QMenu(label, self)
        m.setIcon(_asset_icon(icon_name))
        recent = self._get_recent_dirs(key)
        for folder in recent:
            short = folder if len(folder) <= 50 else "…" + folder[-47:]
            act = m.addAction(short)
            act.setToolTip(folder)
            act.triggered.connect(lambda _checked, d=folder: action_fn(d))
        if recent:
            m.addSeparator()
        m.addAction("Browse…", lambda: action_fn(None))
        return m

    def _copy_file(self, dest_dir=None):
        fp = self._row.get("filepath", "")
        if not fp or not os.path.isfile(fp):
            return
        if dest_dir is None:
            dest_dir = QFileDialog.getExistingDirectory(
                self, "Copy to folder…", str(Path(fp).parent))
        if not dest_dir:
            return
        dest = Path(dest_dir) / Path(fp).name
        try:
            shutil.copy2(fp, dest)
            self._add_recent_dir("recent_copy_dirs", dest_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Copy failed", str(exc))

    def _move_file(self, dest_dir=None):
        fp = self._row.get("filepath", "")
        if not fp or not os.path.isfile(fp):
            return
        if dest_dir is None:
            dest_dir = QFileDialog.getExistingDirectory(
                self, "Move to folder…", str(Path(fp).parent))
        if not dest_dir:
            return
        dest = str(Path(dest_dir) / Path(fp).name)
        try:
            shutil.move(fp, dest)
            self._add_recent_dir("recent_move_dirs", dest_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Move failed", str(exc))
            return
        if self._on_remove:
            self._on_remove(self, fp)

    def _rename_file(self):
        fp = self._row.get("filepath", "")
        if not fp or not os.path.isfile(fp):
            return
        old_name = Path(fp).name
        new_name, ok = QInputDialog.getText(
            self, "Rename", "New filename:", text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        new_fp = str(Path(fp).parent / new_name)
        if os.path.exists(new_fp):
            QMessageBox.warning(self, "Rename",
                                f'"{new_name}" already exists in this folder.')
            return
        try:
            os.rename(fp, new_fp)
        except Exception as exc:
            QMessageBox.critical(self, "Rename failed", str(exc))
            return
        # Update label
        short = new_name[:22] + "…" if len(new_name) > 24 else new_name
        self._name_lbl.setText(short)
        self._name_lbl.setToolTip(new_name)
        if self._on_rename:
            self._on_rename(self, fp, new_fp)

    def _delete_file(self):
        # Collect all cards to delete (multi-select or just self)
        selection = (self._on_get_selection() if self._on_get_selection else []) or [self]
        # Ensure this card is included
        if self not in selection:
            selection = [self]

        confirm = (self._settings.get("confirm_delete") if self._settings else True)
        if confirm:
            if len(selection) == 1:
                msg = f'Move "{Path(selection[0]._row.get("filepath","")).name}" to the Recycle Bin?'
            else:
                msg = f'Move {len(selection)} selected files to the Recycle Bin?'
            reply = QMessageBox.question(
                self, "Delete", msg,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        failed = []
        for card in list(selection):   # copy — list mutates during removal
            fp = card._row.get("filepath", "")
            if not fp:
                continue
            if _send_to_recycle(fp):
                if self._on_remove:
                    self._on_remove(card, fp)
            else:
                failed.append(Path(fp).name)

        if failed:
            QMessageBox.critical(
                self, "Delete failed",
                "Could not recycle:\n" + "\n".join(failed))

    def _copy_path(self):
        fp = self._row.get("filepath", "")
        if fp:
            QApplication.clipboard().setText(fp)


# ── Background thumbnail decoder ─────────────────────────────────────────────

class _ThumbLoaderSignals(QObject):
    done = Signal(str, object)   # (filepath, QImage | None)


class _ThumbLoader(QRunnable):
    """Decodes a JPEG/PNG blob into a QImage on a worker thread."""
    def __init__(self, filepath: str, data: bytes, size: int,
                 signals: _ThumbLoaderSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._filepath = filepath
        self._data    = data
        self._size    = size
        self._signals = signals

    def run(self):
        img = QImage()
        if self._data and img.loadFromData(self._data):
            img = img.scaled(self._size, self._size,
                             Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
            self._signals.done.emit(self._filepath, img)
        else:
            self._signals.done.emit(self._filepath, None)


class _DbThumbLoader(QRunnable):
    """Fetches a thumbnail BLOB from the DB then decodes it on a worker thread.

    Used when rows are loaded without thumbnail data (metadata-only query) so
    only the BLOBs for *visible* cards are pulled from disk on demand.
    """
    def __init__(self, filepath: str, size: int, db,
                 row: dict, signals: _ThumbLoaderSignals):
        super().__init__()
        self.setAutoDelete(True)
        self._filepath = filepath
        self._size    = size
        self._db      = db
        self._row     = row   # mutable dict — store fetched bytes back here
        self._signals = signals

    def run(self):
        data = self._db.get_thumbnail(self._filepath)
        if data:
            self._row["thumbnail"] = data   # cache for future scrolls
        img = QImage()
        if data and img.loadFromData(data):
            img = img.scaled(self._size, self._size,
                             Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)
            self._signals.done.emit(self._filepath, img)
        else:
            self._signals.done.emit(self._filepath, None)


# ── Main thumbnail grid ───────────────────────────────────────────────────────

class ThumbGrid(QWidget):
    status_changed    = Signal(str)
    scan_finished     = Signal()
    task_list_changed = Signal(list)

    def __init__(self, db: ThumbsDB, settings=None, parent=None):
        super().__init__(parent)
        self._db           = db
        self._settings     = settings
        # Propagate ffmpeg path setting to module-level finder
        global _FFMPEG_EXE
        _FFMPEG_EXE = (settings.get("ffmpeg_exe") or "") if settings else ""
        self._thumb_size   = DEFAULT_THUMB
        self._font_image   = int((settings.get("font_image") or FONT_SM)
                                 if settings else FONT_SM)
        self._sort         = "numeric name"
        self._sort_dir     = "asc"
        self._sort2        = ""
        self._sort2_dir    = "asc"
        self._sort3        = ""
        self._sort3_dir    = "asc"
        self._folder       = ""
        self._enabled_exts = set(SUPPORTED_EXTS)
        self._plugins:         list              = []   # PluginInfo list, populated by rescan_plugins()
        # ── Virtual scroll state ──────────────────────────────────────────────
        self._rows:            list[dict]        = []
        self._selected_fps:    set[str]          = set()
        self._anchor_fp:       str | None        = None
        self._card_pool:       list[ThumbCard]   = []
        self._pool_cols:       int               = 0
        self._first_pool_idx:  int               = 0
        self._px_cache:        dict[str, QPixmap] = {}
        self._cache_lru:       deque[str]        = deque()
        self._pending_decodes: set[str]          = set()
        # ── Scan worker ───────────────────────────────────────────────────────
        self._worker:          _ThumbWorker | None = None
        self._worker_folder:   str               = ""
        self._viewers:         list[ImageViewer] = []
        # ── Background thumb decoder ──────────────────────────────────────────
        self._loader_signals = _ThumbLoaderSignals()
        self._loader_signals.done.connect(self._on_thumb_decoded)
        self._decode_pool = QThreadPool(self)
        self._decode_pool.setMaxThreadCount(max(2, QThread.idealThreadCount() - 1))
        # ── Task queue ────────────────────────────────────────────────────────
        self._task_queue:   deque = deque()
        self._task_running: bool  = False
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_viewport_resize)

        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 0)
        v.setSpacing(0)

        # ── Progress bar ──────────────────────────────────────────────────────
        self._prog = QProgressBar(self)
        self._prog.setRange(0, 0)
        self._prog.setFixedHeight(4)
        self._prog.setVisible(False)
        self._prog.setStyleSheet(
            f"QProgressBar{{background:{CAR};border:none;}}"
            f"QProgressBar::chunk{{background:{ACC};}}")
        v.addWidget(self._prog)

        # ── Scroll area — no layout manager, cards positioned manually ────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(f"QScrollArea{{border:none;background:{BG};}}")

        self._container = QWidget()
        self._container.setStyleSheet(f"background:{BG};")

        self._scroll.setWidget(self._container)
        self._scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        v.addWidget(self._scroll, stretch=1)

        QTimer.singleShot(0, self.rescan_plugins)

    # ── Public API ─────────────────────────────────────────────────────────────

    def rescan_plugins(self):
        """Scan plugin_dirs from settings for .8bf filters (background thread)."""
        dirs = (self._settings.get("plugin_dirs") or []) if self._settings else []
        if not dirs:
            self._plugins = []
            return
        import threading
        def _scan():
            try:
                from plugin_host import scan_plugin_dirs
                result = scan_plugin_dirs(dirs)
                self._plugins = result
                n = len(result)
                names = ", ".join(str(p) for p in result[:3])
                suffix = (f" ({names}{'…' if n > 3 else ''})" if names else "")
                self.status_changed.emit(
                    f"Plugins: {n} filter{'s' if n != 1 else ''} loaded{suffix}")
            except Exception as exc:
                self._plugins = []
                self.status_changed.emit(f"Plugins: scan error — {exc}")
        threading.Thread(target=_scan, daemon=True).start()

    def _get_plugins(self) -> list:
        return self._plugins

    def _on_run_plugin(self, plugin_info, filepath: str):
        """Load image, invoke the .8bf filter on the main thread, offer to save result."""
        from io import BytesIO as _BytesIO
        try:
            from PIL import Image as _PIL
            from plugin_host import run_plugin_filter
            img    = _PIL.open(filepath)
            result = run_plugin_filter(plugin_info, img, int(self.winId()))
        except (RuntimeError, Exception) as exc:
            _mb = QMessageBox(self)
            _mb.setWindowTitle("Plugin Error")
            _mb.setText("Filter failed.")
            _mb.setDetailedText(str(exc))
            _mb.exec()
            return
        if result is None:
            return  # user cancelled inside the plugin's own dialog

        msg = QMessageBox(self)
        msg.setWindowTitle(f"Filter: {plugin_info.display_name}")
        msg.setText("Filter applied successfully.")
        msg.setInformativeText("What would you like to do with the result?")
        btn_over   = msg.addButton("Overwrite",  QMessageBox.AcceptRole)
        btn_saveas = msg.addButton("Save As…",   QMessageBox.ActionRole)
        msg.addButton("Discard",    QMessageBox.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()

        dest = None
        if clicked is btn_over:
            dest = filepath
        elif clicked is btn_saveas:
            dest, _ = QFileDialog.getSaveFileName(
                self, "Save As", filepath,
                "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;BMP (*.bmp)")

        if not dest:
            return
        try:
            result.save(dest)
        except Exception as exc:
            QMessageBox.warning(self, "Save Error", str(exc))
            return

        if dest == filepath:
            try:
                from PIL import Image as _PIL2
                sz  = self._thumb_size
                buf = _BytesIO()
                thumb = result.copy()
                thumb.thumbnail((sz, sz), _PIL2.LANCZOS)
                thumb.convert("RGB").save(buf, "JPEG", quality=85)
                self._on_custom_thumb_fp(filepath, buf.getvalue())
            except Exception:
                pass

    def load_folder(self, folder: str):
        """Show cached thumbnails for folder instantly. Does NOT start a scan."""
        folder = str(Path(folder))
        self._folder = folder
        self._clear_grid()
        if not folder or not os.path.isdir(folder):
            return
        all_rows   = self._db.images_in_folder(
            folder,
            self._sort,  self._sort_dir,
            self._sort2, self._sort2_dir,
            self._sort3, self._sort3_dir,
            with_thumbnails=False)
        self._rows = [r for r in all_rows
                      if Path(r["filepath"]).suffix.lower() in self._enabled_exts]
        self._rebuild_pool()

    def append_rows(self, rows: list[dict]):
        """Append extra image rows (e.g. from ThumbsPlus Read Only) to the current grid."""
        extra = [r for r in rows
                 if Path(r.get("filepath", "")).suffix.lower() in self._enabled_exts]
        if not extra:
            return
        self._rows.extend(extra)
        self._update_container_size()
        self._assign_pool()

    def selected_paths(self) -> list[str]:
        """Return file paths of all currently selected items (in row order)."""
        return [r.get("filepath", "") for r in self._rows
                if r.get("filepath") in self._selected_fps]

    def scan_folder(self, folder: str):
        """Queue a background scan for folder.

        If a scan is already running for a DIFFERENT folder, signal it to stop
        (non-blocking) and drop any queued scan tasks so the new folder scan
        runs next rather than waiting behind stale work.
        """
        folder = str(Path(folder))
        if not folder or not os.path.isdir(folder):
            return

        # Cancel running scan if it's for a different folder
        if (self._worker and self._worker.isRunning()
                and self._worker_folder != folder):
            self._worker.requestInterruption()
            # Drop any queued scan tasks — they're for the old folder
            self._task_queue = deque(
                t for t in self._task_queue
                if not t["desc"].startswith("Scan Folder:"))
            self.task_list_changed.emit([t["desc"] for t in self._task_queue])
            if not self._task_running:
                self._task_running = False

        self._enqueue(f"Scan Folder: {Path(folder).name}",
                      lambda f=folder: self._start_folder_scan(f))

    def cancel_task(self, index: int) -> None:
        """Cancel the task at queue index (0 = currently running)."""
        if index == 0:
            if self._worker and self._worker.isRunning():
                # Non-blocking: signal stop, leave the task in the queue.
                # _on_worker_finished → _task_done will pop it and start the next.
                self._worker.requestInterruption()
                self._prog.setVisible(False)
            else:
                # No active worker — advance the queue immediately.
                if self._task_queue:
                    self._task_queue.popleft()
                self._task_running = False
                self.task_list_changed.emit([t["desc"] for t in self._task_queue])
                if self._task_queue:
                    self._run_next_task()
        elif 0 < index < len(self._task_queue):
            q = list(self._task_queue)
            q.pop(index)
            self._task_queue.clear()
            self._task_queue.extend(q)
            self.task_list_changed.emit([t["desc"] for t in self._task_queue])

    def cancel_all_tasks(self) -> None:
        """Stop running task and clear the entire queue."""
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
        self._task_queue.clear()
        self._task_running = False
        self._prog.setVisible(False)
        self.task_list_changed.emit([])
        # The dying worker will call _on_worker_finished; _task_done finds an
        # empty queue and exits cleanly without starting anything.

    def search(self, query: str):
        """Display search results (global across all folders)."""
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
        self._task_queue.clear()
        self._task_running = False
        self._prog.setVisible(False)
        self.task_list_changed.emit([])
        self._folder = ""
        self._clear_grid()
        if not query.strip():
            return
        self._rows = self._db.search(query.strip())
        self._rebuild_pool()
        self.status_changed.emit(
            f"{len(self._rows)} result{'s' if len(self._rows) != 1 else ''} "
            f"for  \"{query}\"")

    def set_thumb_size(self, size: int):
        self._thumb_size = size
        if self._folder:
            self.load_folder(self._folder)

    def set_sort(self, sort: str,  sort_dir: str  = "asc",
                       sort2: str = "", sort2_dir: str = "asc",
                       sort3: str = "", sort3_dir: str = "asc"):
        self._sort      = sort
        self._sort_dir  = sort_dir
        self._sort2     = sort2
        self._sort2_dir = sort2_dir
        self._sort3     = sort3
        self._sort3_dir = sort3_dir
        if self._folder:
            self.load_folder(self._folder)

    def sort_similar(self, reference_path: str):
        """Re-sort current view by visual similarity to reference_path (aHash)."""
        ref_row = next((r for r in self._rows
                        if r.get("filepath") == reference_path), None)
        if ref_row is None:
            self.status_changed.emit("Reference image not in current view")
            return
        ref_thumb = ref_row.get("thumbnail")
        if not ref_thumb:
            self.status_changed.emit("Reference image has no cached thumbnail")
            return
        try:
            ref_hash = _ahash(bytes(ref_thumb))
        except Exception as ex:
            self.status_changed.emit(f"Hash error: {ex}")
            return

        _cache: dict[str, int | None] = {}
        def _get_hash(r: dict) -> int | None:
            fp = r.get("filepath", "")
            if fp not in _cache:
                tb = r.get("thumbnail")
                try:
                    _cache[fp] = _ahash(bytes(tb)) if tb else None
                except Exception:
                    _cache[fp] = None
            return _cache[fp]

        self._rows = sorted(
            self._rows,
            key=lambda r: (
                h := _get_hash(r),
                _hamming(ref_hash, h) if h is not None else 64
            )[1]
        )
        self._selected_fps.clear()
        self._anchor_fp = None
        self._scroll.verticalScrollBar().setValue(0)
        self._rebuild_pool()
        self.status_changed.emit(
            f"Sorted {len(self._rows)} images by similarity  ·  "
            f"{Path(reference_path).name}")

    def refresh(self):
        if self._folder:
            self.load_folder(self._folder)

    def set_enabled_extensions(self, exts: set):
        self._enabled_exts = exts
        if self._folder:
            self.load_folder(self._folder)

    def set_font_image(self, size: int):
        self._font_image = size
        for card in self._card_pool:
            card.set_font_size(size)

    def set_font_meta(self, size: int):
        for viewer in self._viewers:
            viewer.set_font_meta(size)

    def _on_custom_thumb(self, card: "ThumbCard", data: bytes):
        """Called by ThumbCard context menu 'Set Thumbnail…'."""
        fp = card._row.get("filepath", "")
        if not fp:
            return
        self._db.upsert(fp, thumbnail=data)
        for r in self._rows:
            if r.get("filepath") == fp:
                r["thumbnail"] = data
                break
        self._px_cache.pop(fp, None)
        try:
            self._cache_lru.remove(fp)
        except ValueError:
            pass
        card.update_thumb(data)

    def _on_custom_thumb_fp(self, fp: str, data: bytes):
        """Called by ImageViewer 'Browse Image' button."""
        self._db.upsert(fp, thumbnail=data)
        for r in self._rows:
            if r.get("filepath") == fp:
                r["thumbnail"] = data
                break
        self._px_cache.pop(fp, None)
        try:
            self._cache_lru.remove(fp)
        except ValueError:
            pass
        for card in self._card_pool:
            if card.isVisible() and card._row.get("filepath") == fp:
                card.update_thumb(data)
                break

    def make_thumbs_for(self, paths: list[str]):
        """Queue: regenerate thumbnails for an explicit list of file paths."""
        valid = [fp for fp in paths if fp and os.path.isfile(fp)]
        if not valid:
            return
        n = len(valid)
        self._enqueue(
            f"Refresh {n} thumbnail{'s' if n != 1 else ''}",
            lambda vp=valid: self._do_make_thumbs_for(vp))

    def _do_make_thumbs_for(self, fps: list):
        """Internal: regenerate thumbnails for a list of file paths (parallel)."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from database import _file_hash

        t = self._thumb_size

        def _regen_one(fp: str):
            """Returns (rec_dict, error_msg) — runs in a pool thread."""
            try:
                fp_ext = Path(fp).suffix.lower()
                if fp_ext in VIDEO_EXTS:
                    thumb_bytes, w, h = _extract_video_thumb(fp, t)
                else:
                    from PIL import Image as _PIL
                    with _PIL.open(fp) as img:
                        w, h = img.size
                        tmp  = img.copy()
                        tmp.thumbnail((t, t), _PIL.LANCZOS)
                        buf  = BytesIO()
                        tmp.save(buf, format="JPEG", quality=82)
                        thumb_bytes = buf.getvalue()
                mtime = Path(fp).stat().st_mtime
                meta  = parse_png_metadata(fp)
                return {
                    "filepath":        fp,
                    "thumbnail":       thumb_bytes,
                    "width":           w,
                    "height":          h,
                    "modified_at":     mtime,
                    "file_hash":       _file_hash(fp),
                    "prompt":          meta.get("prompt",          ""),
                    "negative_prompt": meta.get("negative_prompt", ""),
                    "seed":            meta.get("seed",            ""),
                    "model":           meta.get("model",           ""),
                    "sampler":         meta.get("sampler",         ""),
                    "cfg_scale":       meta.get("cfg_scale",       ""),
                    "steps":           meta.get("steps",           ""),
                    "source":          meta.get("source",          ""),
                    "raw_meta":        meta.get("raw_meta",        ""),
                }, None
            except Exception as exc:
                return None, f"Error refreshing {Path(fp).name}: {exc}"

        workers = min(_ThumbWorker._WORKERS, max(1, len(fps)))
        records: list[dict] = []
        errors:  list[str]  = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for fut in as_completed({pool.submit(_regen_one, fp): fp for fp in fps}):
                result, err = fut.result()
                if result:
                    records.append(result)
                if err:
                    errors.append(err)

        # Emit errors collected from worker threads (safe — now on calling thread)
        for err_msg in errors:
            self.status_changed.emit(err_msg)

        if records:
            self._db.batch_upsert(records, commit=True)
            for rec in records:
                fp          = rec["filepath"]
                thumb_bytes = rec.get("thumbnail")
                for r in self._rows:
                    if r.get("filepath") == fp:
                        r["thumbnail"] = thumb_bytes
                        break
                self._px_cache.pop(fp, None)
                try:
                    self._cache_lru.remove(fp)
                except ValueError:
                    pass
                for card in self._card_pool:
                    if card.isVisible() and card._row.get("filepath") == fp:
                        card.update_thumb(thumb_bytes)
                        break

        self._task_done()

    def make_selected_thumb(self):
        """Queue: regenerate thumbnail + re-read metadata for selected image."""
        if not self._selected_fps:
            self.status_changed.emit("No image selected")
            return
        fp = next(iter(self._selected_fps))
        if not fp or not os.path.isfile(fp):
            self.status_changed.emit("File not found on disk")
            return
        self._enqueue(f"Make Selected: {Path(fp).name}",
                      lambda f=fp: self._do_make_selected_fp(f))

    def _do_make_selected_fp(self, fp: str):
        try:
            from PIL import Image as _PIL
            t = self._thumb_size
            fp_ext = Path(fp).suffix.lower()
            if fp_ext in VIDEO_EXTS:
                thumb_bytes, w, h = _extract_video_thumb(fp, t)
                if not thumb_bytes:
                    have_ff = bool(_find_ffmpeg())
                    hint = ("ffmpeg found but extraction failed — file may be corrupt"
                            if have_ff else
                            "run:  pip install av  (or opencv-python)")
                    self.status_changed.emit(f"No frame extracted — {hint}")
                    return
            else:
                with _PIL.open(fp) as img:
                    w, h = img.size
                    tmp = img.copy()
                    tmp.thumbnail((t, t), _PIL.LANCZOS)
                    buf = BytesIO()
                    tmp.save(buf, format="JPEG", quality=82)
                    thumb_bytes = buf.getvalue()
            from database import _file_hash
            mtime = Path(fp).stat().st_mtime
            meta  = parse_png_metadata(fp)
            self._db.upsert(fp,
                thumbnail   = thumb_bytes,
                width=w, height=h, modified_at=mtime,
                file_hash   = _file_hash(fp),
                prompt          = meta.get("prompt",          ""),
                negative_prompt = meta.get("negative_prompt", ""),
                seed            = meta.get("seed",            ""),
                model           = meta.get("model",           ""),
                sampler         = meta.get("sampler",         ""),
                cfg_scale       = meta.get("cfg_scale",       ""),
                steps           = meta.get("steps",           ""),
                source          = meta.get("source",          ""),
                raw_meta        = meta.get("raw_meta",        ""),
            )
            for r in self._rows:
                if r.get("filepath") == fp:
                    r["thumbnail"] = thumb_bytes
                    break
            self._px_cache.pop(fp, None)
            try:
                self._cache_lru.remove(fp)
            except ValueError:
                pass
            for card in self._card_pool:
                if card.isVisible() and card._row.get("filepath") == fp:
                    card.update_thumb(thumb_bytes)
                    break
            self.status_changed.emit(f"Thumbnail updated: {Path(fp).name}")
        except Exception as exc:
            self.status_changed.emit(f"Error making thumbnail: {exc}")
        finally:
            self._task_done()

    def scan_all_folders(self, folders: list[str]):
        """Queue: scan all given folders for new/changed thumbnails."""
        valid = [f for f in folders if os.path.isdir(f)]
        if not valid:
            self.status_changed.emit("No folders in database to scan")
            return
        n = len(valid)
        self._enqueue(
            f"Scan Disk: {n} folder{'s' if n != 1 else ''}",
            lambda vf=valid: self._start_disk_scan(vf))

    def _start_disk_scan(self, folders: list[str]):
        """Internal: begin the multi-folder disk scan (called by queue)."""
        self._disk_queue   = list(folders)
        self._disk_totals  = [0, 0]
        self._disk_n_total = len(self._disk_queue)
        self._next_disk_scan()

    def _next_disk_scan(self):
        if not self._disk_queue:
            a = self._disk_totals[0]
            n = self._disk_n_total
            self._prog.setVisible(False)
            self.status_changed.emit(
                f"Disk scan complete — {n} folder{'s' if n != 1 else ''}, "
                f"{a} new thumbnail{'s' if a != 1 else ''}")
            self.scan_finished.emit()
            self._task_done()   # release queue for next task
            return
        folder = self._disk_queue.pop(0)
        rem    = len(self._disk_queue)
        self.status_changed.emit(
            f"Scanning {Path(folder).name}…  ({rem} folder{'s' if rem != 1 else ''} remaining)")
        self._prog.setVisible(True)
        self._disk_worker = _ThumbWorker(folder, self._thumb_size, self._db,
                                         self._enabled_exts)
        self._disk_worker.image_ready.connect(lambda fp, row: None)
        self._disk_worker.progress.connect(lambda msg: self.status_changed.emit(msg))
        self._disk_worker.finished.connect(self._on_disk_folder_done)
        self._disk_worker.start()

    def _on_disk_folder_done(self, added: int, _skipped: int):
        self._disk_totals[0] += added
        self._next_disk_scan()

    # ── Internal ───────────────────────────────────────────────────────────────

    def _card_size(self) -> tuple[int, int]:
        card_w = self._thumb_size + THUMB_PADDING
        card_h = self._thumb_size + LABEL_HEIGHT + THUMB_PADDING
        return card_w, card_h

    def _cols(self) -> int:
        card_w, _ = self._card_size()
        w = max(1, self._scroll.viewport().width())
        return max(1, w // (card_w + CARD_GAP))

    def _update_container_size(self):
        cols    = self._pool_cols or self._cols()
        card_w, card_h = self._card_size()
        row_h   = card_h + CARD_GAP
        vp_w    = max(1, self._scroll.viewport().width())
        vp_h    = max(1, self._scroll.viewport().height())
        n_items = len(self._rows)
        n_rows  = (n_items + cols - 1) // cols if n_items else 0
        cont_h  = n_rows * row_h + 2 * CARD_MARGIN if n_rows else vp_h
        self._container.setFixedSize(vp_w, max(cont_h, vp_h))

    def _rebuild_pool(self):
        """Create or resize the fixed card pool, then assign rows to viewport."""
        cols   = self._cols()
        card_w, card_h = self._card_size()
        row_h  = card_h + CARD_GAP
        vp_h   = max(1, self._scroll.viewport().height())
        vp_w   = max(1, self._scroll.viewport().width())

        # Virtual container height
        n_items = len(self._rows)
        n_rows  = (n_items + cols - 1) // cols if n_items else 0
        cont_h  = n_rows * row_h + 2 * CARD_MARGIN if n_rows else vp_h
        self._container.setFixedSize(vp_w, max(cont_h, vp_h))

        # Pool size: visible rows + buffer above and below
        visible_rows = max(1, (vp_h + row_h - 1) // row_h)
        pool_rows    = visible_rows + 2 * POOL_BUFFER
        target_size  = pool_rows * cols

        # Rebuild pool widgets if column count changed
        if cols != self._pool_cols and self._card_pool:
            for c in self._card_pool:
                c.hide()
                c.deleteLater()
            self._card_pool.clear()
        self._pool_cols = cols

        _blank = {"filepath": ""}
        while len(self._card_pool) < target_size:
            card = ThumbCard(
                _blank, self._thumb_size,
                self._on_select, self._on_open,
                on_remove=self._on_card_removed,
                on_rename=self._on_card_renamed,
                settings=self._settings,
                on_get_selection=self._get_selected_cards,
                font_size=self._font_image,
                defer_thumb=False,
                on_set_thumb=self._on_custom_thumb,
                on_get_plugins=self._get_plugins,
                on_run_plugin=self._on_run_plugin,
                parent=self._container)
            card.hide()
            self._card_pool.append(card)

        # Trim excess (keep a bit of slack to avoid thrash on small resizes)
        while len(self._card_pool) > target_size + cols * 2:
            c = self._card_pool.pop()
            c.hide()
            c.deleteLater()

        self._assign_pool()

    def _assign_pool(self):
        """Move pool cards to their current viewport positions and populate content."""
        if not self._card_pool or not self._pool_cols:
            return
        cols   = self._pool_cols
        card_w, card_h = self._card_size()
        row_h  = card_h + CARD_GAP
        col_w  = card_w + CARD_GAP

        scroll_y       = self._scroll.verticalScrollBar().value()
        first_vis_row  = max(0, scroll_y // row_h)
        first_pool_row = max(0, first_vis_row - POOL_BUFFER)
        self._first_pool_idx = first_pool_row * cols

        _queued: set[str] = set()

        for k, card in enumerate(self._card_pool):
            item_idx = self._first_pool_idx + k
            if item_idx >= len(self._rows):
                card.hide()
                continue

            row   = self._rows[item_idx]
            fp    = row.get("filepath", "")
            r_idx = item_idx // cols
            c_idx = item_idx % cols
            x     = CARD_MARGIN + c_idx * col_w
            y     = CARD_MARGIN + r_idx * row_h

            card.assign_row(row)
            card.set_selected(fp in self._selected_fps)
            card.move(x, y)
            card.show()

            # Serve decoded pixmap from LRU cache or queue a decode job
            fp_ext = Path(fp).suffix.lower() if fp else ""
            is_video_no_thumb = fp_ext in VIDEO_EXTS and not row.get("thumbnail")
            can_display = fp and fp_ext != ".safetensors" and not is_video_no_thumb
            if can_display:
                if fp in self._px_cache:
                    card._img.setPixmap(self._px_cache[fp])
                elif fp not in self._pending_decodes and fp not in _queued:
                    _queued.add(fp)
                    self._pending_decodes.add(fp)
                    data = row.get("thumbnail")
                    if data:
                        # BLOB already in memory (e.g. from a previous scroll)
                        loader = _ThumbLoader(fp, bytes(data),
                                              self._thumb_size, self._loader_signals)
                    else:
                        # Fetch BLOB from DB on demand (metadata-only initial load)
                        loader = _DbThumbLoader(fp, self._thumb_size, self._db,
                                               row, self._loader_signals)
                    self._decode_pool.start(loader)

    def _store_px(self, fp: str, px: QPixmap):
        if fp in self._px_cache:
            try:
                self._cache_lru.remove(fp)
            except ValueError:
                pass
        self._px_cache[fp] = px
        self._cache_lru.append(fp)
        while len(self._cache_lru) > MAX_CACHE_PX:
            evict = self._cache_lru.popleft()
            self._px_cache.pop(evict, None)

    def _get_selected_cards(self) -> list["ThumbCard"]:
        return [c for c in self._card_pool
                if c.isVisible() and c._row.get("filepath") in self._selected_fps]

    def _on_viewport_resize(self):
        self._rebuild_pool()

    def _clear_grid(self):
        for c in self._card_pool:
            c.hide()
        self._rows           = []
        self._selected_fps   = set()
        self._anchor_fp      = None
        self._first_pool_idx = 0
        self._pending_decodes.clear()
        self._update_container_size()
        self._update_status()

    def _update_status(self):
        total = len(self._rows)
        msg   = f"{total} image{'s' if total != 1 else ''}"
        if self._folder:
            msg += f"  ·  {Path(self._folder).name}"
        self.status_changed.emit(msg)

    def _on_card_removed(self, card: "ThumbCard", filepath: str):
        """Called by card after file deleted/moved. Removes it from grid + DB."""
        self._db.delete(filepath)
        self._rows = [r for r in self._rows if r.get("filepath") != filepath]
        self._selected_fps.discard(filepath)
        if self._anchor_fp == filepath:
            self._anchor_fp = None
        self._px_cache.pop(filepath, None)
        try:
            self._cache_lru.remove(filepath)
        except ValueError:
            pass
        self._update_container_size()
        self._assign_pool()

    def _on_card_renamed(self, card: "ThumbCard", old_fp: str, new_fp: str):
        """Called by card after rename. Updates DB + internal rows."""
        self._db.rename_filepath(old_fp, new_fp)
        card._row["filepath"] = new_fp
        card._row["filename"] = Path(new_fp).name
        for r in self._rows:
            if r.get("filepath") == old_fp:
                r["filepath"] = new_fp
                r["filename"] = Path(new_fp).name
                break
        if old_fp in self._selected_fps:
            self._selected_fps.discard(old_fp)
            self._selected_fps.add(new_fp)
        if self._anchor_fp == old_fp:
            self._anchor_fp = new_fp
        if old_fp in self._px_cache:
            self._px_cache[new_fp] = self._px_cache.pop(old_fp)
        try:
            idx = list(self._cache_lru).index(old_fp)
            self._cache_lru[idx] = new_fp
        except ValueError:
            pass

    def _on_scroll(self, value: int):
        if not self._rows or not self._card_pool:
            return
        _, card_h = self._card_size()
        row_h     = card_h + CARD_GAP
        cols      = self._pool_cols or 1
        new_first_pool = max(0, (value // row_h) - POOL_BUFFER) * cols
        if new_first_pool != self._first_pool_idx:
            self._assign_pool()

    def _on_image_ready(self, filepath: str, row: dict):
        """Worker emitted a completed row — update existing row or append."""
        for i, r in enumerate(self._rows):
            if r.get("filepath") == filepath:
                self._rows[i] = row
                thumb = row.get("thumbnail")
                if thumb:
                    self._px_cache.pop(filepath, None)
                    self._pending_decodes.discard(filepath)
                    for card in self._card_pool:
                        if card.isVisible() and card._row.get("filepath") == filepath:
                            data = bytes(thumb)
                            loader = _ThumbLoader(filepath, data,
                                                  self._thumb_size, self._loader_signals)
                            self._decode_pool.start(loader)
                            break
                return
        # New file — only append if it belongs to the currently displayed folder
        if self._folder and str(Path(filepath).parent) != self._folder:
            return
        self._rows.append(row)
        self._update_container_size()

    def _on_thumb_decoded(self, filepath: str, img):
        self._pending_decodes.discard(filepath)
        if img is None or img.isNull():
            return
        px = QPixmap.fromImage(img)
        self._store_px(filepath, px)
        for card in self._card_pool:
            if card.isVisible() and card._row.get("filepath") == filepath:
                card._img.setPixmap(px)
                break

    def _on_worker_finished(self, added: int, skipped: int):
        self._prog.setVisible(False)
        scanned = self._worker_folder
        if self._folder and scanned == self._folder:
            all_rows   = self._db.images_in_folder(
                self._folder,
                self._sort,  self._sort_dir,
                self._sort2, self._sort2_dir,
                self._sort3, self._sort3_dir)
            self._rows = [r for r in all_rows
                          if Path(r["filepath"]).suffix.lower() in self._enabled_exts]
            if added > 0:
                self._selected_fps.clear()
                self._anchor_fp = None
                self._px_cache.clear()
                self._cache_lru.clear()
                self._pending_decodes.clear()
                self._scroll.verticalScrollBar().setValue(0)
                self._rebuild_pool()
            else:
                self._px_cache.clear()
                self._cache_lru.clear()
                self._pending_decodes.clear()
                self._assign_pool()
        total = len(self._rows)
        msg   = (f"{total} image{'s' if total != 1 else ''}"
                 + (f"  ·  {added} new" if added else ""))
        if self._folder:
            msg += f"  ·  {Path(self._folder).name}"
        self.status_changed.emit(msg)
        self.scan_finished.emit()
        self._task_done()

    # ── Task queue ───────────────────────────────────────────────────────────

    def _enqueue(self, desc: str, fn) -> None:
        """Add a task to the queue. Starts immediately if nothing is running.
        Skips if an identical description is already running or anywhere in the
        queue (prevents double-scan when auto-scan and watcher fire together)."""
        if self._task_running and self._task_queue and self._task_queue[0]["desc"] == desc:
            return
        if any(t["desc"] == desc for t in self._task_queue):
            return
        self._task_queue.append({"desc": desc, "fn": fn})
        self.task_list_changed.emit([t["desc"] for t in self._task_queue])
        if not self._task_running:
            self._run_next_task()

    def _run_next_task(self) -> None:
        if not self._task_queue:
            self._task_running = False
            return
        self._task_running = True
        self._task_queue[0]["fn"]()

    def _task_done(self) -> None:
        """Call when the currently running task finishes."""
        # Guard: if the queue was cleared externally (cancel_all / search),
        # _task_running is already False — don't touch anything.
        if not self._task_running and not self._task_queue:
            return
        if self._task_queue:
            self._task_queue.popleft()
        self._task_running = False
        self.task_list_changed.emit([t["desc"] for t in self._task_queue])
        if self._task_queue:
            self._run_next_task()

    def _start_folder_scan(self, folder: str) -> None:
        """Internal: start the _ThumbWorker for a folder (called by queue)."""
        if not folder or not os.path.isdir(folder):
            self._task_done()
            return
        self._worker_folder = folder
        self._prog.setVisible(True)
        self._worker = _ThumbWorker(folder, self._thumb_size, self._db,
                                    self._enabled_exts)
        self._worker.image_ready.connect(self._on_image_ready)
        self._worker.progress.connect(lambda msg: self.status_changed.emit(msg))
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.start()

    def _stop_worker(self):
        """Block until the worker stops. Only call from shutdown() — not the UI."""
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(3000)
            if self._worker.isRunning():
                self._worker.terminate()
                self._worker.wait(500)
        self._worker = None
        self._prog.setVisible(False)

    def _on_select(self, card: ThumbCard, modifiers=Qt.NoModifier):
        fp = card._row.get("filepath", "")
        if not fp:
            return
        ctrl  = bool(modifiers & Qt.ControlModifier)
        shift = bool(modifiers & Qt.ShiftModifier)

        if shift and self._anchor_fp:
            anchor_idx = next((i for i, r in enumerate(self._rows)
                               if r.get("filepath") == self._anchor_fp), -1)
            click_idx  = next((i for i, r in enumerate(self._rows)
                               if r.get("filepath") == fp), -1)
            if anchor_idx >= 0 and click_idx >= 0:
                lo, hi = min(anchor_idx, click_idx), max(anchor_idx, click_idx)
                self._selected_fps = {r["filepath"] for r in self._rows[lo:hi + 1]}
            else:
                self._selected_fps = {fp}
            self._refresh_selection_visual()
        elif ctrl:
            if fp in self._selected_fps:
                self._selected_fps.discard(fp)
                card.set_selected(False)
            else:
                self._selected_fps.add(fp)
                card.set_selected(True)
            self._anchor_fp = fp
        else:
            old_fps = self._selected_fps.copy()
            self._selected_fps = {fp}
            self._anchor_fp = fp
            for c in self._card_pool:
                c_fp = c._row.get("filepath", "")
                if c_fp and (c_fp in old_fps or c_fp == fp):
                    c.set_selected(c_fp == fp)

    def _refresh_selection_visual(self):
        for c in self._card_pool:
            c_fp = c._row.get("filepath", "")
            if c_fp:
                c.set_selected(c_fp in self._selected_fps)

    def _on_open(self, card: ThumbCard):
        fp  = card._row.get("filepath", "")
        ext = Path(fp).suffix.lower()
        if ext in VIDEO_EXTS:
            if fp and os.path.isfile(fp):
                os.startfile(fp)
            return
        idx = next(
            (i for i, r in enumerate(self._rows)
             if r.get("filepath") == card._row.get("filepath")), 0)
        viewer = ImageViewer(list(self._rows), idx, settings=self._settings,
                             db=self._db,
                             on_thumb_changed=self._on_custom_thumb_fp)
        viewer.setAttribute(Qt.WA_DeleteOnClose)
        viewer.destroyed.connect(lambda: self._viewers.remove(viewer)
                                 if viewer in self._viewers else None)
        self._viewers.append(viewer)
        viewer.show()
        viewer.raise_()
        viewer.activateWindow()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._resize_timer.start(60)

    def shutdown(self):
        self._task_queue.clear()
        self._task_running = False
        self._stop_worker(force=True)
        dw = getattr(self, '_disk_worker', None)
        if dw and dw.isRunning():
            dw.requestInterruption()
            dw.terminate()
            dw.wait(500)
        self._disk_queue = []
