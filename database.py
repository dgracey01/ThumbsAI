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
    def __init__(self):
        self._c = sqlite3.connect(str(THUMBS_DB), check_same_thread=False,
                                  timeout=5.0)
        self._c.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA synchronous=NORMAL")
        self._c.execute("PRAGMA cache_size=-64000")       # 64 MB page cache
        self._c.execute("PRAGMA mmap_size=536870912")     # 512 MB memory-map
        self._c.execute("PRAGMA temp_store=MEMORY")
        self._c.execute("PRAGMA busy_timeout=5000")       # wait up to 5 s on lock
        self._c.execute("PRAGMA wal_autocheckpoint=1000") # checkpoint every 1000 pages

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
        ]
        for col, coltype in additions:
            if col not in existing:
                self._c.execute(f"ALTER TABLE images ADD COLUMN {col} {coltype}")
        self._c.commit()

        # Ensure new indexes exist (safe to re-run — IF NOT EXISTS)
        self._c.executescript("""
            CREATE INDEX IF NOT EXISTS idx_folder_name ON images(folder, filename COLLATE NOCASE);
            CREATE INDEX IF NOT EXISTS idx_file_hash   ON images(file_hash);
        """)
        self._c.commit()

    def _migrate_fts5(self):
        """Create FTS5 full-text-search table + sync triggers (idempotent)."""
        tables = {r[0] for r in self._c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "images_fts" in tables:
            return
        self._c.executescript("""
            CREATE VIRTUAL TABLE images_fts USING fts5(
                filename, prompt, tags,
                content=images, content_rowid=id
            );
            -- Populate from existing rows
            INSERT INTO images_fts(rowid, filename, prompt, tags)
                SELECT id, coalesce(filename,''), coalesce(prompt,''),
                       coalesce(tags,'') FROM images;
            -- Keep in sync with images
            CREATE TRIGGER images_fts_ai AFTER INSERT ON images BEGIN
                INSERT INTO images_fts(rowid, filename, prompt, tags)
                VALUES (new.id, coalesce(new.filename,''),
                        coalesce(new.prompt,''), coalesce(new.tags,''));
            END;
            CREATE TRIGGER images_fts_ad AFTER DELETE ON images BEGIN
                INSERT INTO images_fts(images_fts, rowid, filename, prompt, tags)
                VALUES ('delete', old.id, coalesce(old.filename,''),
                        coalesce(old.prompt,''), coalesce(old.tags,''));
            END;
            CREATE TRIGGER images_fts_au AFTER UPDATE ON images BEGIN
                INSERT INTO images_fts(images_fts, rowid, filename, prompt, tags)
                VALUES ('delete', old.id, coalesce(old.filename,''),
                        coalesce(old.prompt,''), coalesce(old.tags,''));
                INSERT INTO images_fts(rowid, filename, prompt, tags)
                VALUES (new.id, coalesce(new.filename,''),
                        coalesce(new.prompt,''), coalesce(new.tags,''));
            END;
        """)
        self._c.commit()

    # ── Periodic maintenance ──────────────────────────────────────────────────

    def optimize(self):
        """Refresh query-planner statistics. Call once at startup or after bulk inserts."""
        self._c.execute("PRAGMA optimize")
        self._c.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def search(self, query: str, folder: str = "") -> list[dict]:
        """FTS5 full-text search across filename, prompt, tags. O(log N)."""
        q = query.strip()
        if not q:
            return []
        fts_q = " OR ".join(f'"{w}"' for w in q.split())
        sql = (
            "SELECT i.* FROM images i "
            "JOIN images_fts f ON f.rowid = i.id "
            "WHERE images_fts MATCH ?"
        )
        params: list = [fts_q]
        if folder:
            sql += " AND i.folder=?"
            params.append(folder)
        sql += " ORDER BY rank LIMIT 2000"
        try:
            rows = self._c.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
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

    def get_meta(self, filepath: str) -> dict | None:
        """Return metadata only — no thumbnail BLOB."""
        row = self._c.execute(
            "SELECT * FROM images WHERE filepath=?", (filepath,)).fetchone()
        return dict(row) if row else None

    def get_thumbnail(self, filepath: str) -> bytes | None:
        """Return only the thumbnail BLOB for a file."""
        row = self._c.execute(
            "SELECT t.data FROM images i "
            "JOIN thumbnails t ON t.image_id=i.id "
            "WHERE i.filepath=?", (filepath,)).fetchone()
        return bytes(row[0]) if row else None

    def get_modified(self, filepath: str) -> float | None:
        row = self._c.execute(
            "SELECT modified_at FROM images WHERE filepath=?",
            (filepath,)).fetchone()
        return row[0] if row else None

    def get_cache_key(self, filepath: str) -> tuple[float | None, str | None]:
        """Return (modified_at, file_hash) for fast invalidation checking."""
        row = self._c.execute(
            "SELECT modified_at, file_hash FROM images WHERE filepath=?",
            (filepath,)).fetchone()
        return (row[0], row[1]) if row else (None, None)

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

    def images_in_folder_recursive(self, root: str,
                                    sort: str = "name", sort2: str = "",
                                    with_thumbnails: bool = True) -> list[dict]:
        import re as _re

        _ORDERS = {
            "name":         "i.filename COLLATE NOCASE",
            "numeric name": "i.filename COLLATE NOCASE",
            "date":         "i.added_at DESC",
            "size":         "i.filesize DESC",
            "modified":     "i.modified_at DESC",
            "rating":       "i.rating DESC",
        }
        order = _ORDERS.get(sort, "i.filename COLLATE NOCASE")
        if sort2 and sort2 != sort:
            order += ", " + _ORDERS.get(sort2, "i.filename COLLATE NOCASE")

        prefix = root.rstrip("/\\") + "\\"

        if with_thumbnails:
            sql = (f"SELECT i.*, t.data AS thumbnail "
                   f"FROM images i LEFT JOIN thumbnails t ON t.image_id=i.id "
                   f"WHERE i.folder=? OR i.folder LIKE ? ORDER BY {order}")
        else:
            sql = (f"SELECT i.* FROM images i "
                   f"WHERE i.folder=? OR i.folder LIKE ? ORDER BY {order}")

        rows = [dict(r) for r in self._c.execute(
            sql, (root, prefix + "%")).fetchall()]

        def _nat_key(r):
            parts = _re.split(r'(\d+)', r["filename"].lower())
            return [int(p) if p.isdigit() else p for p in parts]

        if sort == "numeric name":
            rows.sort(key=_nat_key)
        elif sort2 == "numeric name":
            rows.sort(key=_nat_key)

        return rows

    def search(self, query: str, folder: str = "") -> list[dict]:
        q = f"%{query}%"
        if folder:
            rows = self._c.execute(
                "SELECT i.*, t.data AS thumbnail "
                "FROM images i LEFT JOIN thumbnails t ON t.image_id=i.id "
                "WHERE i.folder=? AND "
                "(i.filename LIKE ? OR i.prompt LIKE ? OR i.model LIKE ? "
                " OR i.seed LIKE ? OR i.sampler LIKE ? OR i.tags LIKE ?) "
                "ORDER BY i.filename COLLATE NOCASE",
                (folder, q, q, q, q, q, q)).fetchall()
        else:
            rows = self._c.execute(
                "SELECT i.*, t.data AS thumbnail "
                "FROM images i LEFT JOIN thumbnails t ON t.image_id=i.id "
                "WHERE i.filename LIKE ? OR i.prompt LIKE ? OR i.model LIKE ? "
                "OR i.seed LIKE ? OR i.sampler LIKE ? OR i.tags LIKE ? "
                "ORDER BY i.filename COLLATE NOCASE",
                (q, q, q, q, q, q)).fetchall()
        return [dict(r) for r in rows]

    def total_in_folder(self, folder: str) -> int:
        return self._c.execute(
            "SELECT COUNT(*) FROM images WHERE folder=?", (folder,)).fetchone()[0]

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
        self._c.close()
