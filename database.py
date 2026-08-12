"""
database.py — SQLite thumbnail cache and metadata store for ThumbsAI
Designed by: Zero  |  Built by: Jarvis

Architecture (digiKam / XnView pattern):
  images     — metadata only (filepath, dimensions, AI meta, rating, tags…)
               No BLOBs here so index scans stay fast regardless of image count.
  thumbnails — BLOB-only table keyed by image_id.
               Separated so metadata queries never touch image data.

Invalidation: mtime + file_hash (xxhash-64 of first 64KB).
  mtime alone catches 99% of changes (free — already stat'd).
  hash catches renamed/copied files with stale mtime.

Pragmas:
  WAL       — concurrent reads while a background write is in progress
  NORMAL    — safe for WAL, no fsync on every commit
  cache_size=-64000 — 64 MB page cache (was 32 MB)
  mmap_size — memory-map up to 512 MB of the DB file for O(1) reads
  optimize  — run periodically to refresh query-planner statistics
"""
from __future__ import annotations
import hashlib
import sqlite3
import threading
from datetime import datetime
from pathlib  import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
THUMBS_DB = DATA_DIR / "thumbs.db"

# How many bytes to hash for change detection (first 64 KB)
_HASH_BYTES = 65536


def _file_hash(path: str) -> str:
    """SHA-1 of first 64 KB — fast, collision-resistant enough for cache keys."""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            h.update(f.read(_HASH_BYTES))
    except OSError:
        return ""
    return h.hexdigest()


class ThumbsDB:
    """SQLite cache with one connection PER THREAD.

    A single shared connection serialises every read and write through one
    lock, so a UI-thread folder read would block behind a background scan's
    write. Giving each thread its own connection lets WAL mode do what it was
    enabled for: concurrent reads while a background write is in progress.
    Each method still uses ``self._c``, which now resolves to the calling
    thread's own connection via the property below.
    """

    def __init__(self):
        self._local      = threading.local()
        self._all_conns  = []
        self._conns_lock = threading.Lock()
        # First self._c access (inside _init) creates this thread's connection
        # with pragmas; then we build the schema and run migrations once.
        self._init()

    def _make_conn(self) -> sqlite3.Connection:
        """Open a new connection and apply per-connection pragmas."""
        c = sqlite3.connect(str(THUMBS_DB), check_same_thread=False, timeout=5.0)
        c.row_factory = sqlite3.Row
        # journal_mode=WAL persists on the DB file; the rest are per-connection
        # and must be set on every new connection.
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")         # enforce ON DELETE CASCADE: deleting an image now also
                                                    # drops its thumbnail BLOB (else orphaned BLOBs pile up)
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=-64000")       # 64 MB page cache
        c.execute("PRAGMA mmap_size=536870912")     # 512 MB memory-map
        c.execute("PRAGMA temp_store=MEMORY")
        c.execute("PRAGMA busy_timeout=5000")       # wait up to 5 s on lock
        c.execute("PRAGMA wal_autocheckpoint=1000") # checkpoint every 1000 pages
        return c

    @property
    def _c(self) -> sqlite3.Connection:
        """The calling thread's own connection (created lazily on first use)."""
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._make_conn()
            self._local.conn = c
            with self._conns_lock:
                self._all_conns.append(c)
        return c

    def _init(self):
        # Touch self._c to create the main-thread connection (with pragmas),
        # then create the schema and run migrations once.
        # NEW databases: choose INCREMENTAL auto-vacuum BEFORE any table exists — the only point SQLite lets
        # auto_vacuum be set without a full VACUUM. This is what lets reclaim() return freed pages to the OS
        # cheaply after orphan removal, so the file shrinks instead of only ever growing. (Legacy DBs created
        # before this get converted on their first reclaim().)
        if self._c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='images'").fetchone() is None:
            self._c.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self._c.executescript("""
            -- ── Metadata table (no BLOBs) ────────────────────────────────────
            CREATE TABLE IF NOT EXISTS images (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath        TEXT    NOT NULL UNIQUE,
                filename        TEXT    NOT NULL,
                folder          TEXT    NOT NULL,
                width           INTEGER,
                height          INTEGER,
                filesize        INTEGER,
                modified_at     REAL,
                file_hash       TEXT,
                added_at        TEXT,
                prompt          TEXT,
                negative_prompt TEXT,
                seed            TEXT,
                model           TEXT,
                sampler         TEXT,
                cfg_scale       TEXT,
                steps           TEXT,
                source          TEXT,
                raw_meta        TEXT,
                rating          INTEGER DEFAULT 0,
                tags            TEXT
            );

            -- ── Thumbnail BLOB table (separate from metadata) ─────────────────
            -- Keeping BLOBs out of the images table means:
            --   • Index scans on images never load image data
            --   • Metadata queries stay fast at 100K+ rows
            CREATE TABLE IF NOT EXISTS thumbnails (
                image_id  INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
                data      BLOB    NOT NULL
            );

            -- ── Indexes ───────────────────────────────────────────────────────
            -- Note: idx_folder_name and idx_file_hash are created in
            -- _migrate_add_columns() after the file_hash column is guaranteed present.
            CREATE INDEX IF NOT EXISTS idx_folder          ON images(folder);
            CREATE INDEX IF NOT EXISTS idx_modified        ON images(modified_at);
            CREATE INDEX IF NOT EXISTS idx_model           ON images(model);
            CREATE INDEX IF NOT EXISTS idx_source          ON images(source);
            CREATE INDEX IF NOT EXISTS idx_folder_modified ON images(folder, modified_at DESC);
            CREATE INDEX IF NOT EXISTS idx_folder_name     ON images(folder, filename COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_rating          ON images(rating);
        """)
        self._c.commit()

        # Migrations — order matters
        self._migrate_split_thumbnails()   # move BLOBs out of images table
        self._migrate_add_columns()        # add columns introduced after initial release
        self._migrate_fts5()               # FTS5 virtual table for text search

    def _migrate_split_thumbnails(self):
        """
        One-time migration: if images.thumbnail column exists (old schema),
        move all BLOBs to the thumbnails table and drop the column.

        SQLite does not support DROP COLUMN before 3.35.0 — we use
        a table-rebuild approach for broad compatibility.
        """
        cols = [r[1] for r in self._c.execute("PRAGMA table_info(images)").fetchall()]
        if "thumbnail" not in cols:
            return   # already migrated or fresh DB

        import sys
        print("[ThumbsDB] Migrating: moving thumbnail BLOBs to separate table…",
              file=sys.stderr)

        self._c.executescript("""
            -- Copy BLOBs to thumbnails table (skip NULLs)
            INSERT OR IGNORE INTO thumbnails (image_id, data)
            SELECT id, thumbnail FROM images WHERE thumbnail IS NOT NULL;

            -- Rebuild images without the thumbnail column
            CREATE TABLE images_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath        TEXT    NOT NULL UNIQUE,
                filename        TEXT    NOT NULL,
                folder          TEXT    NOT NULL,
                width           INTEGER,
                height          INTEGER,
                filesize        INTEGER,
                modified_at     REAL,
                file_hash       TEXT,
                added_at        TEXT,
                prompt          TEXT,
                negative_prompt TEXT,
                seed            TEXT,
                model           TEXT,
                sampler         TEXT,
                cfg_scale       TEXT,
                steps           TEXT,
                source          TEXT,
                raw_meta        TEXT,
                rating          INTEGER DEFAULT 0,
                tags            TEXT
            );

            INSERT INTO images_new
            SELECT id, filepath, filename, folder,
                   width, height, filesize, modified_at,
                   NULL,
                   added_at, prompt, negative_prompt, seed, model,
                   sampler, cfg_scale, steps, source, raw_meta,
                   rating, tags
            FROM images;

            DROP TABLE images;
            ALTER TABLE images_new RENAME TO images;

            CREATE INDEX IF NOT EXISTS idx_folder       ON images(folder);
            CREATE INDEX IF NOT EXISTS idx_folder_name  ON images(folder, filename COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_modified     ON images(modified_at);
            CREATE INDEX IF NOT EXISTS idx_file_hash    ON images(file_hash);
            CREATE INDEX IF NOT EXISTS idx_model        ON images(model);
            CREATE INDEX IF NOT EXISTS idx_source       ON images(source);
        """)
        self._c.commit()
        print("[ThumbsDB] Migration complete.", file=sys.stderr)

    def _migrate_add_columns(self):
        """Add any columns that didn't exist in earlier schema versions."""
        existing = {r[1] for r in self._c.execute("PRAGMA table_info(images)").fetchall()}
        additions = [
            ("file_hash", "TEXT"),
            ("label", "INTEGER DEFAULT 0"),   # pick/reject (digiKam-style): 0=none, 1=pick/keep, 2=reject
        ]
        for col, coltype in additions:
            if col not in existing:
                self._c.execute(f"ALTER TABLE images ADD COLUMN {col} {coltype}")
        self._c.commit()

        # Ensure new indexes exist (safe to re-run — IF NOT EXISTS)
        self._c.executescript("""
            CREATE INDEX IF NOT EXISTS idx_folder_name ON images(folder, filename COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_file_hash   ON images(file_hash);
            CREATE INDEX IF NOT EXISTS idx_label       ON images(label);
        """)
        self._c.commit()

    def _migrate_fts5(self):
        """Create/UPGRADE the FTS5 full-text index over ALL searchable columns + sync triggers (idempotent).
        Older DBs indexed only filename/prompt/tags; this rebuilds them to also cover negative_prompt,
        model, seed, and sampler so the (now single) FTS-backed search() matches the legacy LIKE coverage."""
        has_fts = self._c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='images_fts'").fetchone()
        if has_fts:
            cols = {r[1].lower() for r in self._c.execute("PRAGMA table_info(images_fts)").fetchall()}
            if "model" in cols:
                return   # already the expanded schema
        self._c.executescript("""
            DROP TRIGGER IF EXISTS images_fts_ai;
            DROP TRIGGER IF EXISTS images_fts_ad;
            DROP TRIGGER IF EXISTS images_fts_au;
            DROP TABLE IF EXISTS images_fts;
            CREATE VIRTUAL TABLE images_fts USING fts5(
                filename, prompt, negative_prompt, model, seed, sampler, tags,
                content=images, content_rowid=id
            );
            INSERT INTO images_fts(rowid, filename, prompt, negative_prompt, model, seed, sampler, tags)
                SELECT id, coalesce(filename,''), coalesce(prompt,''), coalesce(negative_prompt,''),
                       coalesce(model,''), coalesce(seed,''), coalesce(sampler,''), coalesce(tags,'')
                FROM images;
            CREATE TRIGGER images_fts_ai AFTER INSERT ON images BEGIN
                INSERT INTO images_fts(rowid, filename, prompt, negative_prompt, model, seed, sampler, tags)
                VALUES (new.id, coalesce(new.filename,''), coalesce(new.prompt,''), coalesce(new.negative_prompt,''),
                        coalesce(new.model,''), coalesce(new.seed,''), coalesce(new.sampler,''), coalesce(new.tags,''));
            END;
            CREATE TRIGGER images_fts_ad AFTER DELETE ON images BEGIN
                INSERT INTO images_fts(images_fts, rowid, filename, prompt, negative_prompt, model, seed, sampler, tags)
                VALUES ('delete', old.id, coalesce(old.filename,''), coalesce(old.prompt,''), coalesce(old.negative_prompt,''),
                        coalesce(old.model,''), coalesce(old.seed,''), coalesce(old.sampler,''), coalesce(old.tags,''));
            END;
            CREATE TRIGGER images_fts_au AFTER UPDATE ON images BEGIN
                INSERT INTO images_fts(images_fts, rowid, filename, prompt, negative_prompt, model, seed, sampler, tags)
                VALUES ('delete', old.id, coalesce(old.filename,''), coalesce(old.prompt,''), coalesce(old.negative_prompt,''),
                        coalesce(old.model,''), coalesce(old.seed,''), coalesce(old.sampler,''), coalesce(old.tags,''));
                INSERT INTO images_fts(rowid, filename, prompt, negative_prompt, model, seed, sampler, tags)
                VALUES (new.id, coalesce(new.filename,''), coalesce(new.prompt,''), coalesce(new.negative_prompt,''),
                        coalesce(new.model,''), coalesce(new.seed,''), coalesce(new.sampler,''), coalesce(new.tags,''));
            END;
        """)
        self._c.commit()

    # ── Periodic maintenance ──────────────────────────────────────────────────

    def optimize(self):
        """Refresh query-planner statistics. Call once at startup or after bulk inserts."""
        self._c.execute("PRAGMA optimize")
        self._c.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def search(self, query: str, folder: str = "", require_thumbnail: bool = True) -> list[dict]:
        """FTS5 full-text search across filename/prompt/negative/model/seed/sampler/tags (O(log N), indexed),
        thumbnails joined. Terms are AND-ed. Falls back to a LIKE scan if the FTS query won't parse.

        require_thumbnail=True (default) keeps rows that are VIEWABLE (have a cached thumbnail) OR carry
        generation metadata (source/prompt/model). This hides dead no-thumbnail-no-metadata ghosts (the empty
        "No Image" box complaint) WITHOUT hiding AI-generated images whose thumbnail was never cached — those
        stay findable by their prompt/model/seed. require_thumbnail=False returns everything (LEFT JOIN)."""
        q = query.strip()
        if not q:
            return []
        fts_q = " ".join(f'"{w}"' for w in q.split())     # quote/escape each term; space = implicit AND
        # viewable OR flagged as a generated image (source = A1111/NovelAI/ComfyUI/…) — never drop a
        # generation just because its thumbnail was never cached. `source` is the reliable generated-image
        # flag; prompt/model can be spuriously populated on non-generated PNGs, so they're NOT used here.
        keep = ("(t.data IS NOT NULL OR i.source != '')"
                if require_thumbnail else "")
        sql = ("SELECT i.*, t.data AS thumbnail FROM images_fts f "
               "JOIN images i ON i.id = f.rowid "
               "LEFT JOIN thumbnails t ON t.image_id = i.id "
               "WHERE images_fts MATCH ?")
        params: list = [fts_q]
        if keep:
            sql += " AND " + keep
        if folder:
            sql += " AND i.folder = ?"
            params.append(folder)
        sql += " ORDER BY rank LIMIT 5000"
        try:
            return [dict(r) for r in self._c.execute(sql, params).fetchall()]
        except Exception:
            like = f"%{query}%"
            base = ("SELECT i.*, t.data AS thumbnail FROM images i "
                    "LEFT JOIN thumbnails t ON t.image_id=i.id WHERE ")
            cond = ("(i.filename LIKE ? OR i.prompt LIKE ? OR i.model LIKE ? OR i.seed LIKE ? "
                    "OR i.sampler LIKE ? OR i.tags LIKE ?)")
            fp = [like] * 6
            if keep:
                cond = keep + " AND " + cond   # same viewable-or-has-metadata guard as the FTS path
            if folder:
                cond = "i.folder=? AND " + cond
                fp = [folder] + fp
            try:
                return [dict(r) for r in self._c.execute(
                    base + cond + " ORDER BY i.filename COLLATE NOCASE", fp).fetchall()]
            except Exception:
                return []

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert(self, filepath: str, thumbnail: bytes | None = None,
               **fields) -> None:
        """
        Insert or update a row.  filepath is the unique key.
        thumbnail is stored in the thumbnails table, not images.
        """
        now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        name   = Path(filepath).name
        folder = str(Path(filepath).parent)

        row = self._c.execute(
            "SELECT id FROM images WHERE filepath=?", (filepath,)).fetchone()

        if row:
            image_id = row[0]
            if fields:
                # Never write 'thumbnail' into images — it belongs in thumbnails
                fields.pop("thumbnail", None)
                sets = ", ".join(f"{k}=?" for k in fields)
                self._c.execute(
                    f"UPDATE images SET {sets} WHERE filepath=?",
                    list(fields.values()) + [filepath])
        else:
            fields.pop("thumbnail", None)
            cols = ["filepath", "filename", "folder", "added_at"] + list(fields.keys())
            vals = [filepath, name, folder, now] + list(fields.values())
            self._c.execute(
                f"INSERT INTO images ({', '.join(cols)}) "
                f"VALUES ({', '.join('?'*len(cols))})", vals)
            image_id = self._c.execute(
                "SELECT id FROM images WHERE filepath=?", (filepath,)).fetchone()[0]

        if thumbnail is not None:
            self._c.execute(
                "INSERT INTO thumbnails(image_id, data) VALUES(?,?) "
                "ON CONFLICT(image_id) DO UPDATE SET data=excluded.data",
                (image_id, thumbnail))

        self._c.commit()

    def move_path(self, old_fp: str, new_fp: str) -> None:
        """Update a row's path after the file moved on disk, preserving the cached thumbnail
        and metadata (the thumbnail is keyed by image_id, which doesn't change)."""
        new_name   = Path(new_fp).name
        new_folder = str(Path(new_fp).parent)
        self._c.execute(
            "UPDATE images SET filepath=?, filename=?, folder=? WHERE filepath=?",
            (new_fp, new_name, new_folder, old_fp))
        self._c.commit()

    def batch_upsert(self, records: list[dict], commit: bool = True) -> None:
        """
        Insert or update many rows in a single transaction.

        Each dict in *records* should contain:
          filepath   — required, unique key
          thumbnail  — optional bytes (stored in thumbnails table)
          ...        — any other images column names as keyword-style keys

        Using a single transaction for N records is 10–100× faster than N
        separate upsert() calls because each SQLite commit is an fsync.
        """
        if not records:
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Pre-fetch all existing (filepath → id) in one query so we can
        # decide INSERT vs UPDATE without per-row SELECTs.
        fps = [r["filepath"] for r in records]
        placeholders = ",".join("?" * len(fps))
        existing = {
            row[0]: row[1]
            for row in self._c.execute(
                f"SELECT filepath, id FROM images WHERE filepath IN ({placeholders})",
                fps,
            ).fetchall()
        }

        for rec in records:
            filepath  = rec["filepath"]
            thumbnail = rec.get("thumbnail")           # read without mutating
            # Build fields dict excluding the non-column keys
            fields    = {k: v for k, v in rec.items()
                         if k not in ("filepath", "thumbnail")}
            name      = Path(filepath).name
            folder    = str(Path(filepath).parent)

            if filepath in existing:
                image_id = existing[filepath]
                if fields:
                    sets = ", ".join(f"{k}=?" for k in fields)
                    self._c.execute(
                        f"UPDATE images SET {sets} WHERE filepath=?",
                        list(fields.values()) + [filepath],
                    )
            else:
                cols = ["filepath", "filename", "folder", "added_at"] + list(fields.keys())
                vals = [filepath, name, folder, now] + list(fields.values())
                self._c.execute(
                    f"INSERT OR IGNORE INTO images ({', '.join(cols)}) "
                    f"VALUES ({', '.join('?' * len(cols))})",
                    vals,
                )
                row = self._c.execute(
                    "SELECT id FROM images WHERE filepath=?", (filepath,)
                ).fetchone()
                image_id = row[0] if row else None
                if image_id:
                    existing[filepath] = image_id

            if thumbnail is not None and image_id is not None:
                self._c.execute(
                    "INSERT INTO thumbnails(image_id, data) VALUES(?,?) "
                    "ON CONFLICT(image_id) DO UPDATE SET data=excluded.data",
                    (image_id, thumbnail),
                )

        if commit:
            self._c.commit()

    def update_rating(self, filepath: str, rating: int):
        self._c.execute(
            "UPDATE images SET rating=? WHERE filepath=?", (rating, filepath))
        self._c.commit()

    def set_label_many(self, filepaths: list[str], label: int) -> int:
        """Bulk pick/reject across a selection, one transaction. Returns rows changed."""
        if not filepaths:
            return 0
        changed = 0
        for i in range(0, len(filepaths), 500):
            chunk = filepaths[i:i + 500]
            ph = ",".join("?" * len(chunk))
            cur = self._c.execute(
                f"UPDATE images SET label=? WHERE filepath IN ({ph})", [int(label)] + chunk)
            changed += cur.rowcount
        self._c.commit()
        return changed

    def rename_filepath(self, old_path: str, new_path: str) -> None:
        new_name   = Path(new_path).name
        new_folder = str(Path(new_path).parent)
        self._c.execute(
            "UPDATE images SET filepath=?, filename=?, folder=? WHERE filepath=?",
            (new_path, new_name, new_folder, old_path))
        self._c.commit()

    def delete(self, filepath: str):
        # thumbnails row is removed by ON DELETE CASCADE
        self._c.execute("DELETE FROM images WHERE filepath=?", (filepath,))
        self._c.commit()

    def reclaim(self, convert_if_needed: bool = True) -> int:
        """Return freed disk space to the OS so the DB file actually SHRINKS after deletes (SQLite otherwise
        keeps deleted pages as internal free space forever — the file only grows). Also purges any orphaned
        thumbnail BLOBs left behind by pre-cascade deletes. Returns bytes freed (approx). Safe to call any
        time; a no-op-cost call when there's nothing to reclaim.

        • Fast path — INCREMENTAL auto_vacuum DB: `PRAGMA incremental_vacuum` truncates freelist pages off the
          end of the file. No full rewrite, no 2x-disk requirement.
        • One-time path — legacy auto_vacuum=NONE DB: set INCREMENTAL + `VACUUM` (the VACUUM applies the mode
          switch AND reclaims all current free space in one pass). Every later call is then the fast path."""
        c = self._c
        try:
            before = THUMBS_DB.stat().st_size
        except OSError:
            before = 0
        try:
            # 1. drop orphaned thumbnail BLOBs (image row already gone — e.g. legacy no-cascade deletes)
            c.execute("DELETE FROM thumbnails WHERE NOT EXISTS "
                      "(SELECT 1 FROM images i WHERE i.id = thumbnails.image_id)")
            c.commit()
            # 2. hand the freed pages back to the filesystem
            mode = c.execute("PRAGMA auto_vacuum").fetchone()[0]     # 0=NONE 1=FULL 2=INCREMENTAL
            if mode == 2:
                c.execute("PRAGMA incremental_vacuum")
                c.commit()
            elif convert_if_needed:
                c.execute("PRAGMA auto_vacuum=INCREMENTAL")
                c.execute("VACUUM")            # one-time: switch mode + reclaim existing free space
                c.commit()
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")            # flush + shrink the -wal sidecar too
        except Exception:
            return 0
        try:
            return max(0, before - THUMBS_DB.stat().st_size)
        except OSError:
            return 0

    def delete_paths(self, filepaths: list[str]) -> int:
        """Delete many rows by filepath in one transaction (thumbnails cascade).

        Returns the number of rows removed. Used by the folder scan to prune
        entries for files that have been deleted from disk — the caller already
        knows the paths are gone, so no per-file stat() is done here.
        """
        if not filepaths:
            return 0
        removed = 0
        # Chunk to stay under SQLite's default 999 bound-parameter limit.
        for i in range(0, len(filepaths), 500):
            chunk = filepaths[i:i + 500]
            ph = ",".join("?" * len(chunk))
            cur = self._c.execute(
                f"DELETE FROM images WHERE filepath IN ({ph})", chunk)
            removed += cur.rowcount
        self._c.commit()
        return removed

    def delete_missing(self, folder: str) -> int:
        """Remove DB rows for files that no longer exist on disk."""
        import os
        from concurrent.futures import ThreadPoolExecutor
        rows = self._c.execute(
            "SELECT filepath FROM images WHERE folder=?", (folder,)).fetchall()
        if not rows:
            return 0
        paths = [r[0] for r in rows]
        with ThreadPoolExecutor(max_workers=10) as ex:
            missing = [p for p, ok in zip(paths, ex.map(os.path.isfile, paths)) if not ok]
        if missing:
            # Batch delete in one statement instead of N individual deletes
            placeholders = ",".join("?" * len(missing))
            self._c.execute(
                f"DELETE FROM images WHERE filepath IN ({placeholders})", missing)
            self._c.commit()
        return len(missing)

    # ── Read — metadata (no BLOBs) ───────────────────────────────────────────

    def get(self, filepath: str) -> dict | None:
        """Return full row including thumbnail BLOB (joined)."""
        row = self._c.execute(
            "SELECT i.*, t.data AS thumbnail "
            "FROM images i LEFT JOIN thumbnails t ON t.image_id=i.id "
            "WHERE i.filepath=?", (filepath,)).fetchone()
        return dict(row) if row else None

    def get_thumbnail(self, filepath: str) -> bytes | None:
        """Return only the thumbnail BLOB for a file."""
        row = self._c.execute(
            "SELECT t.data FROM images i "
            "JOIN thumbnails t ON t.image_id=i.id "
            "WHERE i.filepath=?", (filepath,)).fetchone()
        return bytes(row[0]) if row else None

    def images_in_folder(self, folder: str,
                         sort: str = "name",  sort_dir: str = "asc",
                         sort2: str = "",     sort2_dir: str = "asc",
                         sort3: str = "",     sort3_dir: str = "asc",
                         with_thumbnails: bool = True) -> list[dict]:
        import re as _re

        _COLS = {
            "name":         "i.filename COLLATE NOCASE",
            "numeric name": "i.filename COLLATE NOCASE",
            "date":         "i.added_at",
            "size":         "i.filesize",
            "modified":     "i.modified_at",
            "rating":       "i.rating",
        }

        def _order_clause(key: str, direction: str) -> str:
            col = _COLS.get(key, "i.filename COLLATE NOCASE")
            d   = "DESC" if direction == "desc" else "ASC"
            return f"{col} {d}"

        parts = [_order_clause(sort, sort_dir)]
        if sort2 and sort2 != sort:
            parts.append(_order_clause(sort2, sort2_dir))
        if sort3 and sort3 not in (sort, sort2):
            parts.append(_order_clause(sort3, sort3_dir))
        order = ", ".join(parts)

        if with_thumbnails:
            sql = (f"SELECT i.*, t.data AS thumbnail "
                   f"FROM images i LEFT JOIN thumbnails t ON t.image_id=i.id "
                   f"WHERE i.folder=? ORDER BY {order}")
        else:
            sql = f"SELECT i.* FROM images i WHERE i.folder=? ORDER BY {order}"

        rows = [dict(r) for r in self._c.execute(sql, (folder,)).fetchall()]

        def _nat_key(r):
            parts = _re.split(r'(\d+)', r["filename"].lower())
            return [int(p) if p.isdigit() else p for p in parts]

        rev = sort_dir == "desc"
        if sort == "numeric name":
            rows.sort(key=_nat_key, reverse=rev)
        elif sort2 == "numeric name":
            rows.sort(key=_nat_key, reverse=sort2_dir == "desc")
        elif sort3 == "numeric name":
            rows.sort(key=_nat_key, reverse=sort3_dir == "desc")

        return rows

    def get_thumbnail(self, filepath: str) -> bytes | None:
        """Return raw thumbnail BLOB for filepath, or None if not stored."""
        row = self._c.execute(
            "SELECT t.data FROM images i JOIN thumbnails t ON t.image_id=i.id "
            "WHERE i.filepath=?", (filepath,)).fetchone()
        return bytes(row[0]) if row else None

    def cached_filepaths(self, folder: str) -> dict[str, tuple[float, str, bool]]:
        """Return {filepath: (modified_at, file_hash, has_thumb)} for all cached rows in folder."""
        rows = self._c.execute(
            "SELECT i.filepath, i.modified_at, i.file_hash, (t.image_id IS NOT NULL) "
            "FROM images i LEFT JOIN thumbnails t ON t.image_id=i.id "
            "WHERE i.folder=?",
            (folder,)).fetchall()
        return {r[0]: (r[1], r[2] or "", bool(r[3])) for r in rows}

    def cached_filepaths_recursive(self, root: str) -> dict[str, tuple[float, str, bool]]:
        """Return {filepath: (modified_at, file_hash, has_thumb)} for root and all sub-folders."""
        prefix = root.rstrip("/\\") + "\\"
        rows = self._c.execute(
            "SELECT i.filepath, i.modified_at, i.file_hash, (t.image_id IS NOT NULL) "
            "FROM images i LEFT JOIN thumbnails t ON t.image_id=i.id "
            "WHERE i.folder=? OR i.folder LIKE ?",
            (root, prefix + "%")).fetchall()
        return {r[0]: (r[1], r[2] or "", bool(r[3])) for r in rows}

    # ── Faceted filter (digiKam-style: combine rating/label/model/sampler/tag/text) ──────────────
    def filter_images(self, folder: str = "", recursive: bool = False,
                      rating_min: int = 0, rating_op: str = ">=", label=None,
                      model: str = "", sampler: str = "", source: str = "",
                      tag: str = "", text: str = "",
                      sort: str = "name", sort_dir: str = "asc",
                      with_thumbnails: bool = True, limit: int = 5000) -> list[dict]:
        """Filter the grid by any combination of facets — empty/None facets are ignored, all others AND.
          rating_min : rating >= N            label   : exact pick/reject (0/1/2)
          model/sampler/source : exact match  tag     : substring in the tags column
          text       : FTS5 match (filename/prompt/negative/model/seed/sampler/tags)
        Every facet maps to an indexed column, so this stays fast at 100K+ rows."""
        conds: list[str] = []
        params: list = []
        if folder:
            if recursive:
                conds.append("(i.folder=? OR i.folder LIKE ?)")
                params += [folder, folder.rstrip("/\\") + "\\%"]
            else:
                conds.append("i.folder=?"); params.append(folder)
        if rating_min:
            conds.append("i.rating = ?" if rating_op == "=" else "i.rating >= ?")
            params.append(int(rating_min))
        if label is not None:
            conds.append("i.label = ?"); params.append(int(label))
        if model:
            conds.append("i.model = ?"); params.append(model)
        if sampler:
            conds.append("i.sampler = ?"); params.append(sampler)
        if source:
            conds.append("i.source = ?"); params.append(source)
        if tag:
            conds.append("i.tags LIKE ?"); params.append(f"%{tag}%")
        fts_join = ""
        if text.strip():
            fts_join = "JOIN images_fts f ON f.rowid = i.id"
            conds.append("images_fts MATCH ?")
            params.append(" ".join(f'"{w}"' for w in text.split()))
        _ORD = {"name": "i.filename COLLATE NOCASE", "date": "i.added_at", "size": "i.filesize",
                "modified": "i.modified_at", "rating": "i.rating", "label": "i.label"}
        order = _ORD.get(sort, "i.filename COLLATE NOCASE") + (" DESC" if sort_dir == "desc" else " ASC")
        thumb_sel  = ", t.data AS thumbnail" if with_thumbnails else ""
        thumb_join = "LEFT JOIN thumbnails t ON t.image_id = i.id" if with_thumbnails else ""
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        sql = (f"SELECT i.*{thumb_sel} FROM images i {fts_join} {thumb_join}{where} "
               f"ORDER BY {order} LIMIT ?")
        params.append(int(limit))
        try:
            return [dict(r) for r in self._c.execute(sql, params).fetchall()]
        except Exception:
            return []

    def all_folders(self) -> list[str]:
        rows = self._c.execute(
            "SELECT DISTINCT folder FROM images ORDER BY folder").fetchall()
        return [r[0] for r in rows]

    def close(self):
        try:
            self._c.execute("PRAGMA optimize")
            # PASSIVE: checkpoint without waiting for readers — TRUNCATE blocks
            # indefinitely if a terminated scan thread still holds a WAL lock.
            self._c.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass
        # Close every per-thread connection that was opened during the session.
        with self._conns_lock:
            conns = list(self._all_conns)
            self._all_conns.clear()
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
