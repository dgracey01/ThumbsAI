"""
Diagnostic script — run from the ThumbsAI directory:
  python test_plugin_scan.py
Prints exactly what the PIPL scanner sees.
"""
import sys, ctypes, struct, os
from ctypes import (
    WINFUNCTYPE, c_int32, c_uint32, c_void_p, c_wchar_p, c_int16
)
from pathlib import Path

PLUGIN_PATH = r"C:\Program Files (x86)\Topaz Labs\Topaz DeNoise 5\Plugins_x64\tldenoise5ps_x64.8bf"

# ── 1. PE bitness ─────────────────────────────────────────────────────────────
def pe_is_64bit(path):
    try:
        with open(path, "rb") as f:
            if f.read(2) != b"MZ": return None, "not MZ"
            f.seek(0x3C)
            pe_off = struct.unpack_from("<I", f.read(4))[0]
            f.seek(pe_off)
            sig = f.read(4)
            if sig != b"PE\x00\x00": return None, f"bad PE sig {sig!r}"
            machine = struct.unpack_from("<H", f.read(2))[0]
            return machine == 0x8664, f"machine={machine:#06x}"
    except Exception as e:
        return None, str(e)

is64, detail = pe_is_64bit(PLUGIN_PATH)
print(f"[PE] is_64bit={is64}  ({detail})")
if is64 is None:
    sys.exit(1)

# ── 2. Load as data file ──────────────────────────────────────────────────────
k32 = ctypes.windll.kernel32
LOAD_LIBRARY_AS_DATAFILE = 0x00000002
k32.LoadLibraryExW.restype  = c_void_p
k32.LoadLibraryExW.argtypes = [c_wchar_p, c_void_p, c_uint32]
k32.FreeLibrary.restype     = c_int32
k32.FreeLibrary.argtypes    = [c_void_p]
k32.FindResourceW.restype   = c_void_p
k32.FindResourceW.argtypes  = [c_void_p, c_void_p, c_void_p]
k32.SizeofResource.restype  = c_uint32
k32.SizeofResource.argtypes = [c_void_p, c_void_p]
k32.LoadResource.restype    = c_void_p
k32.LoadResource.argtypes   = [c_void_p, c_void_p]
k32.LockResource.restype    = c_void_p
k32.LockResource.argtypes   = [c_void_p]

hmod = k32.LoadLibraryExW(PLUGIN_PATH, None, LOAD_LIBRARY_AS_DATAFILE)
print(f"[Load] hmod={hmod:#x}" if hmod else f"[Load] FAILED err={ctypes.GetLastError()}")
if not hmod:
    sys.exit(1)

# ── 3. Enumerate ALL resource types ──────────────────────────────────────────
all_types = []

@WINFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)
def _type_cb(hMod, lpType, lParam):
    val = lpType  # already an int from c_void_p
    if val and val < 0x10000:
        all_types.append(f"#{val}")
    else:
        try:
            all_types.append(ctypes.wstring_at(val))
        except Exception:
            all_types.append(f"?{val:#x}")
    return 1

k32.EnumResourceTypesW(c_void_p(hmod), _type_cb, None)
print(f"[Types] {all_types}")

# ── 4. Enumerate PIPL names ───────────────────────────────────────────────────
names = []

@WINFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p, c_void_p)
def _name_cb(hModule, lpType, lpName, lParam):
    names.append(lpName)
    return 1

ret = k32.EnumResourceNamesW(c_void_p(hmod), "PIPL", _name_cb, None)
print(f"[PIPL enum] ret={ret}  names={names}")

# ── 5. If any PIPL resources found, dump the raw bytes ───────────────────────
for res_name in names:
    hrsrc = k32.FindResourceW(hmod, res_name, "PIPL")
    if not hrsrc:
        print(f"  FindResourceW failed for name={res_name}")
        continue
    size    = k32.SizeofResource(hmod, hrsrc)
    hglobal = k32.LoadResource(hmod, hrsrc)
    ptr     = k32.LockResource(hglobal) if hglobal else 0
    if not ptr:
        print(f"  LockResource failed")
        continue
    raw = ctypes.string_at(ptr, size)
    print(f"  raw ({size} bytes): {raw[:64].hex()}")
    if len(raw) >= 8:
        version, count = struct.unpack_from(">II", raw, 0)
        print(f"  version={version:#010x}  count={count}")
        pos = 8
        for i in range(count):
            if pos + 16 > len(raw):
                print(f"  prop {i}: truncated at pos={pos}")
                break
            vendor   = raw[pos:pos+4].decode("latin-1", errors="replace")
            key      = raw[pos+4:pos+8].decode("latin-1", errors="replace")
            prop_id  = struct.unpack_from(">I", raw, pos+8)[0]
            data_len = struct.unpack_from(">I", raw, pos+12)[0]
            prop     = raw[pos+16 : pos+16+data_len]
            pos     += 16 + ((data_len + 3) & ~3)
            print(f"  prop {i}: vendor={vendor!r} key={key!r} id={prop_id} len={data_len} data={prop[:16].hex()}")

k32.FreeLibrary(c_void_p(hmod))
print("Done.")
