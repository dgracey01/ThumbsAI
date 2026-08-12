"""
ai_ops.py — ThumbsAI's shared AI-operations core.

The SINGLE place image intelligence acts on the library. Both entry points call THIS:
  • Internal — ThumbsAI's own worker (standalone JoyCaption), when the Suite/Jarvis is off.
  • External — Jarvis drives it (sends the order), when it's running the models.
Both pull the same DB levers (upsert tags / set_label_many / update_rating / search), so there is
one implementation, two front-ends. The DB is plain SQLite in WAL mode, so concurrent access is safe.

Increment 1 (this file): the DUPLICATE FINDER — needs NO model. It perceptual-hashes the thumbnails
already cached in the DB (no disk re-read) and groups visually-similar images, flagging byte-identical
copies via the stored file_hash. Later increments add caption→tags (auto-tagging) and NL search here.

Pure-PIL (no numpy). stdlib + Pillow only.
"""
from __future__ import annotations

import re
from io import BytesIO
from typing import Callable, Optional


# ── perceptual hash (dHash) ─────────────────────────────────────────────────────────────────────
def dhash(img, hash_size: int = 8) -> int:
    """64-bit difference hash: greyscale, resize to (n+1)×n, compare adjacent columns. Robust to
    re-encode / resize / minor edits (unlike a byte hash), so it catches VISUAL duplicates."""
    from PIL import Image
    small = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    px = list(small.getdata())
    w = hash_size + 1
    bits = 0
    for row in range(hash_size):
        base = row * w
        for col in range(hash_size):
            bits = (bits << 1) | (1 if px[base + col] > px[base + col + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    """Bit distance between two hashes (0 = identical look)."""
    return bin(a ^ b).count("1")


def _decode_thumb(blob) -> Optional[object]:
    """Cached thumbnail BLOB (JPEG bytes) → PIL image, or None if it won't decode."""
    if not blob:
        return None
    try:
        from PIL import Image
        return Image.open(BytesIO(bytes(blob)))
    except Exception:
        return None


# ── duplicate finder ────────────────────────────────────────────────────────────────────────────
def find_duplicate_groups(db, folder: str = "", recursive: bool = False,
                          threshold: int = 5, limit: int = 100000,
                          on_progress: Optional[Callable[[int, int], None]] = None,
                          should_cancel: Optional[Callable[[], bool]] = None) -> list[dict]:
    """Group visually-similar images in `folder` (or the whole library if empty).

    Returns a list of groups, largest first. Each group is:
        {"paths": [filepath, …], "exact": [filepath, …]}
      paths — every image whose look matches (dHash within `threshold`).
      exact — the subset that is byte-identical (same stored file_hash) = safe to delete outright.

    threshold: max dHash bit-distance to treat as the same image. 0 = identical look; ~5 tolerates
    re-encode/resize; >10 starts merging merely-similar shots. Uses the DB's cached thumbnails only.

    SCALE: identical looks are collapsed first (a folder of exact dupes is O(n)), then distinct hashes
    are compared only within shared LSH bands (pigeonhole: two hashes within `threshold` bits must share
    ≥1 of 8 byte-bands), so it stays fast at 100K+ images instead of O(n²).
    """
    rows = db.filter_images(folder=folder, recursive=recursive, with_thumbnails=True, limit=limit)
    items = []                                            # (filepath, file_hash, dhash)
    total = len(rows)
    for i, r in enumerate(rows):
        if should_cancel and should_cancel():
            return []
        img = _decode_thumb(r.get("thumbnail"))
        if img is not None:
            try:
                items.append((r["filepath"], r.get("file_hash") or "", dhash(img)))
            except Exception:
                pass
        if on_progress and (i % 100 == 0 or i == total - 1):
            on_progress(i + 1, total)

    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 1) collapse identical dHashes instantly (the bulk case: re-saved / copied images)
    from collections import defaultdict
    by_hash: dict[int, list[int]] = defaultdict(list)
    for idx, (_fp, _fh, h) in enumerate(items):
        by_hash[h].append(idx)
    for idxs in by_hash.values():
        for k in idxs[1:]:
            union(idxs[0], k)

    # 2) band the DISTINCT hashes into 8 byte-bands; only compare within a shared band
    distinct = list(by_hash.keys())
    buckets = [defaultdict(list) for _ in range(8)]
    for di, h in enumerate(distinct):
        for b in range(8):
            buckets[b][(h >> (b * 8)) & 0xFF].append(di)
    checked: set = set()
    for b in range(8):
        if should_cancel and should_cancel():
            return []
        for cand in buckets[b].values():
            if len(cand) < 2:
                continue
            for i in range(len(cand)):
                hi = distinct[cand[i]]
                for j in range(i + 1, len(cand)):
                    a, c = cand[i], cand[j]
                    pair = (a, c) if a < c else (c, a)
                    if pair in checked:
                        continue
                    checked.add(pair)
                    if hamming(hi, distinct[c]) <= threshold:
                        union(by_hash[hi][0], by_hash[distinct[c]][0])

    # 3) gather connected components into groups
    comp: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        comp[find(idx)].append(idx)
    groups: list[dict] = []
    for members in comp.values():
        if len(members) < 2:
            continue
        paths = [items[m][0] for m in members]
        seen: dict[str, list[str]] = {}
        for m in members:
            fh = items[m][1]
            if fh:
                seen.setdefault(fh, []).append(items[m][0])
        exact = [fp for grp in seen.values() if len(grp) > 1 for fp in grp]
        groups.append({"paths": paths, "exact": exact})

    groups.sort(key=lambda g: len(g["paths"]), reverse=True)
    return groups


# ── auto-tagging (caption → searchable tags) ──────────────────────────────────────────────────────
# Light normalization to a consistent BOORU vocabulary so the SAME concept always indexes identically
# (queries depend on this). Intentionally small — the booru tagging instruction does the heavy lifting;
# this catches the frequent drifts. NOTE: the subject-count mappings assume a predominantly
# single-subject character library (woman/girl -> 1girl); adjust if you tag group scenes.
_TAG_ALIASES = {
    "woman": "1girl", "girl": "1girl", "lady": "1girl", "female": "1girl",
    "man": "1boy", "guy": "1boy", "male": "1boy",
    "blond": "blonde", "blond_hair": "blonde_hair",
    "naked": "nude",
    "photo_realistic": "photorealistic", "photorealism": "photorealistic",
    "looking_at_the_viewer": "looking_at_viewer", "looking_at_camera": "looking_at_viewer",
    "outdoor": "outdoors", "indoor": "indoors", "nighttime": "night", "night_time": "night",
    "daytime": "day",
}

# The USER's own alias additions (edited in Settings ▸ AI ▸ Tag alias map) are layered ON TOP of the
# built-ins, so more terms can be added later without touching code, and a user pair overrides a
# built-in on conflict. `_effective_aliases` is the merged map every tag actually goes through.
_user_aliases: dict[str, str] = {}
_effective_aliases: dict[str, str] = dict(_TAG_ALIASES)


def _canon_key(s: str) -> str:
    """Normalize an alias side (from/to) to the same shape a tag has: lowercase, underscores, trimmed."""
    s = (s or "").strip().strip(".,;:!?").lower()
    s = re.sub(r"[\s\-]+", "_", s)
    return re.sub(r"_+", "_", s).strip("_")


def parse_aliases(text: str) -> dict[str, str]:
    """Parse the user's alias block into {from: to}. One pair per line, written 'from = to' (also accepts
    '->' or ':'). Blank lines and '#' comments are ignored. Both sides are canonicalized so the map keys
    match real tags. Example line:  breasts = boobs"""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\s*(?:=|->|:)\s*", line, maxsplit=1)
        if len(parts) == 2:
            k, v = _canon_key(parts[0]), _canon_key(parts[1])
            if k and v:
                out[k] = v
    return out


def set_user_aliases(mapping: dict[str, str] | None) -> None:
    """Install the user's alias additions (from parse_aliases) so every subsequent tag pass uses them.
    Call this before a tag job. User pairs win over built-ins on conflict."""
    global _user_aliases, _effective_aliases
    _user_aliases = dict(mapping or {})
    _effective_aliases = {**_TAG_ALIASES, **_user_aliases}


def _normalize_tag(frag: str) -> str:
    """One raw fragment -> canonical booru tag: lowercase, spaces/hyphens -> underscores, aliased
    (built-in aliases + the user's own additions, user winning on conflict)."""
    t = frag.strip().strip(".,;:!?").lower()
    t = re.sub(r"[\s\-]+", "_", t)
    t = re.sub(r"_+", "_", t).strip("_")
    return _effective_aliases.get(t, t)


def _clean_tags(text: str) -> list[str]:
    """Turn a captioner's comma/newline output into clean lowercase tags: trimmed, de-duplicated,
    sentence-length fragments dropped (a 'tag' of 8 words is a caption leak, not a tag)."""
    out, seen = [], set()
    for frag in re.split(r"[,\n]", text or ""):
        t = _normalize_tag(frag)
        if not t or len(t) > 40 or t.count("_") > 4:      # booru tags are short; long = caption leak
            continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def caption_to_tags(db, filepaths: list[str], captioner, instruction: Optional[str] = None,
                    merge: bool = True,
                    on_progress: Optional[Callable[[int, int, str, str], None]] = None,
                    should_cancel: Optional[Callable[[], bool]] = None) -> int:
    """Caption each image and write the words to its `tags` column (FTS5-indexed → instantly searchable,
    even for images with no embedded generation metadata). `captioner` is any object with
    .caption(path, instruction)->str (see caption_backend.Captioner). merge=True keeps existing tags.
    Returns how many images were tagged. on_progress(current, total, filepath, tags_or_error)."""
    total = len(filepaths)
    tagged = 0
    for i, fp in enumerate(filepaths):
        if should_cancel and should_cancel():
            break
        text = captioner.caption(fp, instruction) if instruction else captioner.caption(fp)
        if text.startswith(("ERROR", "CAPTION FAILED", "DENIED", "NOT FOUND")):
            if on_progress:
                on_progress(i + 1, total, fp, text)
            continue
        tags = _clean_tags(text)
        if merge:
            row = db.get(fp) or {}
            existing = [t.strip().lower() for t in (row.get("tags") or "").split(",") if t.strip()]
            merged, seen = [], set()
            for t in existing + tags:
                if t not in seen:
                    seen.add(t)
                    merged.append(t)
            tags = merged
        if tags:
            # INVARIANT: JoyCaption writes the `tags` column ONLY — it must never alter an image's
            # generation-source metadata (source / prompt / negative_prompt / model / sampler / etc.).
            # db.upsert updates just the field(s) passed, so tags=… leaves every embedded-generation
            # field untouched. Do NOT add other keyword fields to this call.
            db.upsert(fp, tags=", ".join(tags))
            tagged += 1
        if on_progress:
            on_progress(i + 1, total, fp, ", ".join(tags) if tags else "(no tags)")
    return tagged


def suggest_keeper(db, paths: list[str]) -> str:
    """Which image in a duplicate group to KEEP: highest rating, then largest file, then first by name.
    The rest are the reject candidates."""
    best, best_key = paths[0], None
    for fp in paths:
        row = db.get(fp) or {}
        key = (int(row.get("rating") or 0), int(row.get("filesize") or 0))
        if best_key is None or key > best_key:
            best, best_key = fp, key
    return best


def mark_duplicate_rejects(db, groups: list[dict], keep: str = "auto") -> int:
    """Label the non-keeper of each group as REJECT (label=2) so the user can review/cull in the grid.
    Non-destructive — flags only, never deletes. Returns how many images were flagged."""
    rejects: list[str] = []
    for g in groups:
        paths = g["paths"]
        keeper = suggest_keeper(db, paths) if keep == "auto" else paths[0]
        rejects += [p for p in paths if p != keeper]
    return db.set_label_many(rejects, 2) if rejects else 0
