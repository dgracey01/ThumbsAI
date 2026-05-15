"""
plugin_host.py — Photoshop .8bf filter plugin host for ThumbsAI
Phase 1: 64-bit Windows, filter plugins (eFlt kind) only.

ABI contract:  #pragma pack(2), big-endian PIPL resources,
               PluginMain(short selector, void* params,
                          int32* data, short* result)
"""
from __future__ import annotations
import ctypes, ctypes.wintypes, struct, os, io, sys, threading
from ctypes import (
    WINFUNCTYPE, c_bool, c_int8, c_uint8, c_int16, c_uint16,
    c_int32, c_uint32, c_int64, c_double, c_void_p, c_wchar_p,
    POINTER, Structure, byref, cast,
    create_string_buffer, string_at, addressof,
)
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# Shared log-file handle set by run_plugin_filter so that _eprint (used inside
# callback closures like AcquireSuite) also writes to the per-run plugin.log.
_log_file_handle: list = [None]

def _eprint(*args):
    """Print to stderr safely — sys.stderr is None under pythonw.exe."""
    msg = " ".join(str(a) for a in args)
    try:
        if sys.stderr:
            print(msg, file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        fh = _log_file_handle[0]
        if fh:
            print(msg, file=fh, flush=True)
    except Exception:
        pass

# ── Photoshop selectors ───────────────────────────────────────────────────────
SEL_ABOUT      = 0
SEL_PARAMETERS = 1
SEL_PREPARE    = 2
SEL_START      = 3
SEL_CONTINUE   = 4
SEL_FINISH     = 5

# ── Codes ─────────────────────────────────────────────────────────────────────
MODE_RGB        =    3
noErr           =    0
userCanceledErr = -128

# ── PluginMain call signature ─────────────────────────────────────────────────
PluginMainProc = WINFUNCTYPE(
    None,
    c_int16,             # selector
    c_void_p,            # params  (FilterRecord* or AboutRecord*)
    POINTER(c_int64),    # data    (intptr_t* — plugin's persistent storage, 64-bit)
    POINTER(c_int16),    # result
)


# ── Proc-table structures (pack 2, as in PS SDK) ──────────────────────────────

class BufferProcs(Structure):
    _pack_ = 2
    _fields_ = [
        ("bufferProcsVersion", c_int16),
        ("numBufferProcs",     c_int16),
        ("allocateBuffer",     c_void_p),
        ("freeBuffer",         c_void_p),
        ("lockBuffer",         c_void_p),
        ("unlockBuffer",       c_void_p),
        ("spaceBuffer",        c_void_p),
    ]

class HandleProcs(Structure):
    _pack_ = 2
    _fields_ = [
        ("handleProcsVersion",       c_int16),
        ("numHandleProcs",           c_int16),
        ("newProc",                  c_void_p),
        ("disposeProc",              c_void_p),
        ("getSizeProc",              c_void_p),
        ("setSizeProc",              c_void_p),
        ("lockProc",                 c_void_p),
        ("unlockProc",               c_void_p),
        ("recoverSpaceProc",         c_void_p),
        ("disposeRegularHandleProc", c_void_p),
    ]

class ResourceProcs(Structure):
    _pack_ = 2
    _fields_ = [
        ("resourceProcsVersion", c_int16),
        ("numResourceProcs",     c_int16),
        ("countProc",            c_void_p),
        ("getProc",              c_void_p),
        ("deleteProc",           c_void_p),
        ("addProc",              c_void_p),
    ]

class PropertyProcs(Structure):
    _pack_ = 2
    _fields_ = [
        ("propertyProcsVersion", c_int16),
        ("numPropertyProcs",     c_int16),
        ("getPropertyProc",      c_void_p),
        ("setPropertyProc",      c_void_p),
    ]

class ImageServicesProcs(Structure):
    _pack_ = 2
    _fields_ = [
        ("imageServicesProcsVersion", c_int16),
        ("numImageServicesProcs",     c_int16),
        ("interpolate1DProc",         c_void_p),
        ("interpolate2DProc",         c_void_p),
    ]

class ChannelPortProcs(Structure):
    _pack_ = 2
    _fields_ = [
        ("channelPortProcsVersion",   c_int16),
        ("numChannelPortProcs",       c_int16),
        ("readPixelsProc",            c_void_p),
        ("writeBasePixelsProc",       c_void_p),
        ("readPortForWritePortProc",  c_void_p),
    ]

class SPBasicSuite(Structure):
    """Minimal PICA SPBasic host suite — AcquireSuite returns 'not found' for all suites."""
    _fields_ = [
        ("AcquireSuite",    c_void_p),
        ("ReleaseSuite",    c_void_p),
        ("IsEqual",         c_void_p),
        ("AllocateBlock",   c_void_p),
        ("FreeBlock",       c_void_p),
        ("ReallocateBlock", c_void_p),
        ("Undefined",       c_void_p),
    ]

class _ReadDescProcs(Structure):
    """PS SDK ReadDescriptorProcs — lets plugin read back saved parameters."""
    _pack_ = 2
    _fields_ = [
        ("readDescriptorProcsVersion", c_int16),
        ("numReadDescriptorProcs",     c_int16),
        ("openReadDescriptorProc",     c_void_p),
        ("closeReadDescriptorProc",    c_void_p),
        ("getKeyProc",                 c_void_p),
        ("getIntegerProc",             c_void_p),
        ("getFloatProc",               c_void_p),
        ("getUnitFloatProc",           c_void_p),
        ("getBooleanProc",             c_void_p),
        ("getStringProc",              c_void_p),
        ("getAliasProc",               c_void_p),
        ("getEnumeratedProc",          c_void_p),
        ("getClassProc",               c_void_p),
        ("getSimpleReferenceProc",     c_void_p),
        ("getObjectProc",              c_void_p),
        ("getCountProc",               c_void_p),
        ("getStringProc2",             c_void_p),
        ("getPinnedIntegerProc",       c_void_p),
        ("getPinnedFloatProc",         c_void_p),
        ("getPinnedUnitFloatProc",     c_void_p),
    ]

class _WriteDescProcs(Structure):
    """PS SDK WriteDescriptorProcs — lets plugin save its parameters."""
    _pack_ = 2
    _fields_ = [
        ("writeDescriptorProcsVersion", c_int16),
        ("numWriteDescriptorProcs",     c_int16),
        ("openWriteDescriptorProc",     c_void_p),
        ("closeWriteDescriptorProc",    c_void_p),
        ("putIntegerProc",              c_void_p),
        ("putFloatProc",                c_void_p),
        ("putUnitFloatProc",            c_void_p),
        ("putBooleanProc",              c_void_p),
        ("putStringProc",               c_void_p),
        ("putAliasProc",                c_void_p),
        ("putEnumeratedProc",           c_void_p),
        ("putClassProc",                c_void_p),
        ("putSimpleReferenceProc",      c_void_p),
        ("putObjectProc",               c_void_p),
        ("putCountProc",                c_void_p),
        ("putStringProc2",              c_void_p),
        ("putScopedClassProc",          c_void_p),
        ("putScopedObjectProc",         c_void_p),
    ]

class _DescriptorParameters(Structure):
    """
    PS SDK DescriptorParameters — pointed to by FilterRecord.descriptorParameters.
    CS5 SDK PIFilter.h playInfo constants:
      0 = plugInDialogOptional  → show dialog only if plugin has no saved settings
      1 = plugInDialogRequired  → always show dialog
      2 = plugInDialogNone      → never show dialog (fully silent)
    We use 1 so plugins that have their own saved settings still show the dialog.
    """
    _pack_ = 2
    _fields_ = [
        ("descriptorID", c_int32),
        ("playInfo",     c_int16),   # 1 = plugInDialogRequired
        ("recordInfo",   c_int16),
        ("descriptor",   c_void_p),  # Handle — NULL = no saved params
        ("readProcs",    c_void_p),  # ReadDescriptorProcs*
        ("writeProcs",   c_void_p),  # WriteDescriptorProcs*
    ]

# ── About & Filter records ────────────────────────────────────────────────────

class PlatformData(Structure):
    _pack_ = 2
    _fields_ = [
        ("hwnd",       c_void_p),
        ("filterCase", c_int16),
        ("isMac",      c_uint8),
        ("_reserved",  c_uint8),
    ]

class AboutRecord(Structure):
    """Passed as params for SEL_ABOUT."""
    _pack_ = 2
    _fields_ = [
        ("serialNumber", c_int32),
        ("testAbort",    c_void_p),
        ("platformData", c_void_p),   # PlatformData*
        ("sSPBasic",     c_void_p),
        ("plugInRef",    c_void_p),
        ("_reserved",    c_uint8 * 244),
    ]

class FilterRecord(Structure):
    """
    Photoshop CS5/CS6 FilterRecord — 64-bit Windows, #pragma pack(2).
    Matches PIFilter.h from the CS5 SDK (kCurrentFilterProcsVersion = 4).

    CRITICAL field-order notes vs. the old wrong layout:
      • offset 20 is 'parameters' (Handle, 8 bytes) — NOT 'imageMode' (2 bytes)
      • 'imageMode' is at offset 190, after hostSig/hostProc
      • 'bufferSpace' exists between maxSpace and inRect
      • 'platformData' (→ PlatformData / HWND) is before bufferProcs
      • 'handleProcs' is between displayPixels and colorServices
      • 'sSPBasic' is at offset 426 (inputRate+maskRate occupy 418–426)

    Offset annotations are for x64 with _pack_=2 (pointer alignment = 2).
    """
    _pack_ = 2
    _fields_ = [
        # ── Core ─────────────────────────────────────────── offsets 0–28 ─────
        ("serialNumber",    c_int32),    # 0
        ("abortProc",       c_void_p),   # 4
        ("progressProc",    c_void_p),   # 12
        ("parameters",      c_void_p),   # 20  Handle — plugin's parameter block

        # ── Image geometry ────────────────────────────────── 28–38 ──────────
        ("imageSize_v",     c_int32),    # 28  VPoint: v = rows (height)
        ("imageSize_h",     c_int32),    # 32         h = cols (width)
        ("planes",          c_int16),    # 36

        # ── Filter rect ───────────────────────────────────── 38–54 ──────────
        ("filterRect_top",   c_int32),   # 38  VRect
        ("filterRect_left",  c_int32),   # 42
        ("filterRect_bottom",c_int32),   # 46
        ("filterRect_right", c_int32),   # 50

        # ── Display-space colors (RGBColor = 3×uint16 = 6 bytes each) ────────
        ("background_r",    c_uint16),   # 54
        ("background_g",    c_uint16),   # 56
        ("background_b",    c_uint16),   # 58
        ("foreground_r",    c_uint16),   # 60
        ("foreground_g",    c_uint16),   # 62
        ("foreground_b",    c_uint16),   # 64

        # ── Memory limits ─────────────────────────────────── 66–74 ──────────
        ("maxSpace",        c_int32),    # 66
        ("bufferSpace",     c_int32),    # 70  plugin sets during Prepare

        # ── Input rect + planes ───────────────────────────── 74–94 ──────────
        ("inRect_top",      c_int32),    # 74
        ("inRect_left",     c_int32),    # 78
        ("inRect_bottom",   c_int32),    # 82
        ("inRect_right",    c_int32),    # 86
        ("inLoPlane",       c_int16),    # 90
        ("inHiPlane",       c_int16),    # 92

        # ── Output rect + planes ──────────────────────────── 94–114 ─────────
        ("outRect_top",     c_int32),    # 94
        ("outRect_left",    c_int32),    # 98
        ("outRect_bottom",  c_int32),    # 102
        ("outRect_right",   c_int32),    # 106
        ("outLoPlane",      c_int16),    # 110
        ("outHiPlane",      c_int16),    # 112

        # ── Mask rect ─────────────────────────────────────── 114–130 ────────
        ("maskRect_top",    c_int32),    # 114
        ("maskRect_left",   c_int32),    # 118
        ("maskRect_bottom", c_int32),    # 122
        ("maskRect_right",  c_int32),    # 126

        # ── Pixel buffers ─────────────────────────────────── 130–154 ────────
        ("inData",          c_void_p),   # 130
        ("inRowBytes",      c_int32),    # 138
        ("outData",         c_void_p),   # 142
        ("outRowBytes",     c_int32),    # 150

        # ── Mask flags ────────────────────────────────────── 154–158 ────────
        ("isFloating",      c_uint8),    # 154
        ("haveMask",        c_uint8),    # 155
        ("autoMask",        c_uint8),    # 156
        ("_pad_bool",       c_uint8),    # 157  → align maskData ptr to 158

        # ── Mask data ─────────────────────────────────────── 158–170 ────────
        ("maskData",        c_void_p),   # 158
        ("maskRowBytes",    c_int32),    # 166

        # ── Device-space colors (FilterColor = uint8[4]) ──── 170–178 ────────
        ("backColor",       c_uint8 * 4), # 170
        ("foreColor",       c_uint8 * 4), # 174

        # ── Host identification ───────────────────────────── 178–190 ────────
        ("hostSig",         c_uint32),   # 178  four-char OSType
        ("hostProc",        c_void_p),   # 182  host callback (NULL is fine)

        # ── Image properties ──────────────────────────────── 190–216 ────────
        ("imageMode",       c_int16),    # 190
        ("imageHRes",       c_int32),    # 192  Fixed 16.16 (72<<16 = 72 DPI)
        ("imageVRes",       c_int32),    # 196
        ("floatCoord_v",    c_int32),    # 200  floating selection origin
        ("floatCoord_h",    c_int32),    # 204
        ("wholeSize_v",     c_int32),    # 208  whole image size
        ("wholeSize_h",     c_int32),    # 212

        # ── Monitor (PlugInMonitor = {Ptr, Fixed}) ────────── 216–228 ────────
        ("monitor_gammaTable", c_void_p),# 216
        ("monitor_gamma",   c_int32),    # 224

        # ── Platform + proc tables ────────────────────────── 228–300 ────────
        ("platformData",    c_void_p),   # 228  → PlatformData {HWND, filterCase, …}
        ("bufferProcs",     c_void_p),   # 236
        ("resourceProcs",   c_void_p),   # 244
        ("processEvent",    c_void_p),   # 252
        ("displayPixels",   c_void_p),   # 260
        ("handleProcs",     c_void_p),   # 268
        ("colorServices",   c_void_p),   # 276
        ("advanceState",    c_void_p),   # 284
        ("propertyProcs",   c_void_p),   # 292

        # ── Version + extended procs ──────────────────────── 300–342 ────────
        ("version",                   c_int16),  # 300
        ("imageServicesProcs",        c_void_p), # 302
        ("descriptorParameters",      c_void_p), # 310
        ("errorString",               c_void_p), # 318
        ("channelPortProcs",          c_void_p), # 326
        ("documentInfo",              c_void_p), # 334

        # ── Layout flags ──────────────────────────────────── 342–350 ────────
        ("supportsDummyChannels",     c_int16),  # 342
        ("supportsAlternateLayouts",  c_int16),  # 344
        ("wantLayout",                c_int16),  # 346
        ("filterCaseInfoCount",       c_int16),  # 348

        # ── Filter case + Premiere ────────────────────────── 350–370 ────────
        ("filterCaseInfo",  c_void_p),   # 350
        ("dummyPlaneValue", c_int32),    # 358
        ("premiereHook",    c_void_p),   # 362

        # ── More callbacks ────────────────────────────────── 370–390 ────────
        ("advanceState2",       c_void_p), # 370
        ("supportsAbsolute",    c_int16),  # 378
        ("wantsAbsolute",       c_int16),  # 380
        ("getPropertyObsolete", c_void_p), # 382

        # ── Behaviour flags ───────────────────────────────── 390–394 ────────
        ("cannotUndo",        c_uint8),  # 390
        ("noAbort",           c_uint8),  # 391
        ("wantsMaskAsBitmap", c_uint8),  # 392
        ("_pad_flags",        c_uint8),  # 393

        # ── Dummy-plane counts ────────────────────────────── 394–402 ────────
        ("inPreDummyPlanes",  c_int16),  # 394
        ("inPostDummyPlanes", c_int16),  # 396
        ("outPreDummyPlanes", c_int16),  # 398
        ("outPostDummyPlanes",c_int16),  # 400

        # ── Edge-extension padding (Fixed 16.16) ──────────── 402–414 ────────
        ("inputPadding",    c_int32),    # 402
        ("outputPadding",   c_int32),    # 406
        ("maskPadding",     c_int32),    # 410

        # ── Alternate layout ──────────────────────────────── 414–418 ────────
        ("padBytes",          c_int16),  # 414
        ("isFirstAlternate",  c_int16),  # 416
        ("inputRate",         c_int32),  # 418  Fixed 16.16 input sampling rate
        ("maskRate",          c_int32),  # 422  Fixed 16.16 mask sampling rate

        # ── PICA suite pointers ───────────────────────────── 426– ───────────
        ("sSPBasic",        c_void_p),   # 426
        ("plugInRef",       c_void_p),   # 434

        # ── ICC + transparency ────────────────────────────── 442– ───────────
        ("transparentIndex",    c_int32), # 442
        ("flattenedImageData",  c_void_p),# 446
        ("iCCprofileSize",      c_int32), # 454
        ("iCCprofileData",      c_void_p),# 458
        ("canUseICCProfiles",   c_int32), # 466
        ("inDataHandling",      c_int32), # 470
        ("outDataHandling",     c_int32), # 474
        ("maskDataHandling",    c_int32), # 478

        # ── Layer-plane counts and byte strides (PSFilterPdn / CS5 SDK) ─────
        ("inLayerPlanes",          c_int16), # 482
        ("inTransparencyMask",     c_int16), # 484
        ("inLayerMasks",           c_int16), # 486
        ("inInvertedLayerMasks",   c_int16), # 488
        ("inNonLayerPlanes",       c_int16), # 490  ← 3 for flat RGB
        ("outLayerPlanes",         c_int16), # 492
        ("outTransparencyMask",    c_int16), # 494
        ("outLayerMasks",          c_int16), # 496
        ("outInvertedLayerMasks",  c_int16), # 498
        ("outNonLayerPlanes",      c_int16), # 500
        ("inColumnBytes",          c_int32), # 502  ← 3 for flat RGB
        ("inPlaneBytes",           c_int32), # 506  ← 1 for flat RGB
        ("outColumnBytes",         c_int32), # 510
        ("outPlaneBytes",          c_int32), # 514
        ("_reserved_tail",  c_uint8 * 220), # 518
    ]


# ── FilterRecord layout verification (runs at import time) ───────────────────
def _verify_fr_layout():
    checks = {"parameters": 20, "imageMode": 190, "handleProcs": 268, "sSPBasic": 426}
    ok = True
    for fname, expected in checks.items():
        actual = getattr(FilterRecord, fname).offset
        if actual != expected:
            _eprint(f"[8bf] LAYOUT MISMATCH: FilterRecord.{fname} @ {actual}, expected {expected}")
            ok = False
    status = "OK" if ok else "BAD"
    _eprint(f"[8bf] FilterRecord layout {status} (size={ctypes.sizeof(FilterRecord)}, "
            f"sSPBasic@{FilterRecord.sSPBasic.offset})")
_verify_fr_layout()

# ── Plugin metadata ───────────────────────────────────────────────────────────

@dataclass
class PluginInfo:
    path:             str
    name:             str   = ""
    category:         str   = ""
    kind:             str   = ""      # "eFlt" = filter
    version:          tuple = (1, 0)
    is_64bit:         bool  = True
    resource_id:      int   = 1
    # fici case-1 compatibility: True unless the plugin explicitly declares
    # it does NOT support a plain flat RGB image (case 1 = no transparency/mask).
    # Plugins with no fici in their PIPL default to True (assume compatible).
    supports_flat_rgb: bool = True
    _dll: object = field(default=None, repr=False, compare=False)

    @property
    def display_name(self) -> str:
        return self.name or Path(self.path).stem

    @property
    def compatible(self) -> bool:
        """True when this plugin is likely usable by ThumbsAI's flat-RGB host."""
        return self.is_64bit and self.supports_flat_rgb

    def __str__(self) -> str:
        cat = f"{self.category} / " if self.category else ""
        return f"{cat}{self.display_name}"


# ── PE bitness detection ──────────────────────────────────────────────────────

def _pe_is_64bit(path: str) -> bool | None:
    """Return True = 64-bit, False = 32-bit, None = not a valid PE."""
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                return None
            f.seek(0x3C)
            pe_off = struct.unpack_from("<I", f.read(4))[0]
            f.seek(pe_off)
            if f.read(4) != b"PE\x00\x00":
                return None
            machine = struct.unpack_from("<H", f.read(2))[0]
            return machine == 0x8664   # IMAGE_FILE_MACHINE_AMD64
    except Exception:
        return None


# ── PIPL resource reader ──────────────────────────────────────────────────────

def _read_pipl(dll_path: str) -> list[PluginInfo]:
    """Load DLL as data-only, enumerate PIPL resources, return PluginInfo list."""
    k32 = ctypes.windll.kernel32
    LOAD_LIBRARY_AS_DATAFILE = 0x00000002

    # ── CRITICAL: set restype AND argtypes for every handle-passing function ──
    # ctypes.windll defaults to c_int (32-bit) for both return values AND
    # arguments.  On x64, HMODULE / HRSRC / HGLOBAL / LPVOID are 64-bit.
    # Without argtypes, passing a 64-bit Python int as argument 1 raises
    # OverflowError ("int too long to convert").
    k32.LoadLibraryExW.restype   = c_void_p
    k32.LoadLibraryExW.argtypes  = [c_wchar_p, c_void_p, c_uint32]
    k32.FindResourceW.restype    = c_void_p
    k32.FindResourceW.argtypes   = [c_void_p, c_void_p, c_wchar_p]
    k32.SizeofResource.restype   = c_uint32
    k32.SizeofResource.argtypes  = [c_void_p, c_void_p]
    k32.LoadResource.restype     = c_void_p
    k32.LoadResource.argtypes    = [c_void_p, c_void_p]
    k32.LockResource.restype     = c_void_p
    k32.LockResource.argtypes    = [c_void_p]
    k32.FreeLibrary.restype      = c_int32
    k32.FreeLibrary.argtypes     = [c_void_p]
    # EnumResourceNamesW: wrap hmod in c_void_p instead of setting argtypes
    # (the callback arg makes argtypes awkward).

    hmod = k32.LoadLibraryExW(dll_path, None, LOAD_LIBRARY_AS_DATAFILE)
    if not hmod:
        return []

    names: list = []

    @WINFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p, c_void_p)  # BOOL return
    def _enum_cb(hModule, lpType, lpName, lParam):
        names.append(lpName)
        return 1

    # Pass hmod as c_void_p so ctypes treats it as pointer-sized, not c_int
    k32.EnumResourceNamesW(c_void_p(hmod), "PIPL", _enum_cb, None)

    infos: list[PluginInfo] = []
    for idx, res_name in enumerate(names):
        hrsrc = k32.FindResourceW(hmod, res_name, "PIPL")
        if not hrsrc:
            continue
        size    = k32.SizeofResource(hmod, hrsrc)
        hglobal = k32.LoadResource(hmod, hrsrc)
        if not hglobal:
            continue
        ptr = k32.LockResource(hglobal)
        if not ptr:
            continue
        raw  = ctypes.string_at(ptr, size)
        info = _parse_pipl(raw, dll_path, idx + 1)
        if info:
            infos.append(info)

    k32.FreeLibrary(c_void_p(hmod))
    return infos


def _parse_pipl(data: bytes, dll_path: str, res_id: int) -> PluginInfo | None:
    """Parse a raw PIPL blob into a PluginInfo.

    Windows PIPL binary layout (little-endian, from cnvtpipl / modern PS SDK):
      Header (10 bytes): uint32 version(=1) + uint16 pad + uint16 count + uint16 pad
      Properties:        uint32 vendorID + uint32 key + int32 propID + int32 dataLen + data[]
    OSType four-char codes are stored as LE uint32 (bytes reversed vs Mac big-endian).
    Kind values: "eFlt" = extended filter, "8BFM" = classic filter module.
    """
    if len(data) < 10:
        return None

    info = PluginInfo(path=dll_path, resource_id=res_id)
    pos  = 10   # skip 10-byte Windows PIPL header

    while pos + 16 <= len(data):
        # OSType key is stored LE (bytes reversed); reverse back to canonical form.
        key      = data[pos + 4 : pos + 8][::-1].decode("latin-1", errors="replace")
        data_len = struct.unpack_from("<I", data, pos + 12)[0]   # LE int32
        pos     += 16
        if pos + data_len > len(data):
            break
        prop = data[pos : pos + data_len]
        pos += (data_len + 3) & ~3   # advance to next 4-byte boundary

        if key == "catg" and prop:
            slen = prop[0]
            info.category = prop[1 : 1 + slen].decode("latin-1", errors="replace")
        elif key == "name" and prop:
            slen = prop[0]
            info.name = prop[1 : 1 + slen].decode("latin-1", errors="replace")
        elif key == "kind" and len(prop) >= 4:
            # Kind OSType is also stored LE — reverse to get canonical form.
            info.kind = prop[:4][::-1].decode("latin-1", errors="replace")
        elif key == "vers" and len(prop) >= 4:
            hi, lo = struct.unpack_from("<HH", prop, 0)   # LE shorts
            info.version = (hi, lo)
        elif key == "fici" and len(prop) >= 4:
            # FilterCaseInfo array: 4 bytes per case; case index 0 = plain flat
            # image (filterCaseDoesNotSupportTransparency).  inputHandling byte
            # == 0 means "not supported"; any other value means the plugin
            # can handle this case.  If fici is absent we assume compatible.
            info.supports_flat_rgb = (prop[0] != 0)

    # "eFlt" = Extended Filter (newer SDK), "8BFM" = Filter Module (classic PS)
    if info.kind in ("eFlt", "8BFM"):
        if not info.name:
            info.name = Path(dll_path).stem
        return info
    return None


def find_registered_plugin_dirs() -> list[str]:
    """
    Read vendor-registered plugin directories from the Windows registry.
    Many commercial plugins (Redfield, Topaz, Nik, etc.) store their install
    location under HKCU/HKLM Software keys.  Loading plugins from these paths
    satisfies the plugins' own host-directory checks, enabling their settings
    dialogs to appear.
    Returns a de-duplicated list of existing directory paths.
    """
    import winreg
    found: list[str] = []
    keys = [
        # Redfield (HKCU and HKLM)
        (winreg.HKEY_CURRENT_USER,  r"Software\Redfield",        "PluginsDir"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Redfield",        "PluginsDir"),
        (winreg.HKEY_CURRENT_USER,  r"Software\Wow6432Node\Redfield", "PluginsDir"),
        # Add other vendors here as needed
    ]
    for hive, subkey, valname in keys:
        try:
            with winreg.OpenKey(hive, subkey) as k:
                val, _ = winreg.QueryValueEx(k, valname)
                p = Path(str(val))
                if p.is_dir() and str(p) not in found:
                    found.append(str(p))
        except (FileNotFoundError, OSError):
            pass
    return found


def scan_plugin_dirs(dirs: list[str], include_registered: bool = True) -> list[PluginInfo]:
    """
    Scan directories recursively for .8bf filter plugins.
    When include_registered=True, also scans vendor-registered plugin dirs
    from the Windows registry so plugins load from their expected locations
    (required for commercial plugins that verify their own install path).
    """
    # Registered dirs take priority (so plugins load from their verified install
    # path, satisfying their own host-directory checks).  User dirs fill in any
    # plugins not already covered by the registered dirs.
    all_dirs: list[str] = []
    if include_registered:
        for rd in find_registered_plugin_dirs():
            norm = str(Path(rd))
            if norm not in all_dirs:
                all_dirs.append(norm)
    for d in dirs:
        norm = str(Path(d))
        if norm not in all_dirs:
            all_dirs.append(norm)

    # Deduplicate by resolved path to avoid loading the same .8bf file twice
    seen_files: set[str] = set()
    results: list[PluginInfo] = []
    for d in all_dirs:
        p = Path(d)
        if not p.is_dir():
            continue
        for bf in sorted(p.rglob("*.8bf")):
            key = bf.name.lower()   # same filename = same plugin; skip duplicates
            if key in seen_files:
                continue
            seen_files.add(key)
            is64 = _pe_is_64bit(str(bf))
            if is64 is None:
                continue
            for info in _read_pipl(str(bf)):
                info.is_64bit = is64
                if info.compatible:   # skip 32-bit and non-flat-RGB plugins
                    results.append(info)
    return results


# ── Host callbacks ────────────────────────────────────────────────────────────

# Kept alive while a plugin call is in-flight
_live_refs: list = []

# Suite names requested by the plugin since the last filter run (for diagnostics)
_suite_log: list = []

# Cookies returned by os.add_dll_directory — kept open for process lifetime
_dll_dir_cookies: list = []

# IAT-patched function objects — kept alive for process lifetime (one per DLL load)
_iat_live: list = []

# Counter incremented by the GetModuleFileNameA stub — proves whether the IAT
# patch is actually being reached by the plugin's code (not just written to).
_gmfn_hit_count: list = [0]   # mutable cell so the WINFUNCTYPE closure can write it

# ── Photoshop sentinel window ─────────────────────────────────────────────────
# Many commercial plugins call FindWindowA("Photoshop", NULL) during DllMain to
# detect whether they are running inside Photoshop.  If that call returns NULL
# they enter a headless mode: they apply their algorithm with default settings
# but never show their settings dialog.  We create one hidden window with the
# "Photoshop" class before loading any plugin DLL so FindWindowA succeeds.

_ps_sentinel_hwnd:  list = [0]   # [HWND] — kept alive for process lifetime
_ps_sentinel_refs:  list = []    # keeps WndProc + buffers alive

def _ensure_ps_sentinel() -> int:
    """Create (once) a hidden window with class 'Photoshop'. Returns the HWND."""
    if _ps_sentinel_hwnd[0]:
        return _ps_sentinel_hwnd[0]

    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32

    _WndProcT = WINFUNCTYPE(c_void_p, c_void_p, c_uint32, c_void_p, c_void_p)

    @_WndProcT
    def _wnd_proc(hwnd, msg, wparam, lparam):
        return u32.DefWindowProcA(hwnd, msg, wparam, lparam)

    _cls_buf = create_string_buffer(b"Photoshop")
    _ttl_buf = create_string_buffer(b"Adobe Photoshop")

    class _WNDCLASSEXA(Structure):
        _fields_ = [
            ("cbSize",        c_uint32),
            ("style",         c_uint32),
            ("lpfnWndProc",   c_void_p),
            ("cbClsExtra",    c_int32),
            ("cbWndExtra",    c_int32),
            ("hInstance",     c_void_p),
            ("hIcon",         c_void_p),
            ("hCursor",       c_void_p),
            ("hbrBackground", c_void_p),
            ("lpszMenuName",  c_void_p),
            ("lpszClassName", c_void_p),
            ("hIconSm",       c_void_p),
        ]

    # Set argtypes so ctypes uses c_void_p (64-bit) for pointer params instead
    # of defaulting to c_int (32-bit), which overflows on 64-bit addresses.
    k32.GetModuleHandleW.restype  = c_void_p
    k32.GetModuleHandleW.argtypes = [c_void_p]
    u32.RegisterClassExA.restype  = c_uint16
    u32.RegisterClassExA.argtypes = [c_void_p]
    u32.CreateWindowExA.restype   = c_void_p
    u32.CreateWindowExA.argtypes  = [
        c_uint32, c_void_p, c_void_p, c_uint32,
        c_int32, c_int32, c_int32, c_int32,
        c_void_p, c_void_p, c_void_p, c_void_p,
    ]

    _hinstance = k32.GetModuleHandleW(None)
    wc = _WNDCLASSEXA()
    wc.cbSize        = ctypes.sizeof(_WNDCLASSEXA)
    wc.lpfnWndProc   = cast(_wnd_proc, c_void_p).value
    wc.hInstance     = _hinstance
    wc.lpszClassName = cast(_cls_buf, c_void_p).value
    u32.RegisterClassExA(byref(wc))   # silently ignored if already registered

    hwnd = u32.CreateWindowExA(
        0,                                # dwExStyle
        cast(_cls_buf, c_void_p).value,   # lpClassName  = "Photoshop"
        cast(_ttl_buf, c_void_p).value,   # lpWindowName = "Adobe Photoshop"
        0,                                # dwStyle      (hidden)
        0, 0, 1, 1,                       # x, y, w, h
        0, 0,                             # hWndParent, hMenu
        _hinstance,                       # hInstance
        0,                                # lpParam
    )

    if hwnd:
        _ps_sentinel_hwnd[0] = hwnd
        _ps_sentinel_refs.extend([_wnd_proc, _cls_buf, _ttl_buf, wc])
        _eprint(f"[8bf] PS sentinel window created: hwnd=0x{hwnd:x} class='Photoshop'")
        # Position it off-screen so it never appears to the user.
        u32.SetWindowPos(hwnd, 0, -32000, -32000, 1, 1, 0x0010)  # SWP_NOACTIVATE
    else:
        _eprint(f"[8bf] PS sentinel window FAILED (CreateWindowExA returned 0)")

    return _ps_sentinel_hwnd[0]


def _hook_gmfn_inline():
    """
    Patch the actual code bytes of kernel32!GetModuleFileNameA with a 12-byte
    absolute JMP to our stub.  Works even when the plugin cached the function
    pointer at DllMain time (bypassing IAT hooks).

    Returns opaque state that must be passed to _unhook_gmfn_inline() to
    restore the original bytes.  Keep the returned state alive until after
    restore (it keeps the WINFUNCTYPE object alive).
    """
    k32 = ctypes.windll.kernel32
    _FAKE_PATH_A = b"C:\\Program Files\\Adobe\\Adobe Photoshop 2023\\Photoshop.exe"
    _real_w = k32.GetModuleFileNameW   # NOT patched — safe to call from our hook

    @WINFUNCTYPE(c_uint32, c_void_p, c_void_p, c_uint32)
    def _fake(hmod, buf, size):
        _gmfn_hit_count[0] += 1
        if not hmod:
            n = min(size - 1, len(_FAKE_PATH_A))
            ctypes.memmove(buf, _FAKE_PATH_A, n)
            ctypes.memset(buf + n, 0, 1)
            return n
        # Non-NULL hmod: delegate to GetModuleFileNameW (avoids recursion)
        wbuf = ctypes.create_unicode_buffer(size)
        n = _real_w(hmod, wbuf, size)
        if n > 0:
            ansi = wbuf.value.encode("mbcs", errors="replace")
            m    = min(size - 1, len(ansi))
            ctypes.memmove(buf, ansi, m)
            ctypes.memset(buf + m, 0, 1)
            return m
        return 0

    _iat_live.append(_fake)
    fake_addr = cast(_fake, c_void_p).value
    fn_addr   = cast(k32.GetModuleFileNameA, c_void_p).value
    orig_12   = ctypes.string_at(fn_addr, 12)

    # x64 absolute JMP: MOV RAX, abs64 (10 bytes) + JMP RAX (2 bytes)
    hook_bytes = b"\x48\xB8" + struct.pack("<Q", fake_addr) + b"\xFF\xE0"

    old_prot = c_uint32(0)
    k32.VirtualProtect(c_void_p(fn_addr), 12, 0x40, byref(old_prot))
    ctypes.memmove(fn_addr, hook_bytes, 12)
    k32.FlushInstructionCache(k32.GetCurrentProcess(), c_void_p(fn_addr), 12)

    return fn_addr, orig_12, old_prot.value, _fake   # keep _fake alive


def _unhook_gmfn_inline(state) -> int:
    """Restore GetModuleFileNameA's original bytes. Returns gmfn hit count."""
    fn_addr, orig_12, old_prot_val, _ = state
    k32    = ctypes.windll.kernel32
    dummy  = c_uint32(0)
    k32.VirtualProtect(c_void_p(fn_addr), 12, 0x40, byref(dummy))
    ctypes.memmove(fn_addr, orig_12, 12)
    k32.FlushInstructionCache(k32.GetCurrentProcess(), c_void_p(fn_addr), 12)
    k32.VirtualProtect(c_void_p(fn_addr), 12, old_prot_val, byref(dummy))
    return _gmfn_hit_count[0]


def _hook_reg_inline():
    """
    Patch advapi32!RegOpenKeyExA with a 12-byte absolute JMP so ANY call to it
    (including via cached function pointers) goes through our stub.

    The stub returns ERROR_FILE_NOT_FOUND for keys under Software\\Redfield\\*
    (forcing the plugin to behave as if it has no saved settings and show its
    dialog).  All other keys are forwarded to RegOpenKeyExW via ANSI→Wide
    conversion — the W variant is not patched so there's no recursion.

    Returns opaque state for _unhook_reg_inline().
    """
    adv32 = ctypes.windll.advapi32
    k32   = ctypes.windll.kernel32

    _real_rokw = adv32.RegOpenKeyExW
    _real_rokw.restype  = c_uint32
    _real_rokw.argtypes = [c_void_p, c_wchar_p, c_uint32, c_uint32, c_void_p]

    _ERROR_FILE_NOT_FOUND = 2

    @WINFUNCTYPE(c_uint32, c_void_p, c_void_p, c_uint32, c_uint32, c_void_p)
    def _fake_rok(hKey, subKey, options, access, phkResult):
        try:
            name = (ctypes.string_at(subKey, 256).split(b"\x00")[0]
                    .decode("latin-1")) if subKey else ""
            _eprint(f"[8bf] reg_hook RegOpenKeyExA({name!r})")
            # Block plugin-specific Redfield subkeys so saved settings aren't found.
            if "software\\redfield\\" in name.lower().replace("/", "\\"):
                _eprint(f"[8bf] reg_hook → BLOCKED (ERROR_FILE_NOT_FOUND)")
                return _ERROR_FILE_NOT_FOUND
            # All other keys: forward via W version (no recursion).
            return _real_rokw(hKey, name, options, access, phkResult)
        except Exception:
            return _ERROR_FILE_NOT_FOUND

    _iat_live.append(_fake_rok)
    fake_addr = cast(_fake_rok, c_void_p).value

    adv32.RegOpenKeyExA.restype  = c_void_p   # get real address
    fn_addr  = cast(adv32.RegOpenKeyExA, c_void_p).value
    orig_12  = ctypes.string_at(fn_addr, 12)
    hook_bytes = b"\x48\xB8" + struct.pack("<Q", fake_addr) + b"\xFF\xE0"

    old_prot = c_uint32(0)
    k32.VirtualProtect(c_void_p(fn_addr), 12, 0x40, byref(old_prot))
    ctypes.memmove(fn_addr, hook_bytes, 12)
    k32.FlushInstructionCache(k32.GetCurrentProcess(), c_void_p(fn_addr), 12)

    return fn_addr, orig_12, old_prot.value, _fake_rok


def _unhook_reg_inline(state) -> None:
    fn_addr, orig_12, old_prot_val, _ = state
    k32 = ctypes.windll.kernel32
    dummy = c_uint32(0)
    k32.VirtualProtect(c_void_p(fn_addr), 12, 0x40, byref(dummy))
    ctypes.memmove(fn_addr, orig_12, 12)
    k32.FlushInstructionCache(k32.GetCurrentProcess(), c_void_p(fn_addr), 12)
    k32.VirtualProtect(c_void_p(fn_addr), 12, old_prot_val, byref(dummy))


def _hook_findfile_inline():
    """
    Inline-patch kernel32!FindFirstFileA to log every file-search path during
    DllMain.  Uses temporary self-unhook/re-hook to forward to the real function
    without a trampoline.

    Returns (fn_addr, orig_12, old_prot, callback, log_list).  log_list is
    populated in-place; check it after _unhook_findfile_inline().
    """
    k32 = ctypes.windll.kernel32

    _fn_box:    list = [None]
    _o12_box:   list = [None]
    _op_box:    list = [None]
    _hb_box:    list = [None]
    _log_list:  list = []

    @WINFUNCTYPE(c_void_p, c_void_p, c_void_p)
    def _fake(lpFile, lpData):
        path = ""
        try:
            if lpFile:
                path = ctypes.string_at(lpFile, 520).split(b"\x00")[0].decode(
                    "latin-1", errors="replace")
            _log_list.append(path)
        except Exception:
            pass

        fn  = _fn_box[0]
        o12 = _o12_box[0]
        op  = _op_box[0]
        hb  = _hb_box[0]
        if fn is None or o12 is None:
            return 0xFFFFFFFFFFFFFFFF  # INVALID_HANDLE_VALUE fallback

        dummy = c_uint32(0)
        # Temporarily restore original bytes so we can call the real function.
        k32.VirtualProtect(c_void_p(fn), 12, 0x40, byref(dummy))
        ctypes.memmove(fn, o12, 12)
        k32.FlushInstructionCache(k32.GetCurrentProcess(), c_void_p(fn), 12)
        k32.VirtualProtect(c_void_p(fn), 12, op, byref(dummy))

        k32.FindFirstFileA.restype = c_void_p
        result = k32.FindFirstFileA(lpFile, lpData)

        # Re-apply hook bytes.
        k32.VirtualProtect(c_void_p(fn), 12, 0x40, byref(dummy))
        ctypes.memmove(fn, hb, 12)
        k32.FlushInstructionCache(k32.GetCurrentProcess(), c_void_p(fn), 12)
        k32.VirtualProtect(c_void_p(fn), 12, op, byref(dummy))
        return result

    _iat_live.append(_fake)
    fake_addr = cast(_fake, c_void_p).value
    k32.FindFirstFileA.restype = c_void_p
    fn_addr   = cast(k32.FindFirstFileA, c_void_p).value
    orig_12   = ctypes.string_at(fn_addr, 12)
    hb        = b"\x48\xB8" + struct.pack("<Q", fake_addr) + b"\xFF\xE0"

    old_prot = c_uint32(0)
    k32.VirtualProtect(c_void_p(fn_addr), 12, 0x40, byref(old_prot))
    ctypes.memmove(fn_addr, hb, 12)
    k32.FlushInstructionCache(k32.GetCurrentProcess(), c_void_p(fn_addr), 12)

    _fn_box[0]  = fn_addr
    _o12_box[0] = orig_12
    _op_box[0]  = old_prot.value
    _hb_box[0]  = hb

    return fn_addr, orig_12, old_prot.value, _fake, _log_list


def _unhook_findfile_inline(state) -> list:
    """Restore FindFirstFileA's original bytes.  Returns the accumulated path log."""
    fn_addr, orig_12, old_prot_val, _, log_list = state
    k32   = ctypes.windll.kernel32
    dummy = c_uint32(0)
    k32.VirtualProtect(c_void_p(fn_addr), 12, 0x40, byref(dummy))
    ctypes.memmove(fn_addr, orig_12, 12)
    k32.FlushInstructionCache(k32.GetCurrentProcess(), c_void_p(fn_addr), 12)
    k32.VirtualProtect(c_void_p(fn_addr), 12, old_prot_val, byref(dummy))
    return log_list


def _patch_iat(dll, targets: dict) -> int:
    """
    Generic IAT patcher.  targets maps bytes function-name → replacement pointer (int).
    Walks the plugin DLL's import directory and overwrites matching IAT slots.
    Returns the number of slots patched.
    """
    k32     = ctypes.windll.kernel32
    PAGE_RW = 0x04
    base    = dll._handle
    patched = 0
    try:
        dos = ctypes.string_at(base, 0x40)
        if dos[:2] != b"MZ":
            return 0
        pe_off    = struct.unpack_from("<I", dos, 0x3C)[0]
        pe_hdr    = ctypes.string_at(base + pe_off, 264)
        if pe_hdr[:4] != b"PE\x00\x00":
            return 0
        opt_magic = struct.unpack_from("<H", pe_hdr, 24)[0]
        dd_base   = 24 + (112 if opt_magic == 0x020B else 96)
        imp_rva   = struct.unpack_from("<I", pe_hdr, dd_base + 8)[0]
        if not imp_rva:
            return 0

        desc_off = 0
        while True:
            desc = ctypes.string_at(base + imp_rva + desc_off, 20)
            orig_thunk_rva, _, _, name_rva, first_thunk_rva = struct.unpack_from("<IIIII", desc)
            if not name_rva:
                break
            int_rva = orig_thunk_rva or first_thunk_rva
            thk = 0
            while True:
                try:
                    ov = struct.unpack_from("<Q", ctypes.string_at(base + int_rva + thk, 8))[0]
                except Exception:
                    break
                if not ov:
                    break
                if not (ov >> 63):
                    try:
                        fn = ctypes.string_at(base + ov + 2, 64).split(b"\x00")[0]
                    except Exception:
                        thk += 8
                        continue
                    if fn in targets:
                        iat_va = base + first_thunk_rva + thk
                        old_p  = c_uint32(0)
                        k32.VirtualProtect(c_void_p(iat_va), 8, PAGE_RW, byref(old_p))
                        ctypes.memmove(iat_va, struct.pack("<Q", targets[fn]), 8)
                        k32.VirtualProtect(c_void_p(iat_va), 8, old_p, byref(old_p))
                        patched += 1
                thk += 8
            desc_off += 20
    except Exception as exc:
        _eprint(f"[8bf] _patch_iat failed: {exc}")
    return patched


def _patch_iat_module_filename(dll) -> int:
    """
    Replace the plugin DLL's IAT entries for GetModuleFileNameA/W with stubs
    that return a fake Photoshop.exe path when hModule is NULL.
    Returns the number of IAT entries actually patched (0 = nothing found).
    """
    k32          = ctypes.windll.kernel32
    _FAKE_PATH_A = b"C:\\Program Files\\Adobe\\Adobe Photoshop 2023\\Photoshop.exe"

    _real_a = k32.GetModuleFileNameA
    _real_w = k32.GetModuleFileNameW

    @WINFUNCTYPE(c_uint32, c_void_p, c_void_p, c_uint32)
    def _fake_a(hmod, buf, size):
        _gmfn_hit_count[0] += 1   # proof the IAT hook is actually reached
        if not hmod:
            n = min(size - 1, len(_FAKE_PATH_A))
            ctypes.memmove(buf, _FAKE_PATH_A, n)
            ctypes.memset(buf + n, 0, 1)
            return n
        return _real_a(hmod, buf, size)

    @WINFUNCTYPE(c_uint32, c_void_p, c_void_p, c_uint32)
    def _fake_w(hmod, buf, size):
        _gmfn_hit_count[0] += 1
        if not hmod:
            src = _FAKE_PATH_A.decode("ascii").encode("utf-16-le")
            n   = min(size - 1, len(src) // 2)
            ctypes.memmove(buf, src, n * 2)
            ctypes.memset(buf + n * 2, 0, 2)
            return n
        return _real_w(hmod, buf, size)

    _iat_live.extend([_fake_a, _fake_w])
    return _patch_iat(dll, {
        b"GetModuleFileNameA": cast(_fake_a, c_void_p).value,
        b"GetModuleFileNameW": cast(_fake_w, c_void_p).value,
    })


def _patch_iat_diagnostics(dll, log_fn) -> dict:
    """
    Hook kernel32 + user32 functions in the plugin's IAT for diagnostics.
    Calls through to the real functions so plugin behavior is unchanged.
    Returns {name: hit_count} that is updated live as the plugin runs.
    """
    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    hits: dict = {}

    # ── LoadLibrary ───────────────────────────────────────────────────────────
    def _make_loadlib(real_fn, fname, wide):
        hits[fname] = 0
        @WINFUNCTYPE(c_void_p, c_void_p)
        def _stub(path):
            hits[fname] += 1
            try:
                s = ctypes.wstring_at(path) if wide else ctypes.string_at(path, 256).split(b"\x00")[0].decode("latin-1")
            except Exception:
                s = "?"
            result = real_fn(path)
            log_fn(f"[8bf] IAT {fname}({s!r}) → 0x{result or 0:x}")
            return result
        _iat_live.append(_stub)
        return cast(_stub, c_void_p).value

    # ── GetProcAddress ────────────────────────────────────────────────────────
    _real_gpa = k32.GetProcAddress
    _real_gpa.restype  = c_void_p
    _real_gpa.argtypes = [c_void_p, c_void_p]
    hits["GetProcAddress"] = 0

    @WINFUNCTYPE(c_void_p, c_void_p, c_void_p)
    def _fake_gpa(hmod, name):
        result = _real_gpa(hmod, name)
        hits["GetProcAddress"] += 1
        try:
            if name and name < 0x10000:
                name_s = f"ordinal#{name}"
            else:
                name_s = ctypes.string_at(name, 128).split(b"\x00")[0].decode("latin-1")
            log_fn(f"[8bf] IAT GetProcAddress({name_s!r}) → 0x{result or 0:x}")
        except Exception:
            pass
        return result
    _iat_live.append(_fake_gpa)

    # ── CreateProcess ─────────────────────────────────────────────────────────
    def _make_createproc(real_fn, fname, wide):
        hits[fname] = 0
        @WINFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p, c_void_p,
                     c_int32, c_uint32, c_void_p, c_void_p, c_void_p, c_void_p)
        def _stub(app, cmdline, pa, ta, inherit, flags, env, cwd, si, pi):
            hits[fname] += 1
            try:
                a = (ctypes.wstring_at(app) if wide else ctypes.string_at(app, 256).split(b"\x00")[0].decode("latin-1")) if app else ""
                c = (ctypes.wstring_at(cmdline) if wide else ctypes.string_at(cmdline, 512).split(b"\x00")[0].decode("latin-1")) if cmdline else ""
            except Exception:
                a = c = "?"
            log_fn(f"[8bf] IAT {fname}(app={a!r} cmd={c!r})")
            return real_fn(app, cmdline, pa, ta, inherit, flags, env, cwd, si, pi)
        _iat_live.append(_stub)
        return cast(_stub, c_void_p).value

    # ── Dialog / window creation ──────────────────────────────────────────────
    def _make_dlgbox(real_fn, fname):
        hits[fname] = 0
        @WINFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p, c_void_p, c_void_p)
        def _stub(hInst, tmpl, hParent, dlgProc, initParam):
            hits[fname] += 1
            log_fn(f"[8bf] IAT {fname}(hParent=0x{hParent or 0:x})")
            return real_fn(hInst, tmpl, hParent, dlgProc, initParam)
        _iat_live.append(_stub)
        return cast(_stub, c_void_p).value

    def _make_cwex(real_fn, fname):
        hits[fname] = 0
        @WINFUNCTYPE(c_void_p, c_uint32, c_void_p, c_void_p, c_uint32,
                     c_int32, c_int32, c_int32, c_int32,
                     c_void_p, c_void_p, c_void_p, c_void_p)
        def _stub(exStyle, cls, title, style, x, y, w, h, parent, menu, inst, param):
            hits[fname] += 1
            try:
                cls_s = (ctypes.string_at(cls, 128).split(b"\x00")[0].decode("latin-1")
                         if cls and cls > 0xFFFF else f"#{cls or 0}")
            except Exception:
                cls_s = "?"
            log_fn(f"[8bf] IAT {fname}(cls={cls_s!r} parent=0x{parent or 0:x})")
            return real_fn(exStyle, cls, title, style, x, y, w, h, parent, menu, inst, param)
        _iat_live.append(_stub)
        return cast(_stub, c_void_p).value

    # ── Registry ──────────────────────────────────────────────────────────────
    adv32 = ctypes.windll.advapi32
    _real_rok = adv32.RegOpenKeyExA
    _real_rok.restype  = c_uint32
    _real_rok.argtypes = [c_void_p, c_void_p, c_uint32, c_uint32, c_void_p]
    _real_rqv = adv32.RegQueryValueExA
    _real_rqv.restype  = c_uint32
    _real_rqv.argtypes = [c_void_p, c_void_p, c_void_p, c_void_p, c_void_p, c_void_p]
    _real_rck = adv32.RegCloseKey
    _real_rck.restype  = c_uint32
    _real_rck.argtypes = [c_void_p]
    _real_rsv = adv32.RegSetValueExA
    _real_rsv.restype  = c_uint32
    _real_rsv.argtypes = [c_void_p, c_void_p, c_uint32, c_uint32, c_void_p, c_uint32]

    hits["RegOpenKeyExA"]   = 0
    hits["RegQueryValueExA"] = 0
    hits["RegSetValueExA"]  = 0

    @WINFUNCTYPE(c_uint32, c_void_p, c_void_p, c_uint32, c_uint32, c_void_p)
    def _fake_rok(hKey, subKey, options, access, phkResult):
        hits["RegOpenKeyExA"] += 1
        try:
            k = ctypes.string_at(subKey, 256).split(b"\x00")[0].decode("latin-1") if subKey else ""
        except Exception:
            k = "?"
        r = _real_rok(hKey, subKey, options, access, phkResult)
        log_fn(f"[8bf] RegOpenKeyExA({k!r}) → {r}")
        return r

    @WINFUNCTYPE(c_uint32, c_void_p, c_void_p, c_void_p, c_void_p, c_void_p, c_void_p)
    def _fake_rqv(hKey, valueName, reserved, typeOut, data, sizeInOut):
        hits["RegQueryValueExA"] += 1
        try:
            vn = ctypes.string_at(valueName, 256).split(b"\x00")[0].decode("latin-1") if valueName else ""
        except Exception:
            vn = "?"
        r = _real_rqv(hKey, valueName, reserved, typeOut, data, sizeInOut)
        log_fn(f"[8bf] RegQueryValueExA({vn!r}) → {r}")
        return r

    @WINFUNCTYPE(c_uint32, c_void_p, c_void_p, c_uint32, c_uint32, c_void_p, c_uint32)
    def _fake_rsv(hKey, valueName, reserved, vtype, data, size):
        hits["RegSetValueExA"] += 1
        try:
            vn = ctypes.string_at(valueName, 256).split(b"\x00")[0].decode("latin-1") if valueName else ""
        except Exception:
            vn = "?"
        log_fn(f"[8bf] RegSetValueExA({vn!r})")
        return _real_rsv(hKey, valueName, reserved, vtype, data, size)

    _iat_live.extend([_fake_rok, _fake_rqv, _fake_rsv])

    # ── GetModuleHandleA/W — log calls with module name ──────────────────────
    _real_gmha = k32.GetModuleHandleA
    _real_gmha.restype  = c_void_p
    _real_gmha.argtypes = [c_void_p]
    _real_gmhw = k32.GetModuleHandleW
    _real_gmhw.restype  = c_void_p
    _real_gmhw.argtypes = [c_void_p]
    _fake_ps_handle = k32.GetModuleHandleW(None)   # our own module = stand-in
    hits["GetModuleHandleA"] = 0
    hits["GetModuleHandleW"] = 0

    @WINFUNCTYPE(c_void_p, c_void_p)
    def _fake_gmha(module_name):
        hits["GetModuleHandleA"] += 1
        result = _real_gmha(module_name)
        try:
            name = ctypes.string_at(module_name, 256).split(b"\x00")[0].decode("latin-1") if module_name else ""
        except Exception:
            name = "?"
        if name.lower().startswith("photoshop") and not result:
            result = _fake_ps_handle
        log_fn(f"[8bf] IAT GetModuleHandleA({name!r}) → 0x{result or 0:x}")
        return result

    @WINFUNCTYPE(c_void_p, c_void_p)
    def _fake_gmhw(module_name):
        hits["GetModuleHandleW"] += 1
        result = _real_gmhw(module_name)
        try:
            name = ctypes.wstring_at(module_name, 256) if module_name else ""
        except Exception:
            name = "?"
        if name.lower().startswith("photoshop") and not result:
            result = _fake_ps_handle
        log_fn(f"[8bf] IAT GetModuleHandleW({name!r}) → 0x{result or 0:x}")
        return result

    _iat_live.extend([_fake_gmha, _fake_gmhw])

    # ── ShellExecuteA — log any external app launch ───────────────────────────
    _shell32 = ctypes.windll.shell32
    _real_shex = _shell32.ShellExecuteA
    _real_shex.restype  = c_void_p
    _real_shex.argtypes = [c_void_p, c_void_p, c_void_p, c_void_p, c_void_p, c_int32]
    hits["ShellExecuteA"] = 0

    @WINFUNCTYPE(c_void_p, c_void_p, c_void_p, c_void_p, c_void_p, c_void_p, c_int32)
    def _fake_shex(hwnd, op, file_, params, cwd, show):
        hits["ShellExecuteA"] += 1
        try:
            f = ctypes.string_at(file_, 512).split(b"\x00")[0].decode("latin-1") if file_ else ""
            p = ctypes.string_at(params, 512).split(b"\x00")[0].decode("latin-1") if params else ""
        except Exception:
            f = p = "?"
        log_fn(f"[8bf] IAT ShellExecuteA(file={f!r} params={p!r})")
        return _real_shex(hwnd, op, file_, params, cwd, show)
    _iat_live.append(_fake_shex)

    targets = {
        # kernel32
        b"LoadLibraryA":            _make_loadlib(k32.LoadLibraryA, "LoadLibraryA", False),
        b"LoadLibraryW":            _make_loadlib(k32.LoadLibraryW, "LoadLibraryW", True),
        b"GetProcAddress":          cast(_fake_gpa, c_void_p).value,
        b"CreateProcessA":          _make_createproc(k32.CreateProcessA, "CreateProcessA", False),
        b"CreateProcessW":          _make_createproc(k32.CreateProcessW, "CreateProcessW", True),
        b"GetModuleHandleA":        cast(_fake_gmha, c_void_p).value,
        b"GetModuleHandleW":        cast(_fake_gmhw, c_void_p).value,
        # user32
        b"DialogBoxParamA":         _make_dlgbox(u32.DialogBoxParamA,         "DialogBoxParamA"),
        b"DialogBoxParamW":         _make_dlgbox(u32.DialogBoxParamW,         "DialogBoxParamW"),
        b"DialogBoxIndirectParamA": _make_dlgbox(u32.DialogBoxIndirectParamA, "DialogBoxIndirectParamA"),
        b"DialogBoxIndirectParamW": _make_dlgbox(u32.DialogBoxIndirectParamW, "DialogBoxIndirectParamW"),
        b"CreateWindowExA":         _make_cwex(u32.CreateWindowExA,           "CreateWindowExA"),
        b"CreateWindowExW":         _make_cwex(u32.CreateWindowExW,           "CreateWindowExW"),
        # advapi32
        b"RegOpenKeyExA":   cast(_fake_rok, c_void_p).value,
        b"RegQueryValueExA": cast(_fake_rqv, c_void_p).value,
        b"RegSetValueExA":  cast(_fake_rsv, c_void_p).value,
        # shell32
        b"ShellExecuteA":   cast(_fake_shex, c_void_p).value,
    }
    _patch_iat(dll, targets)
    return hits

# Generic stub vtable for unimplemented PS suites.
# Every slot returns the stub vtable address itself (non-NULL) so that if the
# plugin uses the return value as another handle/suite pointer and calls through
# it again, it hits another no-op stub instead of dereferencing NULL.
# Using c_void_p return so the full 64-bit address lands in RAX.
# _stub_vtable_ref holds the forward reference filled in after _STUB_VTABLE
# is allocated (the function can't reference _STUB_VTABLE before it exists).
_stub_vtable_ref: list = [0]

@WINFUNCTYPE(c_void_p)
def _suite_stub_fn():
    return _stub_vtable_ref[0]

_STUB_VTABLE      = (c_void_p * 64)(*([cast(_suite_stub_fn, c_void_p).value] * 64))
_stub_vtable_ref[0] = addressof(_STUB_VTABLE)   # fill forward reference


def _make_buffer_procs() -> BufferProcs:
    """Implement PS buffer allocation callbacks backed by ctypes buffers."""
    _pool: dict[int, object] = {}

    @WINFUNCTYPE(c_int16, c_int32, POINTER(c_void_p))
    def _alloc(size: int, ptr_out):
        buf  = create_string_buffer(max(size, 1))
        addr = addressof(buf)
        _pool[addr] = buf
        ptr_out[0]  = addr
        return 0

    @WINFUNCTYPE(None, c_void_p)
    def _free(ptr):
        _pool.pop(ptr, None)

    @WINFUNCTYPE(c_void_p, c_void_p, c_uint8)
    def _lock(ptr, _move):
        return ptr

    @WINFUNCTYPE(None, c_void_p)
    def _unlock(_ptr):
        pass

    @WINFUNCTYPE(c_int32)
    def _space():
        return 512 * 1024 * 1024

    _live_refs.extend([_alloc, _free, _lock, _unlock, _space])

    bp = BufferProcs()
    bp.bufferProcsVersion = 2
    bp.numBufferProcs     = 5
    bp.allocateBuffer     = cast(_alloc,  c_void_p).value
    bp.freeBuffer         = cast(_free,   c_void_p).value
    bp.lockBuffer         = cast(_lock,   c_void_p).value
    bp.unlockBuffer       = cast(_unlock, c_void_p).value
    bp.spaceBuffer        = cast(_space,  c_void_p).value
    return bp


def _make_handle_procs() -> HandleProcs:
    """Implement PS Handle callbacks (double-indirect memory handles)."""
    _handles: dict[int, tuple] = {}   # handle_addr → (data_buf, ptr_cell)

    @WINFUNCTYPE(c_int16, c_int32, POINTER(c_void_p))
    def _new(size, handle_out):
        buf  = create_string_buffer(max(size, 1))
        cell = (c_void_p * 1)(addressof(buf))   # the handle IS &cell
        addr = addressof(cell)
        _handles[addr] = (buf, cell)
        if handle_out:
            handle_out[0] = addr
        return 0  # noErr

    @WINFUNCTYPE(None, c_void_p)
    def _dispose(handle):
        _handles.pop(handle, None)

    @WINFUNCTYPE(c_int32, c_void_p)
    def _get_size(handle):
        entry = _handles.get(handle)
        return len(entry[0]) if entry else 0

    @WINFUNCTYPE(c_int16, c_void_p, c_int32)
    def _set_size(handle, new_size):
        entry = _handles.get(handle)
        if entry:
            buf = create_string_buffer(max(new_size, 1))
            entry[1][0] = addressof(buf)
            _handles[handle] = (buf, entry[1])
        return 0

    @WINFUNCTYPE(c_void_p, c_void_p, c_uint8)
    def _lock(handle, _move_high):
        entry = _handles.get(handle)
        return addressof(entry[0]) if entry else 0

    @WINFUNCTYPE(None, c_void_p)
    def _unlock(_handle):
        pass

    @WINFUNCTYPE(None, c_int32)
    def _recover(_size):
        pass

    @WINFUNCTYPE(None, c_void_p)
    def _dispose_reg(handle):
        _handles.pop(handle, None)

    _live_refs.extend([_new, _dispose, _get_size, _set_size,
                       _lock, _unlock, _recover, _dispose_reg])

    hp = HandleProcs()
    hp.handleProcsVersion       = 1
    hp.numHandleProcs           = 8
    hp.newProc                  = cast(_new,         c_void_p).value
    hp.disposeProc              = cast(_dispose,     c_void_p).value
    hp.getSizeProc              = cast(_get_size,    c_void_p).value
    hp.setSizeProc              = cast(_set_size,    c_void_p).value
    hp.lockProc                 = cast(_lock,        c_void_p).value
    hp.unlockProc               = cast(_unlock,      c_void_p).value
    hp.recoverSpaceProc         = cast(_recover,     c_void_p).value
    hp.disposeRegularHandleProc = cast(_dispose_reg, c_void_p).value
    return hp


def _make_spbasic() -> SPBasicSuite:
    """
    Build a minimal SPBasicSuite for the host.

    AcquireSuite always returns non-zero (suite not found) so the plugin falls
    back to its own code paths.  AllocateBlock / FreeBlock are real allocators
    so plugins that call them directly (without acquiring a suite) don't crash.
    """
    _pool: dict[int, object] = {}

    @WINFUNCTYPE(c_int32, c_void_p, c_int32, POINTER(c_void_p))
    def _acquire(name, version, suite_out):
        try:
            name_str = ctypes.string_at(name).decode('latin-1', errors='replace') if name else '(null)'
        except Exception:
            name_str = f'<err:{name}>'
        entry = f"{name_str!r}@v{version}"
        _suite_log.append(entry)
        _eprint(f"[8bf] AcquireSuite({entry})")
        if suite_out:
            suite_out[0] = addressof(_STUB_VTABLE)
        return 0   # kSPNoError

    @WINFUNCTYPE(c_int32, c_void_p, c_int32)
    def _release(name, version):
        return 0

    @WINFUNCTYPE(c_int32, c_void_p, c_void_p)
    def _is_equal(t1, t2):
        return 0   # SPBoolean FALSE

    @WINFUNCTYPE(c_int32, c_int32, POINTER(c_void_p))
    def _alloc(size, block_out):
        buf  = create_string_buffer(max(size, 1))
        addr = addressof(buf)
        _pool[addr] = buf
        if block_out:
            block_out[0] = addr
        return 0

    @WINFUNCTYPE(c_int32, c_void_p)
    def _free(block):
        _pool.pop(block, None)
        return 0

    @WINFUNCTYPE(c_int32, c_void_p, c_int32, POINTER(c_void_p))
    def _realloc(block, new_size, out):
        buf  = create_string_buffer(max(new_size, 1))
        addr = addressof(buf)
        _pool.pop(block, None)
        _pool[addr] = buf
        if out:
            out[0] = addr
        return 0

    @WINFUNCTYPE(None)
    def _undef():
        pass

    _live_refs.extend([_acquire, _release, _is_equal, _alloc, _free, _realloc, _undef])

    sp = SPBasicSuite()
    sp.AcquireSuite    = cast(_acquire,  c_void_p).value
    sp.ReleaseSuite    = cast(_release,  c_void_p).value
    sp.IsEqual         = cast(_is_equal, c_void_p).value
    sp.AllocateBlock   = cast(_alloc,    c_void_p).value
    sp.FreeBlock       = cast(_free,     c_void_p).value
    sp.ReallocateBlock = cast(_realloc,  c_void_p).value
    sp.Undefined       = cast(_undef,    c_void_p).value
    return sp


def _make_descriptor_params():
    """
    Build a DescriptorParameters block that tells modern .8bf plugins to show
    their settings dialog.  We stub the read/write proc tables:
      - openRead  returns NULL  → plugin has no saved params, uses defaults
      - openWrite returns dummy token; closeWrite discards the written handle
      - all other procs return 0 (noErr / FALSE) — never called in practice
        because openRead returned NULL (nothing to iterate) and we don't
        persist the written descriptor across calls.
    Returns an opaque holder whose .params field is the _DescriptorParameters;
    keep the holder alive for the duration of the plugin call.
    """
    @WINFUNCTYPE(c_void_p, c_void_p, c_void_p)
    def _open_read(descriptor, key_array):
        _eprint(f"[8bf] openRead(desc=0x{descriptor or 0:x}) → token=1")
        return 1   # non-NULL empty token; getKey will immediately return FALSE

    @WINFUNCTYPE(c_int16, c_void_p)
    def _close_read(token):
        _eprint(f"[8bf] closeRead(token={token})")
        return 0   # noErr

    @WINFUNCTYPE(c_int16, c_void_p, c_void_p, c_void_p, c_void_p)
    def _get_key(token, key_out, type_out, flags_out):
        _eprint(f"[8bf] getKey(token={token}) → FALSE")
        return 0   # FALSE = no more keys (empty descriptor)

    @WINFUNCTYPE(c_void_p)
    def _open_write():
        _eprint("[8bf] openWrite() → token=2")
        return 2   # dummy non-NULL write token

    @WINFUNCTYPE(c_int16, c_void_p, POINTER(c_void_p))
    def _close_write(write_token, written_out):
        _eprint(f"[8bf] closeWrite(token={write_token})")
        if written_out:
            written_out[0] = 0   # don't persist the written descriptor
        return 0   # noErr

    @WINFUNCTYPE(c_int16)
    def _noop():
        return 0

    _live_refs.extend([_open_read, _close_read, _get_key,
                       _open_write, _close_write, _noop])

    noop_val = cast(_noop, c_void_p).value

    rp = _ReadDescProcs()
    rp.readDescriptorProcsVersion = 1
    rp.numReadDescriptorProcs     = 18
    rp.openReadDescriptorProc     = cast(_open_read,  c_void_p).value
    rp.closeReadDescriptorProc    = cast(_close_read, c_void_p).value
    rp.getKeyProc                 = cast(_get_key,    c_void_p).value
    for _f in ("getIntegerProc", "getFloatProc", "getUnitFloatProc",
               "getBooleanProc", "getStringProc", "getAliasProc",
               "getEnumeratedProc", "getClassProc", "getSimpleReferenceProc",
               "getObjectProc", "getCountProc", "getStringProc2",
               "getPinnedIntegerProc", "getPinnedFloatProc", "getPinnedUnitFloatProc"):
        setattr(rp, _f, noop_val)

    wp = _WriteDescProcs()
    wp.writeDescriptorProcsVersion = 1
    wp.numWriteDescriptorProcs     = 16
    wp.openWriteDescriptorProc     = cast(_open_write,  c_void_p).value
    wp.closeWriteDescriptorProc    = cast(_close_write, c_void_p).value
    for _f in ("putIntegerProc", "putFloatProc", "putUnitFloatProc",
               "putBooleanProc", "putStringProc", "putAliasProc",
               "putEnumeratedProc", "putClassProc", "putSimpleReferenceProc",
               "putObjectProc", "putCountProc", "putStringProc2",
               "putScopedClassProc", "putScopedObjectProc"):
        setattr(wp, _f, noop_val)

    # descriptor = NULL signals "no recorded/scripted parameters" — the plugin
    # should fall back to its built-in defaults and show its settings dialog.
    # A non-NULL descriptor could be interpreted as "replay silently from script"
    # by some plugins regardless of the playInfo field.
    dp = _DescriptorParameters()
    dp.descriptorID = 0
    dp.playInfo     = 1   # plugInDialogRequired = 1 → always show dialog
    dp.recordInfo   = 0
    dp.descriptor   = 0   # NULL = no saved params
    dp.readProcs    = addressof(rp)
    dp.writeProcs   = addressof(wp)

    class _Holder:
        pass
    h = _Holder()
    h.params = dp
    h.rp = rp
    h.wp = wp
    _live_refs.extend([rp, wp, dp])
    return h


def _dump_iat_imports(dll) -> list[str]:
    """Return sorted list of 'dllname!funcname' for every static IAT import."""
    base   = dll._handle
    result = []
    try:
        dos = ctypes.string_at(base, 0x40)
        if dos[:2] != b"MZ":
            return ["NOT_PE"]
        pe_off    = struct.unpack_from("<I", dos, 0x3C)[0]
        pe_hdr    = ctypes.string_at(base + pe_off, 264)
        opt_magic = struct.unpack_from("<H", pe_hdr, 24)[0]
        dd_base   = 24 + (112 if opt_magic == 0x020B else 96)
        imp_rva   = struct.unpack_from("<I", pe_hdr, dd_base + 8)[0]
        if not imp_rva:
            return ["NO_IMPORTS"]
        desc_off = 0
        while True:
            desc = ctypes.string_at(base + imp_rva + desc_off, 20)
            orig_rva, _, _, name_rva, first_rva = struct.unpack_from("<IIIII", desc)
            if not name_rva:
                break
            dname = ctypes.string_at(base + name_rva, 64).split(b"\x00")[0].decode("latin-1")
            int_rva = orig_rva or first_rva
            thk = 0
            while True:
                try:
                    ov = struct.unpack_from("<Q", ctypes.string_at(base + int_rva + thk, 8))[0]
                except Exception:
                    break
                if not ov:
                    break
                if not (ov >> 63):
                    try:
                        fn = ctypes.string_at(base + ov + 2, 128).split(b"\x00")[0].decode("latin-1")
                        result.append(f"{dname}!{fn}")
                    except Exception:
                        pass
                else:
                    result.append(f"{dname}!ordinal#{ov & 0xFFFF}")
                thk += 8
            desc_off += 20
    except Exception as exc:
        result.append(f"ERROR:{exc}")
    return sorted(result)


# ── Public API ────────────────────────────────────────────────────────────────

def run_plugin_about(plugin_info: PluginInfo, parent_hwnd: int = 0):
    """Show the plugin's About dialog. No-op for 32-bit plugins."""
    if not plugin_info.is_64bit:
        return
    dll, pm = _load(plugin_info)
    if pm is None:
        return

    _live_refs.clear()

    pd           = PlatformData()
    pd.hwnd      = parent_hwnd
    pd.filterCase = 0
    pd.isMac     = 0

    sp = _make_spbasic()
    _live_refs.append(sp)

    ar               = AboutRecord()
    ar.serialNumber  = 0
    ar.testAbort     = 0
    ar.platformData  = addressof(pd)
    ar.sSPBasic      = addressof(sp)
    ar.plugInRef     = 0

    data_val = c_int64(0)
    result   = c_int16(noErr)
    pm(SEL_ABOUT, addressof(ar), byref(data_val), byref(result))


def run_plugin_filter(
        plugin_info: PluginInfo,
        image,                     # PIL.Image.Image
        parent_hwnd: int = 0,
        progress_cb=None):         # optional callable(done, total)
    """
    Apply a .8bf filter to *image*; return the resulting PIL Image.
    Returns None if the user cancelled or an error occurred.
    Raises RuntimeError for structural failures (32-bit, missing export, etc.).
    """
    if not plugin_info.is_64bit:
        raise RuntimeError(
            f"'{plugin_info.display_name}' is a 32-bit plugin — "
            "cannot load into a 64-bit process. "
            "A 32-bit bridge is planned for a future release.")

    _live_refs.clear()

    # Open log immediately so every subsequent crash is captured.
    _log_path = str(Path(__file__).parent / "data" / "plugin.log")
    _log_file = open(_log_path, "w", encoding="utf-8")
    _log_file_handle[0] = _log_file

    def _log(msg: str):
        _eprint(msg)

    _log(f"[8bf] host_version=3  file={__file__}")
    _log(f"[8bf] image size={image.size}  mode={image.mode}")

    # ── Last-attempt dialog fix: delete plugin's "Last Used" saved state so its
    # DllMain can't cache settings, then force a fresh DLL reload so DllMain
    # actually re-runs. Without this, DllMain only ran once (at startup) and
    # the plugin may have cached "use defaults silently" from saved registry data.
    try:
        import winreg as _wr
        _hk = _wr.OpenKey(_wr.HKEY_CURRENT_USER,
                          r"Software\Redfield\Perfectum2",
                          0, _wr.KEY_SET_VALUE)
        _wr.DeleteValue(_hk, "Last Used")
        _wr.CloseKey(_hk)
        _log("[8bf] deleted HKCU\\Software\\Redfield\\Perfectum2\\Last Used")
    except FileNotFoundError:
        _log("[8bf] Last Used value already absent")
    except Exception as _rex:
        _log(f"[8bf] registry delete: {_rex}")
    # Force a fresh DLL load so DllMain re-runs with the deleted settings.
    if plugin_info._dll is not None:
        _log("[8bf] releasing cached DLL for fresh reload")
        plugin_info._dll = None

    try:
        _dll, pm = _load(plugin_info)
    except Exception as _load_exc:
        import traceback as _tb
        _log(f"[8bf] _load FAILED: {_load_exc}")
        _log(_tb.format_exc())
        raise
    if pm is None:
        raise RuntimeError(
            f"Could not locate PluginMain in '{plugin_info.path}'")
    _log(f"[8bf] plugin loaded: {plugin_info.path}")
    _imports = _dump_iat_imports(plugin_info._dll)
    _log(f"[8bf] static imports: {_imports}")

    # ── COM initialization (needed by plugins that use DirectX / DirectML) ──────
    _ole32 = ctypes.windll.ole32
    _ole32.CoInitializeEx.restype  = c_int32
    _ole32.CoInitializeEx.argtypes = [c_void_p, c_uint32]
    _coinit_hr = _ole32.CoInitializeEx(None, 2)   # COINIT_APARTMENTTHREADED = 2

    # ── Add every subdirectory of the plugin folder to DLL search path ────────
    _plug_root = Path(plugin_info.path).parent
    for _sub in [_plug_root, _plug_root.parent, *_plug_root.rglob("*")]:
        if _sub.is_dir():
            try:
                _dll_dir_cookies.append(os.add_dll_directory(str(_sub)))
            except Exception:
                pass

    # ── Prepare pixel data ────────────────────────────────────────────────────
    from PIL import Image as _PIL
    orig_mode = image.mode
    rgb       = image.convert("RGB")
    w, h      = rgb.size
    _log(f"[8bf] image rgb size: w={w} h={h}  raw_bytes={w*h*3}")
    in_raw    = rgb.tobytes()
    _log(f"[8bf] tobytes done  len={len(in_raw)}")
    # PS SDK norm: inData == outData (single buffer, in-place processing).
    # Separate in/out buffers cause older plugins to write to inData while
    # outData stays zero, producing a black result.
    work_buf  = create_string_buffer(in_raw, len(in_raw))
    _log(f"[8bf] work_buf created")
    row_bytes = w * 3

    # ── Build proc tables ─────────────────────────────────────────────────────
    bp = _make_buffer_procs()
    hp = _make_handle_procs()
    sp = _make_spbasic()
    _live_refs.extend([bp, hp, sp])

    @WINFUNCTYPE(c_int16)
    def _test_abort():
        return 0

    @WINFUNCTYPE(None, c_int32, c_int32)
    def _progress(done, total):
        if progress_cb:
            progress_cb(done, total)

    @WINFUNCTYPE(c_int16)
    def _advance():
        return 0

    # Signature: (PSPixelMap*, PSPixelRgn*, int32 dstRow, int32 dstCol, void* platData)
    @WINFUNCTYPE(c_int16, c_void_p, c_void_p, c_int32, c_int32, c_void_p)
    def _display_pixels(src_map, src_rgn, dst_row, dst_col, platform_data):
        return noErr

    _live_refs.extend([_test_abort, _progress, _advance, _display_pixels])

    # ── Platform data (HWND etc.) ─────────────────────────────────────────────
    # Use the sentinel window (class="Photoshop") as the plugin's parent HWND.
    # Plugins commonly verify pd.hwnd via GetClassNameA; a Qt/other class name
    # causes them to skip their dialog.  The real ThumbsAI HWND is stored
    # separately for message pumping if needed.
    _sentinel = _ensure_ps_sentinel()
    pd           = PlatformData()
    pd.hwnd      = _sentinel or parent_hwnd
    pd.filterCase = 1   # filterCaseFlatImageNoSelection = 1 (flat RGB, no transparency/mask)
    pd.isMac     = 0
    _live_refs.append(pd)

    # ── Build FilterRecord (corrected CS5 SDK layout) ─────────────────────────
    fr = FilterRecord()
    fr.serialNumber   = 0
    fr.abortProc      = cast(_test_abort, c_void_p).value
    fr.progressProc   = cast(_progress,   c_void_p).value
    fr.parameters     = 0     # plugin allocates its own param Handle if needed

    fr.imageSize_v    = h
    fr.imageSize_h    = w
    fr.planes         = 3

    fr.filterRect_top    = 0;  fr.filterRect_left  = 0
    fr.filterRect_bottom = h;  fr.filterRect_right = w

    fr.background_r = fr.background_g = fr.background_b = 0
    fr.foreground_r = fr.foreground_g = fr.foreground_b = 0

    fr.maxSpace    = 512 * 1024 * 1024
    fr.bufferSpace = 512 * 1024 * 1024

    fr.inRect_top  = 0;  fr.inRect_left  = 0
    fr.inRect_bottom = h; fr.inRect_right = w
    fr.inLoPlane   = 0;  fr.inHiPlane   = 2

    fr.outRect_top = 0;  fr.outRect_left = 0
    fr.outRect_bottom = h; fr.outRect_right = w
    fr.outLoPlane  = 0;  fr.outHiPlane  = 2

    fr.maskRect_top = fr.maskRect_left = fr.maskRect_bottom = fr.maskRect_right = 0

    fr.inData      = cast(work_buf, c_void_p).value
    fr.inRowBytes  = row_bytes
    fr.outData     = cast(work_buf, c_void_p).value   # same buffer — PS SDK norm
    fr.outRowBytes = row_bytes

    fr.isFloating = 0;  fr.haveMask = 0;  fr.autoMask = 0
    fr.maskData   = 0;  fr.maskRowBytes = 0

    fr.backColor[0] = 255;  fr.backColor[1] = 255   # white in device space
    fr.backColor[2] = 255;  fr.backColor[3] = 0
    fr.foreColor[0] = 0;    fr.foreColor[1] = 0     # black
    fr.foreColor[2] = 0;    fr.foreColor[3] = 0

    fr.hostSig  = int.from_bytes(b'8BIM', 'big')   # identify as PS-compatible host
    fr.hostProc = 0

    fr.imageMode  = MODE_RGB
    fr.imageHRes  = 72 << 16   # Fixed 16.16: 72 DPI
    fr.imageVRes  = 72 << 16
    fr.floatCoord_v = 0;  fr.floatCoord_h = 0
    fr.wholeSize_v  = h;  fr.wholeSize_h  = w

    fr.monitor_gammaTable = 0
    fr.monitor_gamma      = int(2.2 * 65536)

    # processEvent: plugin calls this to let the host process platform events.
    # A non-NULL stub signals "I am a proper event-driven host"; some plugins
    # skip their settings dialog when this is NULL.
    @WINFUNCTYPE(c_int16, c_void_p)
    def _process_event(msg_ptr):
        if msg_ptr:
            try:
                ctypes.windll.user32.TranslateMessage(msg_ptr)
                ctypes.windll.user32.DispatchMessageA(msg_ptr)
            except Exception:
                pass
        return 0  # noErr

    _live_refs.append(_process_event)

    fr.platformData   = addressof(pd)
    fr.bufferProcs    = addressof(bp)
    fr.resourceProcs  = 0
    fr.processEvent   = cast(_process_event, c_void_p).value
    fr.displayPixels  = cast(_display_pixels, c_void_p).value
    fr.handleProcs    = addressof(hp)
    fr.colorServices  = 0
    fr.advanceState   = cast(_advance, c_void_p).value
    fr.propertyProcs  = 0

    _desc_holder = _make_descriptor_params()
    _live_refs.append(_desc_holder)

    fr.version                   = 4   # kCurrentFilterProcsVersion (CS5/CS6)
    fr.imageServicesProcs        = 0
    fr.descriptorParameters      = addressof(_desc_holder.params)
    fr.errorString               = 0
    fr.channelPortProcs          = 0
    fr.documentInfo              = 0

    fr.supportsDummyChannels     = 0
    fr.supportsAlternateLayouts  = 0
    fr.wantLayout                = 0
    fr.filterCaseInfoCount       = 0
    fr.filterCaseInfo            = 0
    fr.dummyPlaneValue           = 0
    fr.premiereHook              = 0
    fr.advanceState2             = cast(_advance, c_void_p).value
    fr.supportsAbsolute          = 0
    fr.wantsAbsolute             = 0
    fr.getPropertyObsolete       = 0

    fr.cannotUndo       = 0
    fr.noAbort          = 0
    fr.wantsMaskAsBitmap= 0

    fr.inPreDummyPlanes  = 0;  fr.inPostDummyPlanes  = 0
    fr.outPreDummyPlanes = 0;  fr.outPostDummyPlanes = 0

    fr.inputPadding  = 0x00010000   # Fixed 1.0 = edge-extend
    fr.outputPadding = 0x00010000
    fr.maskPadding   = 0x00010000
    fr.padBytes         = 0
    fr.isFirstAlternate = 0
    fr.inputRate        = 0x00010000   # Fixed 1.0
    fr.maskRate         = 0x00010000   # Fixed 1.0

    fr.sSPBasic  = addressof(sp)
    fr.plugInRef = 0

    fr.transparentIndex   = 0
    fr.flattenedImageData = 0
    fr.iCCprofileSize     = 0
    fr.iCCprofileData     = 0
    fr.canUseICCProfiles  = 0
    fr.inDataHandling     = 0
    fr.outDataHandling    = 0
    fr.maskDataHandling   = 0

    # Layer-plane counts: flat RGB has no layer/mask planes; all image data is
    # in the 3 non-layer planes.  inColumnBytes=3 (RGB triplet), inPlaneBytes=1.
    # Some plugins (e.g. Redfield) check inNonLayerPlanes and skip their dialog
    # or processing entirely when it is 0 (no planes).
    fr.inLayerPlanes         = 0
    fr.inTransparencyMask    = 0
    fr.inLayerMasks          = 0
    fr.inInvertedLayerMasks  = 0
    fr.inNonLayerPlanes      = 3
    fr.outLayerPlanes        = 0
    fr.outTransparencyMask   = 0
    fr.outLayerMasks         = 0
    fr.outInvertedLayerMasks = 0
    fr.outNonLayerPlanes     = 3
    fr.inColumnBytes         = 3
    fr.inPlaneBytes          = 1
    fr.outColumnBytes        = 3
    fr.outPlaneBytes         = 1

    data_val = c_int64(0)
    result   = c_int16(noErr)
    fr_ptr   = addressof(fr)

    _suite_log.clear()

    # ── Invoke selectors ──────────────────────────────────────────────────────
    def _call(sel, name):
        _log(f"[8bf] → SEL_{name}")
        try:
            pm(sel, fr_ptr, byref(data_val), byref(result))
            _log(f"[8bf] ← SEL_{name} result={result.value}")
        except Exception as exc:
            try:
                sp_val = fr.sSPBasic or 0
                diag = (f"sSPBasic_layout_offset={FilterRecord.sSPBasic.offset} "
                        f"fr.sSPBasic=0x{sp_val:016x} "
                        f"suites_acquired={_suite_log}")
            except Exception as inner:
                diag = f"(diag failed: {inner})"
            msg = f"SEL_{name} crashed: {exc}\n[diag: {diag}]\n[log: {_log_path}]"
            _log(f"[8bf] ✗ {msg}")
            raise RuntimeError(msg) from None

    try:
        # SEL_PARAMETERS shows the plugin's settings UI.
        # The plugin blocks here until the user clicks OK or Cancel.
        _gmfn_hit_count[0] = 0   # reset before this run
        _iat_count = _patch_iat_module_filename(_dll)
        _diag_hits = _patch_iat_diagnostics(_dll, _log)

        # ── WH_CBT hook: fires on ANY window creation in this thread,
        #    regardless of IAT patching or cached function pointers ────────────
        _u32  = ctypes.windll.user32
        _k32b = ctypes.windll.kernel32
        _CBT_HOOKPROC = WINFUNCTYPE(c_void_p, c_int32, c_void_p, c_void_p)
        _cbt_events: list = []

        @_CBT_HOOKPROC
        def _cbt_proc(nCode, wParam, lParam):
            if nCode >= 0:
                try:
                    _cb  = ctypes.create_string_buffer(64)
                    _tb  = ctypes.create_string_buffer(128)
                    _u32.GetClassNameA(wParam, _cb, 64)
                    _u32.GetWindowTextA(wParam, _tb, 128)
                    _cls = _cb.value.decode("latin-1", errors="replace")
                    _ttl = _tb.value.decode("latin-1", errors="replace")
                except Exception:
                    _cls = _ttl = "?"
                _cbt_events.append(
                    f"nCode={nCode} wParam=0x{wParam or 0:x} cls={_cls!r} ttl={_ttl!r}")
            return _u32.CallNextHookEx(None, nCode, wParam, lParam)

        _live_refs.append(_cbt_proc)

        # Position sentinel at screen centre so plugins that compute dialog
        # position via GetWindowRect(pd.hwnd) land on visible coordinates.
        _sm_w = _u32.GetSystemMetrics(0)   # SM_CXSCREEN
        _sm_h = _u32.GetSystemMetrics(1)   # SM_CYSCREEN
        _sn_w, _sn_h = 800, 600
        _u32.SetWindowPos(c_void_p(pd.hwnd), 0,
                          (_sm_w - _sn_w) // 2, (_sm_h - _sn_h) // 2,
                          _sn_w, _sn_h, 0x0010)  # SWP_NOACTIVATE
        _u32.ShowWindow(c_void_p(pd.hwnd), 4)   # SW_SHOWNOACTIVATE

        _log(f"[8bf] plugin={plugin_info.path}")
        _log(f"[8bf] ps_sentinel=0x{_ps_sentinel_hwnd[0]:x}  pd_hwnd=0x{pd.hwnd:x}  "
             f"descriptorParameters @ 0x{fr.descriptorParameters:016x}  "
             f"playInfo={_desc_holder.params.playInfo}  parent_hwnd=0x{parent_hwnd:x}  "
             f"processEvent=0x{fr.processEvent:016x}  iat_entries_patched={_iat_count}")

        # Apply inline kernel32 hook around SEL_PARAMETERS so even cached
        # function pointers in the plugin resolve through our fake.
        _gmfn_hit_count[0] = 0
        _gmfn_state = _hook_gmfn_inline()

        # Block advapi32!RegOpenKeyExA for Redfield-specific keys so the plugin
        # cannot find its saved "Last Used" settings.  Without saved settings it
        # falls through to its first-run dialog.  The W variant is not hooked and
        # is used as a pass-through for all non-Redfield keys.
        _reg_state = _hook_reg_inline()

        # WH_CBT hook is thread-local; SEL_PARAMETERS runs on a dedicated thread
        # so DialogBoxParam finds an initialised message queue and a running pump.
        _params_done = threading.Event()
        _params_exc: list = [None]
        _thread_hwnd_ref: list = [None]   # set by _params_thread once window is created

        def _params_thread():
            # Init Win32 message queue for this thread.
            _tmsg = ctypes.wintypes.MSG()
            _u32.PeekMessageA(byref(_tmsg), None, 0, 0, 0)  # PM_NOREMOVE

            # Create a Photoshop-class window on THIS thread so pd.hwnd
            # belongs to the same thread as the plugin call.  Plugins that
            # verify GetWindowThreadProcessId(pd.hwnd)==GetCurrentThreadId()
            # before opening their dialog will see a match and proceed.
            # The "Photoshop" class is already registered by _ensure_ps_sentinel.
            _sm_w  = _u32.GetSystemMetrics(0)   # SM_CXSCREEN
            _sm_h  = _u32.GetSystemMetrics(1)   # SM_CYSCREEN
            _sn_w, _sn_h = 800, 600
            _cls_a = ctypes.create_string_buffer(b"Photoshop")
            _ttl_a = ctypes.create_string_buffer(b"Adobe Photoshop")
            _u32.CreateWindowExA.restype  = c_void_p
            _u32.CreateWindowExA.argtypes = [
                c_uint32, c_void_p, c_void_p, c_uint32,
                c_int32, c_int32, c_int32, c_int32,
                c_void_p, c_void_p, c_void_p, c_void_p,
            ]
            _thread_hwnd = _u32.CreateWindowExA(
                0,
                cast(_cls_a, c_void_p).value,
                cast(_ttl_a, c_void_p).value,
                0x80000000,              # WS_POPUP
                (_sm_w - _sn_w) // 2,
                (_sm_h - _sn_h) // 2,
                _sn_w, _sn_h,
                0, 0,
                ctypes.windll.kernel32.GetModuleHandleW(None),
                0,
            )
            _log(f"[8bf] params_thread_hwnd=0x{_thread_hwnd or 0:x}")
            _thread_hwnd_ref[0] = _thread_hwnd
            if _thread_hwnd:
                _u32.ShowWindow(_thread_hwnd, 4)   # SW_SHOWNOACTIVATE
                pd.hwnd = _thread_hwnd             # plugin reads this from fr.platformData

            # WH_CBT hook is thread-local — must be installed on this thread.
            _local_hook = _u32.SetWindowsHookExA(
                5, _cbt_proc, None, _k32b.GetCurrentThreadId())
            _log(f"[8bf] params_thread cbt_hook=0x{_local_hook or 0:x}")
            try:
                _call(SEL_PARAMETERS, "PARAMETERS")

                # Some plugins show a modeless/child dialog and return from
                # SEL_PARAMETERS immediately — the dialog lives as a child of
                # _thread_hwnd on a worker thread.  If we destroy _thread_hwnd
                # right away we kill the dialog before the user sees it.
                # Enumerate children and pump messages until they all close.
                if _thread_hwnd:
                    _child_wins_here: list = []
                    _CHILD_PROC_T = WINFUNCTYPE(c_bool, c_void_p, c_void_p)
                    @_CHILD_PROC_T
                    def _child_ecb_t(hwnd, _lp):
                        _child_wins_here.append(hwnd)
                        return True
                    _u32.EnumChildWindows(_thread_hwnd, _child_ecb_t, 0)

                    if _child_wins_here:
                        for _cw in _child_wins_here:
                            try:
                                _cb2 = ctypes.create_string_buffer(64)
                                _u32.GetClassNameA(_cw, _cb2, 64)
                                _log(f"[8bf] child dialog: hwnd=0x{_cw:x} "
                                     f"cls={_cb2.value.decode('latin-1')!r}")
                            except Exception:
                                _log(f"[8bf] child dialog: hwnd=0x{_cw:x}")
                            _u32.ShowWindow(_cw, 9)    # SW_RESTORE
                            _u32.SetForegroundWindow(_cw)
                            _u32.BringWindowToTop(_cw)
                        # Pump messages until all children are gone
                        _wp2 = ctypes.wintypes.MSG()
                        while any(_u32.IsWindow(h) for h in _child_wins_here):
                            r2 = _u32.PeekMessageA(byref(_wp2), None, 0, 0, 1)
                            if r2 > 0:
                                _u32.TranslateMessage(byref(_wp2))
                                _u32.DispatchMessageA(byref(_wp2))
                            else:
                                _params_done.wait(0.01)
                                if _params_done.is_set():
                                    break

            except Exception as _e:
                _params_exc[0] = _e
            finally:
                if _thread_hwnd:
                    _u32.DestroyWindow(_thread_hwnd)
                    pd.hwnd = _sentinel or parent_hwnd   # restore for SEL_START/FINISH
                if _local_hook:
                    _u32.UnhookWindowsHookEx(_local_hook)
                _params_done.set()

        try:
            # Self-test: call GetModuleFileNameA from Python while hook is live.
            # If the returned path is the fake PS path, the hook is working.
            _tbuf = ctypes.create_string_buffer(512)
            ctypes.windll.kernel32.GetModuleFileNameA(None, _tbuf, 512)
            _hook_selftest = _tbuf.value.decode("latin-1", errors="replace")
            _gmfn_hit_count[0] = 0   # reset after self-test so plugin hits count cleanly

            # Monitor for plugin dialog windows on ANY thread (plugins sometimes
            # create their dialog on a spawned thread our CBT hook misses).
            _pid = _k32b.GetCurrentProcessId()
            _found_plugin_wins: list = []

            def _poll_new_windows():
                _WNDENUMPROC = WINFUNCTYPE(c_bool, c_void_p, c_void_p)
                _seen: set = set()
                _wpid = ctypes.wintypes.DWORD(0)
                _seen_children: set = set()

                def _ecb(hwnd, _):
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, byref(_wpid))
                    if _wpid.value == _pid:
                        _seen.add(hwnd)
                    return True

                def _cecb(hwnd, _):
                    _seen_children.add(hwnd)
                    return True

                _cb  = _WNDENUMPROC(_ecb)
                _ccb = _WNDENUMPROC(_cecb)
                ctypes.windll.user32.EnumWindows(_cb, None)
                _initial = frozenset(_seen)
                while not _params_done.is_set():
                    _seen.clear()
                    ctypes.windll.user32.EnumWindows(_cb, None)
                    for _h in _seen - _initial:
                        if _h not in _found_plugin_wins:
                            _found_plugin_wins.append(_h)
                            _log(f"[8bf] plugin_dialog hwnd=0x{_h:x}")
                            try:
                                _u32.ShowWindow(_h, 9)   # SW_RESTORE
                                _u32.SetForegroundWindow(_h)
                                _u32.BringWindowToTop(_h)
                            except Exception:
                                pass
                    # Also check for child windows of the params thread window
                    # (WS_CHILD dialogs are invisible to EnumWindows)
                    _tw = _thread_hwnd_ref[0]
                    if _tw:
                        _seen_children.clear()
                        ctypes.windll.user32.EnumChildWindows(_tw, _ccb, None)
                        for _ch in _seen_children:
                            if _ch not in _found_plugin_wins:
                                _found_plugin_wins.append(_ch)
                                try:
                                    _cb2 = ctypes.create_string_buffer(64)
                                    ctypes.windll.user32.GetClassNameA(_ch, _cb2, 64)
                                    _log(f"[8bf] child_dialog hwnd=0x{_ch:x} "
                                         f"cls={_cb2.value.decode('latin-1')!r}")
                                except Exception:
                                    _log(f"[8bf] child_dialog hwnd=0x{_ch:x}")
                                try:
                                    _u32.ShowWindow(_ch, 9)   # SW_RESTORE
                                    _u32.SetForegroundWindow(_ch)
                                    _u32.BringWindowToTop(_ch)
                                except Exception:
                                    pass
                    _params_done.wait(0.05)

            _wmon = threading.Thread(target=_poll_new_windows, daemon=True,
                                     name="8bf-wndmon")
            _wmon.start()

            _pt = threading.Thread(target=_params_thread, daemon=True, name="8bf-params")
            _pt.start()

            # Pump Win32 messages on the calling thread while the plugin dialog
            # is open. Some plugins post messages back expecting a running loop.
            _pmsg = ctypes.wintypes.MSG()
            while not _params_done.is_set():
                r = _u32.PeekMessageA(byref(_pmsg), None, 0, 0, 1)  # PM_REMOVE
                if r > 0:
                    _u32.TranslateMessage(byref(_pmsg))
                    _u32.DispatchMessageA(byref(_pmsg))
                else:
                    _params_done.wait(timeout=0.005)
            _pt.join()
            _wmon.join(timeout=0.5)
            _log(f"[8bf] plugin_wins={[hex(h) for h in _found_plugin_wins]}")
        finally:
            _gmfn_hits = _unhook_gmfn_inline(_gmfn_state)
            _unhook_reg_inline(_reg_state)

        if _params_exc[0] is not None:
            raise _params_exc[0]

        _log(f"[8bf] hook_selftest={_hook_selftest!r}")
        _log(f"[8bf] after PARAMETERS: inline_gmfn_hits={_gmfn_hits}  "
             f"filterCase={pd.filterCase}  cbt_events={_cbt_events}  "
             f"suites={_suite_log}  diag_hits={_diag_hits}")
        if result.value == userCanceledErr:
            return None
        if result.value != noErr:
            raise RuntimeError(
                f"Plugin returned error {result.value} on Parameters\n"
                f"[log: {_log_path}]")

        _call(SEL_PREPARE, "PREPARE")
        if result.value != noErr:
            raise RuntimeError(
                f"Plugin refused to run (SEL_PREPARE returned {result.value})\n"
                f"[diag: sSPBasic@{FilterRecord.sSPBasic.offset} "
                f"fr.sSPBasic=0x{fr.sSPBasic:016x} "
                f"suites_acquired={_suite_log}]\n"
                f"[log: {_log_path}]"
            )

        _ir_top_before    = fr.inRect_top
        _ir_bottom_before = fr.inRect_bottom

        _call(SEL_START, "START")
        _log(f"[8bf] after START: cbt_events={_cbt_events}")
        if result.value == userCanceledErr:
            _call(SEL_FINISH, "FINISH")
            raise RuntimeError(
                f"Plugin returned userCanceled on Start (error {result.value}) — "
                f"dialog may have failed to open\n"
                f"[suites_acquired={_suite_log}]\n[log: {_log_path}]"
            )
        if result.value != noErr:
            _call(SEL_FINISH, "FINISH")
            raise RuntimeError(
                f"Plugin returned error {result.value} on Start\n"
                f"[suites_acquired={_suite_log}]\n[log: {_log_path}]"
            )

        if fr.inRect_bottom < _ir_bottom_before or fr.inRect_top > _ir_top_before:
            for _ in range(2000):
                if fr.inRect_bottom <= fr.inRect_top:
                    break
                try:
                    pm(SEL_CONTINUE, fr_ptr, byref(data_val), byref(result))
                except Exception:
                    break
                if result.value != noErr:
                    break

        _call(SEL_FINISH, "FINISH")
        _log(f"[8bf] after FINISH: suites={_suite_log}  diag_hits={_diag_hits}")

        # ── Did the plugin actually change any pixels? ─────────────────────────
        _pixels_changed = (work_buf.raw != in_raw)
        _log(f"[8bf] pixels_changed={_pixels_changed}")

        # ── Decode output ─────────────────────────────────────────────────────
        # work_buf was initialised with the input and given as both inData and
        # outData, so in-place plugins and copy plugins both write their result
        # there.  Only fall back to memmove when the plugin replaced fr.outData
        # with its own allocated buffer.
        _own_addr   = cast(work_buf, c_void_p).value
        _plugin_out = fr.outData
        _log(f"[8bf] decode: own=0x{_own_addr:x}  plugin_out={_plugin_out}")
        if _plugin_out and _plugin_out != _own_addr:
            _out_size = h * row_bytes
            _tmp_buf  = create_string_buffer(_out_size)
            ctypes.memmove(_tmp_buf, _plugin_out, _out_size)
            out_raw = _tmp_buf.raw
        else:
            out_raw = work_buf.raw
        result_img = _PIL.frombytes("RGB", (w, h), out_raw)
        if orig_mode == "RGBA":
            r, g, b    = result_img.split()
            _, _, _, a = image.split()
            result_img = _PIL.merge("RGBA", (r, g, b, a))
        return result_img

    finally:
        try:
            if _hhook:
                _u32.UnhookWindowsHookEx(_hhook)
        except Exception:
            pass
        try:
            _u32.ShowWindow(c_void_p(pd.hwnd), 0)   # SW_HIDE
            _u32.SetWindowPos(c_void_p(pd.hwnd), 0, -32000, -32000, 1, 1,
                              0x0010)  # SWP_NOACTIVATE — park off-screen again
        except Exception:
            pass
        _log_file_handle[0] = None
        _log_file.close()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load(plugin_info: PluginInfo):
    """Load the DLL (cached) and return (dll, PluginMainProc) or (None, None)."""
    if plugin_info._dll is None:
        _plug_dir   = str(Path(plugin_info.path).parent)
        _parent_dir = str(Path(plugin_info.path).parent.parent)
        for _d in (_plug_dir, _parent_dir):
            try:
                _dll_dir_cookies.append(os.add_dll_directory(_d))
            except Exception:
                pass
        # Create the "Photoshop" sentinel window before DllMain runs.
        _eprint("[8bf] _load: ensuring sentinel")
        _ensure_ps_sentinel()
        # Hook GetModuleFileNameA, RegOpenKeyExA, and FindFirstFileA before loading
        # so DllMain sees the fake PS path, no Redfield registry keys, and we can
        # observe every file-existence check the plugin makes during init.
        _eprint("[8bf] _load: hooking gmfn+reg+findfile")
        _gmfn_state     = _hook_gmfn_inline()
        _reg_state      = _hook_reg_inline()
        _findfile_state = _hook_findfile_inline()
        _eprint("[8bf] _load: loading WinDLL")
        try:
            plugin_info._dll = ctypes.WinDLL(plugin_info.path)
        except OSError as e:
            _unhook_gmfn_inline(_gmfn_state)
            _unhook_reg_inline(_reg_state)
            _unhook_findfile_inline(_findfile_state)
            raise RuntimeError(
                f"Could not load '{plugin_info.path}': {e}") from e
        _eprint("[8bf] _load: WinDLL loaded, unhooking")
        _dllmain_gmfn_hits = _unhook_gmfn_inline(_gmfn_state)
        _unhook_reg_inline(_reg_state)
        _ff_paths = _unhook_findfile_inline(_findfile_state)
        _eprint(f"[8bf] dllmain_gmfn_hits={_dllmain_gmfn_hits}")
        # Log unique interesting paths (skip obvious system/python files).
        _skip_sfx = ('.dll', '.exe', '.pyd', '.pyc', '.png', '.ico', '.cur',
                     '.mui', '.sys', '.cat', '.manifest')
        _eprint(f"[8bf] dllmain_findfile_count={len(_ff_paths)}")
        _ff_seen: set = set()
        for _p in _ff_paths:
            _pl = _p.lower()
            _redfield = any(kw in _pl for kw in ('redfield', 'perfectum'))
            if (_redfield or not any(_pl.endswith(s) for s in _skip_sfx)) and _p not in _ff_seen:
                _ff_seen.add(_p)
                _eprint(f"[8bf] dllmain_findfile: {_p!r}")

        # Check the actual resolved IAT address for RegOpenKeyExA to verify
        # whether our inline hook on the advapi32 stub will intercept plugin calls.
        _adv32_rok = cast(ctypes.windll.advapi32.RegOpenKeyExA, c_void_p).value or 0
        try:
            _b = plugin_info._dll._handle
            _dos = ctypes.string_at(_b, 0x40)
            _pe_off = struct.unpack_from("<I", _dos, 0x3C)[0]
            _pe = ctypes.string_at(_b + _pe_off, 264)
            _opt = struct.unpack_from("<H", _pe, 24)[0]
            _ddb = 24 + (112 if _opt == 0x020B else 96)
            _irva = struct.unpack_from("<I", _pe, _ddb + 8)[0]
            _doff = 0
            while True:
                _d = ctypes.string_at(_b + _irva + _doff, 20)
                _orva, _, _, _nrva, _frva = struct.unpack_from("<IIIII", _d)
                if not _nrva:
                    break
                _dn = ctypes.string_at(_b + _nrva, 64).split(b"\x00")[0].decode("latin-1")
                _irva2 = _orva or _frva
                _t = 0
                while True:
                    _ov = struct.unpack_from("<Q", ctypes.string_at(_b + _irva2 + _t, 8))[0]
                    if not _ov:
                        break
                    if not (_ov >> 63):
                        _fn = ctypes.string_at(_b + _ov + 2, 64).split(b"\x00")[0].decode("latin-1")
                        if _dn.upper() == "ADVAPI32.DLL" and _fn == "RegOpenKeyExA":
                            _slot = struct.unpack_from("<Q", ctypes.string_at(_b + _frva + _t, 8))[0]
                            _eprint(f"[8bf] IAT RegOpenKeyExA slot=0x{_slot:x}  "
                                    f"adv32_stub=0x{_adv32_rok:x}  "
                                    f"hook_covers={'YES' if _slot == _adv32_rok else 'NO-MISS'}")
                    _t += 8
                _doff += 20
        except Exception as _ex:
            _eprint(f"[8bf] IAT check failed: {_ex}")

        _eprint("[8bf] _load: patching IAT")
        _patch_iat_module_filename(plugin_info._dll)
        _eprint("[8bf] _load: IAT patched")

    dll = plugin_info._dll
    for _ep in ("PluginMain", "ENTRYPOINT", "PlugInMain", "xPluginMain"):
        try:
            pm = PluginMainProc((_ep, dll))
            return dll, pm
        except AttributeError:
            continue
    return dll, None
