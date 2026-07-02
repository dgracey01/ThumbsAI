"""
Native Windows shell file drag for ThumbsAI.

Replicates how Windows Explorer (and ThumbsPlus) drag a file: it hands the OS the
shell's OWN IDataObject for the file(s) — the rich object carrying CF_HDROP plus the
Shell IDList / FileGroupDescriptor / FileContents formats — and runs the standard OLE
``DoDragDrop``. Browsers (reverse-image search, file-upload zones), Photoshop, Explorer,
etc. all consume that real FILE. Qt's ``QDrag`` only exposed CF_DIB image data (which
upload zones ignore) or a ``file://`` URL that search boxes mis-read as text
("file:///" / "about:blank#blocked"), which is why drag-to-browser never worked.

``drag_files(paths)`` returns True if the native drag ran (whatever target handled the
drop), or False if it could not build the data object — the caller then falls back to
the old Qt drag so behaviour never regresses.
"""
from __future__ import annotations

import os

try:
    import pythoncom
    import winerror
    import win32con
    from win32com.shell import shell, shellcon
    from win32com.server.util import wrap as _wrap
    _OK = True
except Exception:
    _OK = False


if _OK:
    class _DropSource:
        """Minimal IDropSource — standard 'drop on left-button release, cancel on Esc'."""
        _public_methods_ = ["QueryContinueDrag", "GiveFeedback"]
        _com_interfaces_ = [pythoncom.IID_IDropSource]

        def QueryContinueDrag(self, fEscapePressed, grfKeyState):
            if fEscapePressed:
                return winerror.DRAGDROP_S_CANCEL
            if not (grfKeyState & win32con.MK_LBUTTON):
                return winerror.DRAGDROP_S_DROP
            return winerror.S_OK

        def GiveFeedback(self, dwEffect):
            return winerror.DRAGDROP_S_USEDEFAULTCURSORS


def _data_object_for(paths):
    """The shell's own IDataObject for ``paths`` (which must share one parent folder —
    the normal case: a single image, or a multi-select within one folder)."""
    desktop = shell.SHGetDesktopFolder()
    parent_pidl = None
    child_pidls = []
    for p in paths:
        full = desktop.ParseDisplayName(0, None, os.path.abspath(p))[1]
        par, child = full[:-1], full[-1:]
        if parent_pidl is None:
            parent_pidl = par
        elif par != parent_pidl:
            return None                      # spans folders — one GetUIObjectOf can't
        child_pidls.append(child)
    if not child_pidls:
        return None
    folder = (desktop.BindToObject(parent_pidl, None, shell.IID_IShellFolder)
              if parent_pidl else desktop)
    res = folder.GetUIObjectOf(0, child_pidls, pythoncom.IID_IDataObject, 0)
    return res[1] if isinstance(res, tuple) else res


def available() -> bool:
    return _OK


def drag_files(paths) -> bool:
    """Run a native shell drag for ``paths``. True = it ran; False = fall back to Qt."""
    if not _OK:
        return False
    files = [os.path.abspath(p) for p in paths if p and os.path.isfile(p)]
    if not files:
        return False
    try:
        data = _data_object_for(files)
        if data is None:
            return False
        src = _wrap(_DropSource(), pythoncom.IID_IDropSource)
        pythoncom.DoDragDrop(data, src,
                             shellcon.DROPEFFECT_COPY | shellcon.DROPEFFECT_MOVE)
        return True
    except Exception:
        return False
