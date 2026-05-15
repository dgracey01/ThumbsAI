"""
image_ops.py — Classic (no-AI) image editing operations for ThumbsAI
Designed by: Zero  |  Built by: Jarvis

Entry point: apply_op(op_name, paths, preserve_metadata, parent) -> int
"""
from __future__ import annotations
import io
from pathlib import Path
from PIL import Image, ImageFilter, ImageEnhance, ImageOps

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QWidget, QFrame, QColorDialog,
    QComboBox, QFileDialog, QCheckBox, QSpinBox, QScrollArea,
    QMessageBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui  import QPixmap, QColor

from theme import BG, CAR, ACC, MUT, PRI, SEC, FONT, FONT_SM

# ── Constants ─────────────────────────────────────────────────────────────────

_PREVIEW_SIZE = 220   # max dimension of the dialog preview thumbnail

# ── Helpers ───────────────────────────────────────────────────────────────────

def _pil_to_qpixmap(img: Image.Image) -> QPixmap:
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "PNG")
    buf.seek(0)
    px = QPixmap()
    px.loadFromData(buf.read())
    return px


def _fit_preview(img: Image.Image) -> Image.Image:
    w, h = img.size
    scale = _PREVIEW_SIZE / max(w, h, 1)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return img.resize((nw, nh), Image.LANCZOS)


def _exif_bytes(img: Image.Image) -> bytes:
    """Return raw EXIF bytes from a PIL image, or b'' if none."""
    try:
        return img.getexif().tobytes()
    except Exception:
        return img.info.get("exif", b"") or b""


def _save_image(img: Image.Image, path: str,
                exif: bytes, preserve: bool):
    """Save img back to path in its original format, optionally with EXIF."""
    ext = Path(path).suffix.lower()
    fmt_map = {
        ".jpg": "JPEG", ".jpeg": "JPEG", ".jpe": "JPEG",
        ".png": "PNG",
        ".webp": "WEBP",
        ".tif": "TIFF", ".tiff": "TIFF",
        ".bmp": "BMP",
    }
    fmt = fmt_map.get(ext, "PNG")
    kwargs: dict = {}

    if fmt == "JPEG":
        img = img.convert("RGB")
        kwargs["quality"]     = 95
        kwargs["subsampling"] = 0
        if preserve and exif:
            kwargs["exif"] = exif
    elif fmt == "WEBP":
        if preserve and exif:
            kwargs["exif"] = exif
    elif fmt == "TIFF":
        if preserve and exif:
            kwargs["exif"] = exif
    elif fmt == "PNG":
        if img.mode not in ("RGBA", "RGB", "L", "LA", "P"):
            img = img.convert("RGBA" if "A" in img.mode else "RGB")

    img.save(path, fmt, **kwargs)


# ── Pure image operations (no Qt) ─────────────────────────────────────────────

def _op_rotate_cw(img: Image.Image, **_) -> Image.Image:
    return img.rotate(-90, expand=True)

def _op_rotate_ccw(img: Image.Image, **_) -> Image.Image:
    return img.rotate(90, expand=True)

def _op_rotate_180(img: Image.Image, **_) -> Image.Image:
    return img.rotate(180, expand=True)

def _op_flip_v(img: Image.Image, **_) -> Image.Image:
    return ImageOps.flip(img)

def _op_flip_h(img: Image.Image, **_) -> Image.Image:
    return ImageOps.mirror(img)

def _op_scale(img: Image.Image, percent: int = 150, **_) -> Image.Image:
    w, h = img.size
    nw = max(1, round(w * percent / 100))
    nh = max(1, round(h * percent / 100))
    return img.resize((nw, nh), Image.LANCZOS)

def _op_blur(img: Image.Image, radius: int = 2, **_) -> Image.Image:
    return img.filter(ImageFilter.GaussianBlur(radius=radius))

def _op_sharpen(img: Image.Image, factor: int = 200, **_) -> Image.Image:
    return ImageEnhance.Sharpness(img).enhance(factor / 100)

def _op_color_balance(img: Image.Image,
                      r: int = 0, g: int = 0, b: int = 0, **_) -> Image.Image:
    base = img.convert("RGB")
    rc, gc, bc = base.split()
    def _lut(offset: int):
        return [max(0, min(255, i + offset)) for i in range(256)]
    rc = rc.point(_lut(r))
    gc = gc.point(_lut(g))
    bc = bc.point(_lut(b))
    return Image.merge("RGB", (rc, gc, bc))

def _op_tint(img: Image.Image,
             tint_r: int = 255, tint_g: int = 0, tint_b: int = 0,
             strength: int = 30, **_) -> Image.Image:
    base  = img.convert("RGB")
    solid = Image.new("RGB", base.size, (tint_r, tint_g, tint_b))
    return Image.blend(base, solid, strength / 100)

def _op_gamma(img: Image.Image, gamma_x10: int = 10, **_) -> Image.Image:
    gamma = gamma_x10 / 10
    lut = [min(255, int(255 * (i / 255) ** (1.0 / gamma))) for i in range(256)]
    base = img.convert("RGB")
    r, g, b = base.split()
    return Image.merge("RGB", (r.point(lut), g.point(lut), b.point(lut)))

def _op_hslc(img: Image.Image,
             hue: int = 0, saturation: int = 0,
             lightness: int = 0, contrast: int = 0, **_) -> Image.Image:
    out = img.convert("RGB")
    # Hue shift via HSV channel (H is 0-255 ↔ 0-360°)
    if hue:
        hsv = out.convert("HSV")
        h, s, v = hsv.split()
        shift = round(hue / 360 * 255) % 256
        lut = [(i + shift) % 256 for i in range(256)]
        h   = h.point(lut)
        out = Image.merge("HSV", (h, s, v)).convert("RGB")
    if saturation:
        factor = 1.0 + saturation / 100
        out = ImageEnhance.Color(out).enhance(max(0.0, factor))
    if lightness:
        factor = 1.0 + lightness / 100
        out = ImageEnhance.Brightness(out).enhance(max(0.0, factor))
    if contrast:
        factor = 1.0 + contrast / 100
        out = ImageEnhance.Contrast(out).enhance(max(0.0, factor))
    return out

def _op_denoise(img: Image.Image, **_) -> Image.Image:
    return img.filter(ImageFilter.MedianFilter(size=3))

def _op_fix_compression(img: Image.Image, **_) -> Image.Image:
    """Reduce JPEG-block artifacts with a gentle smooth + mild sharpen."""
    out = img.filter(ImageFilter.SMOOTH_MORE)
    out = ImageEnhance.Sharpness(out).enhance(1.3)
    return out


# ── Shared dialog helpers ─────────────────────────────────────────────────────

def _dlg_ss() -> str:
    return (
        f"QDialog{{background:{BG};color:{PRI};font-family:{FONT};}}"
        f"QLabel{{background:transparent;color:{PRI};font-size:{FONT_SM}px;}}"
        f"QSlider::groove:horizontal{{background:{CAR};height:4px;border-radius:2px;}}"
        f"QSlider::handle:horizontal{{background:{ACC};width:12px;height:12px;"
        f"margin:-4px 0;border-radius:6px;}}"
        f"QSlider::sub-page:horizontal{{background:{ACC};border-radius:2px;}}")

def _btn_ok_ss() -> str:
    return (
        f"QPushButton{{background:{ACC};color:{PRI};border:none;border-radius:4px;"
        f"font-family:{FONT};font-size:{FONT_SM}px;font-weight:bold;padding:4px 16px;}}"
        f"QPushButton:hover{{background:#185FA5;}}")

def _btn_cx_ss() -> str:
    return (
        f"QPushButton{{background:{MUT};color:{PRI};border:none;border-radius:4px;"
        f"font-family:{FONT};font-size:{FONT_SM}px;font-weight:bold;padding:4px 16px;}}"
        f"QPushButton:hover{{background:#555577;}}")


class _ParamDialog(QDialog):
    """Styled base dialog: optional preview pane + controls + OK/Cancel."""

    def __init__(self, title: str, preview: Image.Image | None = None,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(_dlg_ss())
        self._preview_img = preview

        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        outer.setContentsMargins(16, 16, 16, 12)

        # Preview
        if preview:
            self._prev_lbl = QLabel()
            self._prev_lbl.setAlignment(Qt.AlignCenter)
            self._prev_lbl.setFixedSize(_PREVIEW_SIZE, _PREVIEW_SIZE)
            self._prev_lbl.setStyleSheet(
                f"background:{CAR};border:1px solid {MUT};border-radius:4px;")
            self._refresh_preview(preview)
            outer.addWidget(self._prev_lbl, alignment=Qt.AlignHCenter)
        else:
            self._prev_lbl = None

        # Controls area (subclasses populate _ctl_layout)
        ctl_w = QWidget()
        ctl_w.setStyleSheet("background:transparent;")
        self._ctl_layout = QVBoxLayout(ctl_w)
        self._ctl_layout.setContentsMargins(0, 0, 0, 0)
        self._ctl_layout.setSpacing(8)
        outer.addWidget(ctl_w)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{MUT};border:none;")
        sep.setFixedHeight(1)
        outer.addWidget(sep)

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._btn_cancel = QPushButton("Cancel")
        self._btn_cancel.setStyleSheet(_btn_cx_ss())
        self._btn_ok = QPushButton("Apply")
        self._btn_ok.setStyleSheet(_btn_ok_ss())
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_ok.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_ok)
        outer.addLayout(btn_row)

    def _refresh_preview(self, img: Image.Image):
        if self._prev_lbl is None:
            return
        self._prev_lbl.setPixmap(_pil_to_qpixmap(_fit_preview(img)))

    def _add_slider(self, label: str, lo: int, hi: int, val: int,
                    on_change=None, fmt: str = "{}") -> QSlider:
        """Add a labeled slider row; returns the QSlider."""
        row = QWidget()
        row.setStyleSheet("background:transparent;")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        lbl = QLabel(label)
        lbl.setFixedWidth(140)
        sl = QSlider(Qt.Horizontal)
        sl.setRange(lo, hi)
        sl.setValue(val)
        val_lbl = QLabel(fmt.format(val))
        val_lbl.setFixedWidth(44)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        def _cb(v):
            val_lbl.setText(fmt.format(v))
            if on_change:
                on_change(v)
        sl.valueChanged.connect(_cb)
        h.addWidget(lbl)
        h.addWidget(sl, stretch=1)
        h.addWidget(val_lbl)
        self._ctl_layout.addWidget(row)
        return sl


# ── Parameter dialogs ─────────────────────────────────────────────────────────

class _ScaleDialog(_ParamDialog):
    def __init__(self, default_pct: int, lo: int, hi: int,
                 preview: Image.Image | None, parent=None):
        super().__init__("Scale Image", preview, parent)
        self.setFixedWidth(400)
        def _update(v):
            if preview:
                self._refresh_preview(_op_scale(preview, percent=v))
        self._sl = self._add_slider("Scale  (%)", lo, hi, default_pct,
                                    on_change=_update, fmt="{}%")

    @property
    def percent(self) -> int:
        return self._sl.value()


class _BlurDialog(_ParamDialog):
    def __init__(self, preview: Image.Image | None, parent=None):
        super().__init__("Blur", preview, parent)
        self.setFixedWidth(400)
        def _update(v):
            if preview:
                self._refresh_preview(_op_blur(preview, radius=v))
        self._sl = self._add_slider("Radius (px)", 1, 20, 2,
                                    on_change=_update)

    @property
    def radius(self) -> int:
        return self._sl.value()


class _SharpenDialog(_ParamDialog):
    def __init__(self, preview: Image.Image | None, parent=None):
        super().__init__("Sharpen", preview, parent)
        self.setFixedWidth(400)
        def _update(v):
            if preview:
                self._refresh_preview(_op_sharpen(preview, factor=v))
        self._sl = self._add_slider("Strength", 110, 500, 200,
                                    on_change=_update, fmt="{}%")

    @property
    def factor(self) -> int:
        return self._sl.value()


class _ColorBalanceDialog(_ParamDialog):
    def __init__(self, preview: Image.Image | None, parent=None):
        super().__init__("Color Balance", preview, parent)
        self.setFixedWidth(420)
        def _upd(v=None):
            if preview:
                self._refresh_preview(
                    _op_color_balance(preview,
                                      r=self._r.value(),
                                      g=self._g.value(),
                                      b=self._b.value()))
        self._r = self._add_slider("Red", -100, 100, 0, on_change=_upd)
        self._g = self._add_slider("Green", -100, 100, 0, on_change=_upd)
        self._b = self._add_slider("Blue", -100, 100, 0, on_change=_upd)

    @property
    def rgb(self) -> tuple[int, int, int]:
        return self._r.value(), self._g.value(), self._b.value()


class _TintDialog(_ParamDialog):
    def __init__(self, preview: Image.Image | None, parent=None):
        super().__init__("Tint", preview, parent)
        self.setFixedWidth(420)
        self._color = QColor(255, 80, 80)   # default: warm red

        # Color swatch + picker button
        swatch_row = QWidget()
        swatch_row.setStyleSheet("background:transparent;")
        sh = QHBoxLayout(swatch_row)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(8)
        lbl = QLabel("Tint Color")
        lbl.setFixedWidth(140)
        self._swatch = QLabel()
        self._swatch.setFixedSize(40, 20)
        self._swatch.setStyleSheet(
            f"background:{self._color.name()};border:1px solid {MUT};border-radius:3px;")
        btn_pick = QPushButton("Pick…")
        btn_pick.setFixedWidth(60)
        btn_pick.setStyleSheet(_btn_cx_ss())
        btn_pick.clicked.connect(self._pick_color)
        sh.addWidget(lbl)
        sh.addWidget(self._swatch)
        sh.addWidget(btn_pick)
        sh.addStretch()
        self._ctl_layout.addWidget(swatch_row)

        def _upd(v=None):
            if preview:
                self._refresh_preview(
                    _op_tint(preview,
                             tint_r=self._color.red(),
                             tint_g=self._color.green(),
                             tint_b=self._color.blue(),
                             strength=self._sl.value()))
        self._sl = self._add_slider("Strength", 1, 80, 30,
                                    on_change=_upd, fmt="{}%")

    def _pick_color(self):
        c = QColorDialog.getColor(self._color, self, "Choose Tint Color")
        if c.isValid():
            self._color = c
            self._swatch.setStyleSheet(
                f"background:{c.name()};border:1px solid {MUT};border-radius:3px;")
            if self._preview_img:
                self._refresh_preview(
                    _op_tint(self._preview_img,
                             tint_r=c.red(), tint_g=c.green(), tint_b=c.blue(),
                             strength=self._sl.value()))

    @property
    def tint_rgb(self) -> tuple[int, int, int]:
        return self._color.red(), self._color.green(), self._color.blue()

    @property
    def strength(self) -> int:
        return self._sl.value()


class _GammaDialog(_ParamDialog):
    def __init__(self, preview: Image.Image | None, parent=None):
        super().__init__("Gamma Correction", preview, parent)
        self.setFixedWidth(400)
        def _update(v):
            if preview:
                self._refresh_preview(_op_gamma(preview, gamma_x10=v))
        # Store as tenths: 10 = γ1.0, 5 = γ0.5, 30 = γ3.0
        self._sl = self._add_slider("Gamma  (÷10)", 3, 30, 10,
                                    on_change=_update, fmt="{}·10⁻¹")

    @property
    def gamma_x10(self) -> int:
        return self._sl.value()


class _HslcDialog(_ParamDialog):
    def __init__(self, preview: Image.Image | None, parent=None):
        super().__init__("HSLC — Hue / Saturation / Lightness / Contrast",
                         preview, parent)
        self.setFixedWidth(460)
        def _upd(v=None):
            if preview:
                self._refresh_preview(
                    _op_hslc(preview,
                             hue=self._hue.value(),
                             saturation=self._sat.value(),
                             lightness=self._lit.value(),
                             contrast=self._con.value()))
        self._hue = self._add_slider("Hue  (°)", -180, 180, 0, on_change=_upd)
        self._sat = self._add_slider("Saturation  (%)", -100, 100, 0, on_change=_upd)
        self._lit = self._add_slider("Lightness  (%)", -100, 100, 0, on_change=_upd)
        self._con = self._add_slider("Contrast  (%)", -100, 100, 0, on_change=_upd)

    @property
    def params(self) -> dict:
        return dict(hue=self._hue.value(),
                    saturation=self._sat.value(),
                    lightness=self._lit.value(),
                    contrast=self._con.value())


# ── Operation registry ────────────────────────────────────────────────────────
#
# Each entry: op_name → (fn, needs_dialog)
# fn signature: fn(img, **params) → Image
#
_OPS: dict[str, tuple] = {
    "enlarge":         (_op_scale,            True),
    "reduce":          (_op_scale,            True),
    "rotate_cw":       (_op_rotate_cw,        False),
    "rotate_ccw":      (_op_rotate_ccw,       False),
    "rotate_180":      (_op_rotate_180,       False),
    "flip_v":          (_op_flip_v,           False),
    "flip_h":          (_op_flip_h,           False),
    "blur":            (_op_blur,             True),
    "sharpen":         (_op_sharpen,          True),
    "color_balance":   (_op_color_balance,    True),
    "tint":            (_op_tint,             True),
    "gamma":           (_op_gamma,            True),
    "hslc":            (_op_hslc,             True),
    "denoise":         (_op_denoise,          False),
    "fix_compression": (_op_fix_compression,  False),
}


def _get_dialog_params(op_name: str,
                       preview: Image.Image | None,
                       parent) -> dict | None:
    """Show the appropriate dialog; return param dict or None if cancelled."""
    if op_name == "enlarge":
        dlg = _ScaleDialog(150, 101, 400, preview, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dict(percent=dlg.percent)

    if op_name == "reduce":
        dlg = _ScaleDialog(75, 10, 99, preview, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dict(percent=dlg.percent)

    if op_name == "blur":
        dlg = _BlurDialog(preview, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dict(radius=dlg.radius)

    if op_name == "sharpen":
        dlg = _SharpenDialog(preview, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dict(factor=dlg.factor)

    if op_name == "color_balance":
        dlg = _ColorBalanceDialog(preview, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        r, g, b = dlg.rgb
        return dict(r=r, g=g, b=b)

    if op_name == "tint":
        dlg = _TintDialog(preview, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        r, g, b = dlg.tint_rgb
        return dict(tint_r=r, tint_g=g, tint_b=b, strength=dlg.strength)

    if op_name == "gamma":
        dlg = _GammaDialog(preview, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dict(gamma_x10=dlg.gamma_x10)

    if op_name == "hslc":
        dlg = _HslcDialog(preview, parent)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.params

    return {}   # no-dialog ops


# ── Public API ────────────────────────────────────────────────────────────────

def apply_op(op_name: str, paths: list[str],
             preserve_metadata: bool, parent=None) -> int:
    """
    Apply *op_name* to every file in *paths*, in-place.
    Shows a parameter dialog if the operation requires one.
    Returns the number of files successfully modified.
    """
    fn, needs_dialog = _OPS[op_name]

    # Build preview from the first file
    preview: Image.Image | None = None
    if paths:
        try:
            preview = _fit_preview(Image.open(paths[0]))
        except Exception:
            preview = None

    # Collect parameters (may show a dialog)
    params = _get_dialog_params(op_name, preview, parent) if needs_dialog \
        else {}
    if params is None:
        return 0   # user cancelled

    # Apply to every selected file (parallel for batches of 2+)
    from concurrent.futures import ThreadPoolExecutor
    import threading
    count = 0
    count_lock = threading.Lock()

    def _apply_one(path: str):
        nonlocal count
        try:
            img  = Image.open(path)
            exif = _exif_bytes(img) if preserve_metadata else b""
            out  = fn(img, **params)
            _save_image(out, path, exif, preserve_metadata)
            with count_lock:
                count += 1
        except Exception as exc:
            print(f"[image_ops] {path}: {exc}")

    workers = min(6, len(paths))
    if workers <= 1:
        _apply_one(paths[0])
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_apply_one, paths))

    return count


# ── Batch Save As ─────────────────────────────────────────────────────────────

class _BatchSaveAsDialog(QDialog):
    """Format-conversion dialog for Batch Save As."""

    _FORMATS = ["PNG", "JPEG", "WebP", "BMP", "TIFF"]
    _LOSSY   = {"JPEG", "WebP"}

    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Save As")
        self.setModal(True)
        self.setFixedWidth(480)
        self.setStyleSheet(_dlg_ss())

        self._out_folder = str(Path(paths[0]).parent) if paths else ""

        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        outer.setContentsMargins(16, 16, 16, 12)

        # File count
        count_lbl = QLabel(f"{len(paths)} file(s) selected")
        count_lbl.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        outer.addWidget(count_lbl)

        # Format row
        fmt_row = QWidget()
        fmt_row.setStyleSheet("background:transparent;")
        fh = QHBoxLayout(fmt_row)
        fh.setContentsMargins(0, 0, 0, 0)
        fh.setSpacing(8)
        fmt_lbl = QLabel("Output Format")
        fmt_lbl.setFixedWidth(120)
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(self._FORMATS)
        self._fmt_combo.setStyleSheet(
            f"QComboBox{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"border-radius:4px;padding:3px 8px;font-family:{FONT};font-size:{FONT_SM}px;}}"
            f"QComboBox::drop-down{{border:none;}}"
            f"QComboBox QAbstractItemView{{background:{CAR};color:{PRI};"
            f"selection-background-color:{ACC};}}")
        fh.addWidget(fmt_lbl)
        fh.addWidget(self._fmt_combo, stretch=1)
        outer.addWidget(fmt_row)

        # Quality slider (JPEG / WebP only)
        self._qual_widget = QWidget()
        self._qual_widget.setStyleSheet("background:transparent;")
        qh = QHBoxLayout(self._qual_widget)
        qh.setContentsMargins(0, 0, 0, 0)
        qh.setSpacing(8)
        qual_lbl = QLabel("Quality")
        qual_lbl.setFixedWidth(120)
        self._qual_sl = QSlider(Qt.Horizontal)
        self._qual_sl.setRange(1, 100)
        self._qual_sl.setValue(90)
        self._qual_val = QLabel("90")
        self._qual_val.setFixedWidth(30)
        self._qual_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._qual_sl.valueChanged.connect(lambda v: self._qual_val.setText(str(v)))
        qh.addWidget(qual_lbl)
        qh.addWidget(self._qual_sl, stretch=1)
        qh.addWidget(self._qual_val)
        outer.addWidget(self._qual_widget)

        # Output folder row
        folder_row = QWidget()
        folder_row.setStyleSheet("background:transparent;")
        flh = QHBoxLayout(folder_row)
        flh.setContentsMargins(0, 0, 0, 0)
        flh.setSpacing(8)
        folder_hdr = QLabel("Output Folder")
        folder_hdr.setFixedWidth(120)
        self._folder_lbl = QLabel(self._out_folder)
        self._folder_lbl.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        self._folder_lbl.setWordWrap(True)
        btn_browse = QPushButton("Browse…")
        btn_browse.setFixedWidth(72)
        btn_browse.setStyleSheet(_btn_cx_ss())
        btn_browse.clicked.connect(self._browse)
        flh.addWidget(folder_hdr)
        flh.addWidget(self._folder_lbl, stretch=1)
        flh.addWidget(btn_browse)
        outer.addWidget(folder_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"background:{MUT};border:none;")
        sep.setFixedHeight(1)
        outer.addWidget(sep)

        # OK / Cancel
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(_btn_cx_ss())
        btn_ok = QPushButton("Save")
        btn_ok.setStyleSheet(_btn_ok_ss())
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self.accept)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        outer.addLayout(btn_row)

        self._fmt_combo.currentTextChanged.connect(self._on_fmt_changed)
        self._on_fmt_changed(self._fmt_combo.currentText())

    def _on_fmt_changed(self, fmt: str):
        self._qual_widget.setVisible(fmt in self._LOSSY)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(
            self, "Choose Output Folder", self._out_folder)
        if d:
            self._out_folder = d
            self._folder_lbl.setText(d)

    @property
    def format(self) -> str:
        return self._fmt_combo.currentText()

    @property
    def quality(self) -> int:
        return self._qual_sl.value()

    @property
    def out_folder(self) -> str:
        return self._out_folder


def batch_save_as(paths: list[str], parent=None) -> tuple[int, str]:
    """
    Show the Batch Save As dialog and convert all *paths* to the chosen format.
    Files are written to the chosen output folder; originals are untouched.
    Returns (count_saved, output_folder), or (0, "") if cancelled.
    """
    if not paths:
        return 0, ""

    dlg = _BatchSaveAsDialog(paths, parent)
    if dlg.exec() != QDialog.Accepted:
        return 0, ""

    fmt     = dlg.format
    quality = dlg.quality
    out_dir = Path(dlg.out_folder)
    out_dir.mkdir(parents=True, exist_ok=True)

    _EXT = {"PNG": ".png", "JPEG": ".jpg", "WebP": ".webp",
            "BMP": ".bmp", "TIFF": ".tiff"}
    ext = _EXT[fmt]

    from concurrent.futures import ThreadPoolExecutor
    import threading
    count = 0
    count_lock = threading.Lock()

    def _convert_one(src: str):
        nonlocal count
        try:
            img  = Image.open(src)
            stem = Path(src).stem
            out_path = out_dir / (stem + ext)
            n = 1
            while out_path.exists():
                out_path = out_dir / f"{stem}_{n}{ext}"
                n += 1

            kwargs: dict = {}
            if fmt == "JPEG":
                img = img.convert("RGB")
                kwargs["quality"]     = quality
                kwargs["subsampling"] = 0
            elif fmt == "WebP":
                kwargs["quality"] = quality

            img.save(str(out_path), fmt, **kwargs)
            with count_lock:
                count += 1
        except Exception as exc:
            print(f"[batch_save_as] {src}: {exc}")

    workers = min(6, len(paths))
    if workers <= 1:
        _convert_one(paths[0])
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_convert_one, paths))

    return count, str(out_dir)


# ── Batch Pipeline ────────────────────────────────────────────────────────────

class _BatchPipelineDialog(QDialog):
    """Build a multi-step pipeline: check ops, set params, optionally save as."""

    _FORMATS = ["PNG", "JPEG", "WebP", "BMP", "TIFF"]
    _LOSSY   = {"JPEG", "WebP"}

    # (display_label, op_key, [(param_key, lo, hi, default, suffix), ...])
    _OP_DEFS = [
        ("Enlarge",         "enlarge",         [("percent",      101, 400, 150, "%")]),
        ("Reduce",          "reduce",           [("percent",       10,  99,  75, "%")]),
        ("Rotate 90° CW",   "rotate_cw",        []),
        ("Rotate 90° CCW",  "rotate_ccw",       []),
        ("Rotate 180°",     "rotate_180",       []),
        ("Flip Vertical",   "flip_v",           []),
        ("Flip Horizontal", "flip_h",           []),
        ("Blur",            "blur",             [("radius",         1,  20,   2, "px")]),
        ("Sharpen",         "sharpen",          [("strength",     110, 500, 200, "%")]),
        ("Color Balance",   "color_balance",    [("r",           -100, 100,   0, "R"),
                                                 ("g",           -100, 100,   0, "G"),
                                                 ("b",           -100, 100,   0, "B")]),
        ("Gamma",           "gamma",            [("gamma ÷10",     3,  30,  10, "")]),
        ("HSLC",            "hslc",             [("H°",          -180, 180,   0, ""),
                                                 ("S%",          -100, 100,   0, ""),
                                                 ("L%",          -100, 100,   0, ""),
                                                 ("C%",          -100, 100,   0, "")]),
        ("Denoise",         "denoise",          []),
        ("Fix Compression", "fix_compression",  []),
    ]

    # Actual param keys passed to the op functions (order matches _OP_DEFS params)
    _PARAM_KEYS = {
        "enlarge":       ["percent"],
        "reduce":        ["percent"],
        "blur":          ["radius"],
        "sharpen":       ["factor"],
        "color_balance": ["r", "g", "b"],
        "gamma":         ["gamma_x10"],
        "hslc":          ["hue", "saturation", "lightness", "contrast"],
    }

    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Pipeline")
        self.setModal(True)
        self.setMinimumWidth(580)
        self.setStyleSheet(_dlg_ss())

        self._paths      = paths
        self._chk:   dict[str, QCheckBox]       = {}
        self._spins: dict[str, list[QSpinBox]]  = {}
        self._out_folder = str(Path(paths[0]).parent) if paths else ""

        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        outer.setContentsMargins(16, 16, 16, 12)

        hdr = QLabel(f"{len(paths)} file(s) selected — check operations to apply in order:")
        hdr.setStyleSheet(f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        outer.addWidget(hdr)

        # ── Op list in scroll area ────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{BG};border:1px solid {MUT};border-radius:4px;}}"
            f"QScrollBar:vertical{{background:{CAR};width:8px;border-radius:4px;}}"
            f"QScrollBar::handle:vertical{{background:{MUT};border-radius:4px;}}")

        ops_w = QWidget()
        ops_w.setStyleSheet(f"background:{BG};")
        ops_box = QVBoxLayout(ops_w)
        ops_box.setContentsMargins(8, 6, 8, 6)
        ops_box.setSpacing(0)

        chk_ss = (
            f"QCheckBox{{color:{PRI};font-family:{FONT};font-size:{FONT_SM}px;"
            f"background:transparent;spacing:6px;min-width:148px;}}"
            f"QCheckBox::indicator{{width:13px;height:13px;border:1px solid {MUT};"
            f"border-radius:3px;background:{CAR};}}"
            f"QCheckBox::indicator:checked{{background:{ACC};}}")
        sb_ss = (
            f"QSpinBox{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"border-radius:3px;padding:1px 3px;font-family:{FONT};"
            f"font-size:{FONT_SM}px;min-width:54px;max-width:70px;}}"
            f"QSpinBox:disabled{{color:{MUT};}}"
            f"QSpinBox::up-button,QSpinBox::down-button{{width:14px;}}")
        p_lbl_ss = (
            f"color:{MUT};font-size:{FONT_SM}px;background:transparent;")

        for disp_lbl, op_key, params in self._OP_DEFS:
            row_w = QWidget()
            row_w.setStyleSheet("background:transparent;")
            row_h = QHBoxLayout(row_w)
            row_h.setContentsMargins(2, 3, 2, 3)
            row_h.setSpacing(6)

            chk = QCheckBox(disp_lbl)
            chk.setStyleSheet(chk_ss)
            row_h.addWidget(chk)
            self._chk[op_key] = chk

            spins: list[QSpinBox] = []
            for p_lbl_txt, lo, hi, default, sfx in params:
                lbl = QLabel(p_lbl_txt + (":" if sfx == "" else ""))
                lbl.setStyleSheet(p_lbl_ss)
                sb = QSpinBox()
                sb.setRange(lo, hi)
                sb.setValue(default)
                sb.setStyleSheet(sb_ss)
                sb.setEnabled(False)
                if sfx:
                    sb.setSuffix(" " + sfx)
                row_h.addWidget(lbl)
                row_h.addWidget(sb)
                spins.append(sb)
            self._spins[op_key] = spins

            row_h.addStretch(1)
            ops_box.addWidget(row_w)

            chk.toggled.connect(
                lambda checked, s=spins: [sb.setEnabled(checked) for sb in s])

        ops_box.addStretch(1)
        scroll.setWidget(ops_w)
        scroll.setFixedHeight(300)
        outer.addWidget(scroll)

        # ── Save As section ───────────────────────────────────────────────────
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet(f"background:{MUT};border:none;")
        sep1.setFixedHeight(1)
        outer.addWidget(sep1)

        self._save_as_chk = QCheckBox(
            "Save As  (convert format — original files are not modified)")
        self._save_as_chk.setStyleSheet(chk_ss.replace("min-width:148px;", ""))
        outer.addWidget(self._save_as_chk)

        self._sa_opts = QWidget()
        self._sa_opts.setStyleSheet("background:transparent;")
        sa_vbox = QVBoxLayout(self._sa_opts)
        sa_vbox.setContentsMargins(20, 4, 0, 0)
        sa_vbox.setSpacing(6)

        # Format + quality
        fq_row = QWidget()
        fq_row.setStyleSheet("background:transparent;")
        fq_h = QHBoxLayout(fq_row)
        fq_h.setContentsMargins(0, 0, 0, 0)
        fq_h.setSpacing(8)
        fmt_hdr = QLabel("Format:")
        fmt_hdr.setStyleSheet(f"color:{PRI};font-size:{FONT_SM}px;background:transparent;")
        self._fmt_combo = QComboBox()
        self._fmt_combo.addItems(self._FORMATS)
        self._fmt_combo.setFixedWidth(90)
        self._fmt_combo.setStyleSheet(
            f"QComboBox{{background:{CAR};color:{PRI};border:1px solid {MUT};"
            f"border-radius:4px;padding:2px 6px;font-family:{FONT};font-size:{FONT_SM}px;}}"
            f"QComboBox::drop-down{{border:none;}}"
            f"QComboBox QAbstractItemView{{background:{CAR};color:{PRI};"
            f"selection-background-color:{ACC};}}")
        self._qual_lbl = QLabel("Quality:")
        self._qual_lbl.setStyleSheet(f"color:{PRI};font-size:{FONT_SM}px;background:transparent;")
        self._qual_spin = QSpinBox()
        self._qual_spin.setRange(1, 100)
        self._qual_spin.setValue(90)
        self._qual_spin.setStyleSheet(sb_ss)
        fq_h.addWidget(fmt_hdr)
        fq_h.addWidget(self._fmt_combo)
        fq_h.addWidget(self._qual_lbl)
        fq_h.addWidget(self._qual_spin)
        fq_h.addStretch(1)
        sa_vbox.addWidget(fq_row)

        # Output folder
        fo_row = QWidget()
        fo_row.setStyleSheet("background:transparent;")
        fo_h = QHBoxLayout(fo_row)
        fo_h.setContentsMargins(0, 0, 0, 0)
        fo_h.setSpacing(8)
        fo_hdr = QLabel("Output:")
        fo_hdr.setStyleSheet(f"color:{PRI};font-size:{FONT_SM}px;background:transparent;")
        fo_hdr.setFixedWidth(52)
        self._folder_lbl = QLabel(self._out_folder)
        self._folder_lbl.setStyleSheet(
            f"color:{SEC};font-size:{FONT_SM}px;background:transparent;")
        self._folder_lbl.setWordWrap(True)
        btn_browse = QPushButton("Browse…")
        btn_browse.setFixedWidth(72)
        btn_browse.setStyleSheet(_btn_cx_ss())
        btn_browse.clicked.connect(self._browse)
        fo_h.addWidget(fo_hdr)
        fo_h.addWidget(self._folder_lbl, stretch=1)
        fo_h.addWidget(btn_browse)
        sa_vbox.addWidget(fo_row)

        outer.addWidget(self._sa_opts)
        self._sa_opts.hide()

        self._save_as_chk.toggled.connect(self._sa_opts.setVisible)
        self._fmt_combo.currentTextChanged.connect(self._update_quality_vis)
        self._update_quality_vis(self._fmt_combo.currentText())

        # ── Footer ────────────────────────────────────────────────────────────
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet(f"background:{MUT};border:none;")
        sep2.setFixedHeight(1)
        outer.addWidget(sep2)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(_btn_cx_ss())
        n_word = "file" if len(paths) == 1 else "files"
        self._btn_run = QPushButton(f"Run on {len(paths)} {n_word}")
        self._btn_run.setStyleSheet(_btn_ok_ss())
        btn_cancel.clicked.connect(self.reject)
        self._btn_run.clicked.connect(self._on_run)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(self._btn_run)
        outer.addLayout(btn_row)

    def _update_quality_vis(self, fmt: str):
        visible = fmt in self._LOSSY
        self._qual_lbl.setVisible(visible)
        self._qual_spin.setVisible(visible)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(
            self, "Choose Output Folder", self._out_folder)
        if d:
            self._out_folder = d
            self._folder_lbl.setText(d)

    def _on_run(self):
        if not self.pipeline and not self._save_as_chk.isChecked():
            QMessageBox.information(
                self, "Batch Pipeline",
                "Please check at least one operation or enable Save As.")
            return
        self.accept()

    @property
    def pipeline(self) -> list[tuple[str, dict]]:
        steps = []
        for _disp, op_key, params in self._OP_DEFS:
            if not self._chk[op_key].isChecked():
                continue
            keys   = self._PARAM_KEYS.get(op_key, [])
            spins  = self._spins[op_key]
            p_dict = {k: spins[i].value() for i, k in enumerate(keys)}
            steps.append((op_key, p_dict))
        return steps

    @property
    def save_as(self) -> dict | None:
        if not self._save_as_chk.isChecked():
            return None
        return {
            "format":  self._fmt_combo.currentText(),
            "quality": self._qual_spin.value(),
            "folder":  self._out_folder,
        }


def run_pipeline(steps: list[tuple[str, dict]],
                 save_as: dict | None,
                 paths:   list[str],
                 preserve_meta: bool = True) -> int:
    """Apply *steps* to each path in sequence. If *save_as* is set the result is
    written to a new file (original untouched); otherwise saved in-place.
    Returns the number of successfully processed files."""
    from concurrent.futures import ThreadPoolExecutor
    import threading

    _EXT = {"PNG": ".png", "JPEG": ".jpg", "WebP": ".webp",
            "BMP": ".bmp", "TIFF": ".tiff"}
    count      = 0
    count_lock = threading.Lock()

    def _process_one(src: str):
        nonlocal count
        try:
            img  = Image.open(src)
            exif = _exif_bytes(img) if preserve_meta else b""
            for op_key, params in steps:
                fn  = _OPS[op_key][0]
                img = fn(img, **params)

            if save_as:
                fmt     = save_as["format"]
                quality = save_as["quality"]
                out_dir = Path(save_as["folder"])
                out_dir.mkdir(parents=True, exist_ok=True)
                ext      = _EXT[fmt]
                stem     = Path(src).stem
                out_path = out_dir / (stem + ext)
                n = 1
                while out_path.exists():
                    out_path = out_dir / f"{stem}_{n}{ext}"
                    n += 1
                kwargs: dict = {}
                if fmt == "JPEG":
                    img = img.convert("RGB")
                    kwargs["quality"]     = quality
                    kwargs["subsampling"] = 0
                elif fmt == "WebP":
                    kwargs["quality"] = quality
                if preserve_meta and exif and fmt in ("JPEG", "WebP", "TIFF"):
                    kwargs["exif"] = exif
                img.save(str(out_path), fmt, **kwargs)
            else:
                _save_image(img, src, exif, preserve_meta)

            with count_lock:
                count += 1
        except Exception as exc:
            print(f"[run_pipeline] {src}: {exc}")

    workers = min(6, len(paths))
    if workers <= 1:
        _process_one(paths[0])
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(_process_one, paths))

    return count


def show_batch_pipeline(paths: list[str], parent=None) -> tuple[int, str]:
    """Show the pipeline dialog and run if confirmed.
    Returns (count_processed, output_folder)."""
    if not paths:
        return 0, ""
    dlg = _BatchPipelineDialog(paths, parent)
    if dlg.exec() != QDialog.Accepted:
        return 0, ""
    steps   = dlg.pipeline
    save_as = dlg.save_as
    n       = run_pipeline(steps, save_as, paths)
    folder  = save_as["folder"] if save_as else str(Path(paths[0]).parent)
    return n, folder
