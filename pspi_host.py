"""
pspi_host.py — ThumbsAI bridge to pspiHost.dll
(spetric/Photoshop-Plugin-Host, https://github.com/spetric/Photoshop-Plugin-Host).

A mature, embeddable C++ host for Photoshop .8bf filters. We delegate the fragile
PS-host emulation (FilterRecord ABI, suites, tiling, and — the part our hand-rolled
plugin_host.py couldn't crack — driving the filter's interactive settings dialog) to
this engine. It exposes the SAME run_plugin_filter(plugin, PIL_image, hwnd) entry the
old host did, so it's a drop-in.

REQUIRES pspiHost.dll built for **64-bit** (matching this Python). Put it next to this
file (or set the PSPI_DLL env var). On x64 there is a single calling convention, so
ctypes.WinDLL/CDLL are equivalent. See get_dll_status() for diagnostics.

API reference (pspiHost/pspiHost.h, pspiGlobals.h):
    const char* pspiGetVersion(void)
    int  pspiSetPath(const wchar_t *filterFolder)
    int  pspiSetImage(TImgType, int w, int h, void *buf, int stride, void *alpha=0, int aStride=0)
    int  pspiSetImageOrientation(TImgOrientation)
    int  pspiSetRoi(int top,left,bottom,right)
    int  pspiReleaseAllImages(void)
    int  pspiSetProgressCallBack(PROGRESSCALLBACK)
    int  pspiPlugInLoad(const wchar_t *filter)
    int  pspiPlugInAbout(HWND)
    int  pspiPlugInExecute(HWND)          # shows the dialog + applies, in place
"""
from __future__ import annotations
import os
import ctypes
from ctypes import c_int, c_char_p, c_wchar_p, c_void_p, c_uint, WINFUNCTYPE
from pathlib import Path

# ── TImgType ────────────────────────────────────────────────────────────────
PSPI_IMG_TYPE_BGR, PSPI_IMG_TYPE_BGRA, PSPI_IMG_TYPE_RGB, \
    PSPI_IMG_TYPE_RGBA, PSPI_IMG_TYPE_GRAY, PSPI_IMG_TYPE_GRAYA = range(6)
# ── TImgOrientation ─────────────────────────────────────────────────────────
PSPI_IMG_ORIENTATION_ASIS = 0
PSPI_IMG_ORIENTATION_INVERT = 1
# ── return codes ────────────────────────────────────────────────────────────
PSPI_OK = 0
PSPI_ERR_FILTER_CANCELED = 5
_PSPI_ERR = {
    1: "FILTER_NOT_LOADED", 2: "FILTER_BAD_PROC", 3: "FILTER_ABOUT_ERROR",
    4: "FILTER_DUMMY_PROC", 5: "FILTER_CANCELED", 6: "FILTER_CRASHED",
    7: "FILTER_INVALID", 10: "IMAGE_INVALID", 11: "MEMORY_ALLOC",
    12: "INIT_PATH_EMPTY", 13: "WORK_PATH_EMPTY", 14: "BAD_PARAM",
    15: "BAD_IMAGE_TYPE",
}

PROGRESSCALLBACK = WINFUNCTYPE(None, c_uint, c_uint)

_dll = None
_dll_err: str | None = None


def _candidate_paths():
    here = Path(__file__).parent
    env = os.environ.get("PSPI_DLL")
    cands = []
    if env:
        cands.append(Path(env))
    cands += [here / "pspiHost.dll", here / "bin" / "pspiHost.dll",
              here / "plugins" / "pspiHost.dll"]
    return cands


def _bind(d):
    d.pspiGetVersion.restype = c_char_p
    d.pspiGetVersion.argtypes = []
    d.pspiSetPath.restype = c_int
    d.pspiSetPath.argtypes = [c_wchar_p]
    d.pspiSetImage.restype = c_int
    d.pspiSetImage.argtypes = [c_int, c_int, c_int, c_void_p, c_int, c_void_p, c_int]
    d.pspiSetImageOrientation.restype = c_int
    d.pspiSetImageOrientation.argtypes = [c_int]
    d.pspiSetRoi.restype = c_int
    d.pspiSetRoi.argtypes = [c_int, c_int, c_int, c_int]
    d.pspiReleaseAllImages.restype = c_int
    d.pspiReleaseAllImages.argtypes = []
    d.pspiSetProgressCallBack.restype = c_int
    d.pspiSetProgressCallBack.argtypes = [PROGRESSCALLBACK]
    d.pspiPlugInLoad.restype = c_int
    d.pspiPlugInLoad.argtypes = [c_wchar_p]
    d.pspiPlugInAbout.restype = c_int
    d.pspiPlugInAbout.argtypes = [c_void_p]
    d.pspiPlugInExecute.restype = c_int
    d.pspiPlugInExecute.argtypes = [c_void_p]


def _load():
    global _dll, _dll_err
    if _dll is not None:
        return _dll
    if _dll_err is not None:
        return None
    tried = []
    for p in _candidate_paths():
        if not p.is_file():
            tried.append(f"{p} (missing)")
            continue
        try:
            d = ctypes.WinDLL(str(p))   # x64: cconv is uniform
            _bind(d)
            _dll = d
            return _dll
        except OSError as e:
            # WinError 193 = "%1 is not a valid Win32 application" → wrong bitness
            hint = " (likely a 32-bit DLL — build pspiHost as x64)" if getattr(e, "winerror", 0) == 193 else ""
            tried.append(f"{p}: {e}{hint}")
        except Exception as e:
            tried.append(f"{p}: {e}")
    _dll_err = "pspiHost.dll not loadable. Tried:\n  " + "\n  ".join(tried)
    return None


def available() -> bool:
    return _load() is not None


def get_dll_status() -> str:
    d = _load()
    if d:
        try:
            v = d.pspiGetVersion()
            v = v.decode("latin-1", "replace") if v else "?"
        except Exception:
            v = "?"
        return f"pspiHost.dll loaded (version {v})"
    return _dll_err or "pspiHost.dll not found"


def _err(rc) -> str:
    return _PSPI_ERR.get(rc, f"code {rc}")


def run_plugin_filter(plugin_info, image, parent_hwnd: int = 0, progress_cb=None):
    """Apply a .8bf filter to *image* via pspiHost.dll. Drop-in replacement for
    plugin_host.run_plugin_filter: returns a PIL.Image, or None if the user
    cancelled the plugin's dialog. Raises RuntimeError on failure.

    MUST be called on the GUI thread (pspiPlugInExecute shows a modal dialog)."""
    d = _load()
    if not d:
        raise RuntimeError(get_dll_status())
    from PIL import Image as _PIL

    path = getattr(plugin_info, "path", plugin_info)
    orig_mode = image.mode
    has_alpha = (orig_mode in ("RGBA", "LA") or
                 (orig_mode == "P" and "transparency" in image.info))
    if has_alpha:
        im, itype, ch = image.convert("RGBA"), PSPI_IMG_TYPE_RGBA, 4
    else:
        im, itype, ch = image.convert("RGB"), PSPI_IMG_TYPE_RGB, 3
    w, h = im.size
    stride = w * ch

    # Shared, mutable buffer — pspiHost writes the result back in place.
    raw = bytearray(im.tobytes())
    buf = (ctypes.c_char * len(raw)).from_buffer(raw)

    _keep_cb = None
    if progress_cb:
        @PROGRESSCALLBACK
        def _pcb(done, total):
            try:
                progress_cb(int(done), int(total))
            except Exception:
                pass
        _keep_cb = _pcb
        try:
            d.pspiSetProgressCallBack(_pcb)
        except Exception:
            pass

    try:
        # A work path is required (errors 12/13 otherwise); the plugin's own folder
        # also helps it locate sibling resources.
        try:
            d.pspiSetPath(str(Path(path).parent))
        except Exception:
            pass

        rc = d.pspiPlugInLoad(str(path))
        if rc != PSPI_OK:
            raise RuntimeError(f"pspiPlugInLoad failed: {_err(rc)}")

        rc = d.pspiSetImage(itype, w, h, ctypes.cast(buf, c_void_p), stride, None, 0)
        if rc != PSPI_OK:
            raise RuntimeError(f"pspiSetImage failed: {_err(rc)}")
        d.pspiSetImageOrientation(PSPI_IMG_ORIENTATION_ASIS)

        rc = d.pspiPlugInExecute(c_void_p(int(parent_hwnd) or 0))
        if rc == PSPI_ERR_FILTER_CANCELED:
            return None
        if rc != PSPI_OK:
            raise RuntimeError(f"pspiPlugInExecute failed: {_err(rc)}")

        result_bytes = bytes(raw)   # read the in-place result before releasing the view
    finally:
        try:
            d.pspiReleaseAllImages()
        except Exception:
            pass
        del buf   # drop the from_buffer view so the bytearray is unlocked

    out = _PIL.frombytes("RGBA" if ch == 4 else "RGB", (w, h), result_bytes)
    if orig_mode not in ("RGB", "RGBA") and orig_mode in ("L", "LA", "P", "I", "F"):
        try:
            out = out.convert(orig_mode)
        except Exception:
            pass
    return out


def run_plugin_about(plugin_info, parent_hwnd: int = 0):
    """Show a plugin's About box. Returns True on success."""
    d = _load()
    if not d:
        raise RuntimeError(get_dll_status())
    path = getattr(plugin_info, "path", plugin_info)
    if d.pspiPlugInLoad(str(path)) != PSPI_OK:
        return False
    return d.pspiPlugInAbout(c_void_p(int(parent_hwnd) or 0)) == PSPI_OK
