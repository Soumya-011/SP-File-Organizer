"""
Persistent cache layer — avoids recomputing expensive results across sessions.

Three cache subsystems in one SQLite database:
  1. Thumbnail cache  — base64-encoded JPEG thumbnails keyed by path + mtime + size
  2. Hash cache       — perceptual/partial/full hashes keyed by path + mtime + size
  3. Trash index      — origin lookup for trashed files (replaces .trash_origin_index.json)
  4. History manifest  — run log metadata summary (replaces full directory scan)

All caches use (file_path, mtime, size) as the composite key so stale entries
are automatically ignored when a file changes or is replaced.
"""

import json
import sqlite3
import threading
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Database singleton (thread-safe)
# ---------------------------------------------------------------------------
_DB_PATH = None
_DB_LOCK = threading.Lock()

# Cache TTL: entries older than this (seconds) are considered stale.
# 0 = never expire (rely solely on mtime/size key).
_THUMBNAIL_TTL = 0
_HASH_TTL = 0


def _get_db_path() -> Path:
    global _DB_PATH
    if _DB_PATH is None:
        raise RuntimeError("cache_store not initialized — call init_cache_db() first.")
    return _DB_PATH


def init_cache_db(folder: Path):
    """Create/open the cache database inside the primary folder's log directory."""
    global _DB_PATH
    from utils import LOG_DIR_NAME
    log_dir = folder / LOG_DIR_NAME
    log_dir.mkdir(exist_ok=True)
    _DB_PATH = log_dir / ".cache_store.db"
    _ensure_tables()


def _get_conn() -> sqlite3.Connection:
    db_path = _get_db_path()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8192")  # 8 MB page cache
    return conn


def _ensure_tables():
    with _DB_LOCK:
        conn = _get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS thumbnail_cache (
                    file_path TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    b64_data TEXT NOT NULL,
                    PRIMARY KEY (file_path, mtime, size)
                );

                CREATE TABLE IF NOT EXISTS hash_cache (
                    file_path TEXT NOT NULL,
                    hash_type TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    hash_value TEXT NOT NULL,
                    PRIMARY KEY (file_path, hash_type, mtime, size)
                );

                CREATE TABLE IF NOT EXISTS trash_index (
                    trash_path TEXT PRIMARY KEY,
                    original_source TEXT
                );

                CREATE TABLE IF NOT EXISTS history_manifest (
                    log_path TEXT PRIMARY KEY,
                    timestamp TEXT,
                    count INTEGER,
                    label TEXT,
                    is_undone INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_hash_type ON hash_cache (hash_type);
                CREATE INDEX IF NOT EXISTS idx_thumb_mtime ON thumbnail_cache (mtime);
                CREATE INDEX IF NOT EXISTS idx_hist_label ON history_manifest (label);
            """)
            conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Thumbnail cache (#3)
# ---------------------------------------------------------------------------
def get_cached_thumb(file_path: Path, mtime: float, size: int) -> str:
    """Return cached base64 thumbnail or empty string."""
    with _DB_LOCK:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT b64_data FROM thumbnail_cache WHERE file_path=? AND mtime=? AND size=?",
                (str(file_path), mtime, size)
            ).fetchone()
            conn.close()
            return row[0] if row else ""
        except Exception:
            return ""


def put_cached_thumb(file_path: Path, mtime: float, size: int, b64_data: str):
    """Store a base64 thumbnail in the cache."""
    if not b64_data:
        return
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO thumbnail_cache (file_path, mtime, size, b64_data) VALUES (?,?,?,?)",
                (str(file_path), mtime, size, b64_data)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def get_cached_thumbs_batch(entries: list) -> dict:
    """Batch-fetch thumbnails. entries = [(path_str, mtime, size), ...].
    Returns {path_str: b64_data}."""
    if not entries:
        return {}
    with _DB_LOCK:
        try:
            conn = _get_conn()
            rows = conn.execute(
                f"SELECT file_path, b64_data FROM thumbnail_cache WHERE "
                f"({' OR '.join('(file_path=? AND mtime=? AND size=?)' for _ in entries)})",
                tuple(v for e in entries for v in e)
            ).fetchall()
            conn.close()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}


def put_cached_thumbs_batch(entries: list):
    """Batch-store thumbnails. entries = [(path_str, mtime, size, b64_data), ...]."""
    if not entries:
        return
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.executemany(
                "INSERT OR REPLACE INTO thumbnail_cache (file_path, mtime, size, b64_data) VALUES (?,?,?,?)",
                entries
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def prune_stale_thumbs(valid_keys: set):
    """Remove thumbnail entries whose (path, mtime, size) key is no longer valid."""
    with _DB_LOCK:
        try:
            conn = _get_conn()
            # Delete entries not in valid_keys
            if valid_keys:
                placeholders = ",".join("?" * len(valid_keys))
                conn.execute(
                    f"DELETE FROM thumbnail_cache WHERE (file_path || '|' || mtime || '|' || size) NOT IN "
                    f"(SELECT file_path || '|' || mtime || '|' || size FROM thumbnail_cache WHERE "
                    f"(file_path || '|' || mtime || '|' || size) IN ({placeholders}))",
                    list(valid_keys)
                )
            else:
                conn.execute("DELETE FROM thumbnail_cache")
            conn.commit()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Hash cache (#4)
# ---------------------------------------------------------------------------
def get_cached_hash(file_path: Path, hash_type: str, mtime: float, size: int):
    """Return cached hash value or None."""
    with _DB_LOCK:
        try:
            conn = _get_conn()
            row = conn.execute(
                "SELECT hash_value FROM hash_cache WHERE file_path=? AND hash_type=? AND mtime=? AND size=?",
                (str(file_path), hash_type, mtime, size)
            ).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None


def get_cached_hashes_batch(entries: list) -> dict:
    """Batch-fetch hashes. entries = [(path_str, hash_type, mtime, size), ...].
    Returns {(path_str, hash_type): hash_value}."""
    if not entries:
        return {}
    with _DB_LOCK:
        try:
            conn = _get_conn()
            condition = " OR ".join("(file_path=? AND hash_type=? AND mtime=? AND size=?)" for _ in entries)
            rows = conn.execute(
                f"SELECT file_path, hash_type, hash_value FROM hash_cache WHERE {condition}",
                tuple(v for e in entries for v in e)
            ).fetchall()
            conn.close()
            return {(r[0], r[1]): r[2] for r in rows}
        except Exception:
            return {}


def put_cached_hash(file_path: Path, hash_type: str, mtime: float, size: int, hash_value):
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO hash_cache (file_path, hash_type, mtime, size, hash_value) VALUES (?,?,?,?,?)",
                (str(file_path), hash_type, mtime, size, str(hash_value))
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def put_cached_hashes_batch(entries: list):
    """Batch-store hashes. entries = [(path_str, hash_type, mtime, size, hash_value), ...]."""
    if not entries:
        return
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.executemany(
                "INSERT OR REPLACE INTO hash_cache (file_path, hash_type, mtime, size, hash_value) VALUES (?,?,?,?,?)",
                [(e[0], e[1], e[2], e[3], str(e[4])) for e in entries]
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Trash index (#7) — replaces .trash_origin_index.json
# ---------------------------------------------------------------------------
def get_trash_origins() -> dict:
    """Return {trash_path_str: original_source_str}."""
    with _DB_LOCK:
        try:
            conn = _get_conn()
            rows = conn.execute("SELECT trash_path, original_source FROM trash_index").fetchall()
            conn.close()
            return {r[0]: r[1] for r in rows}
        except Exception:
            return {}


def update_trash_index(run_log: list):
    """Incrementally add new trash entries from a run log."""
    if not run_log:
        return
    from utils import TRASH_DIR_NAME, LOG_DIR_NAME
    folder = _get_db_path().parent.parent  # log_dir's parent = folder
    trash_root = str(folder / TRASH_DIR_NAME)
    entries = []
    for entry in run_log:
        dest = entry.get("destination", "")
        if dest.startswith(trash_root):
            entries.append((dest, entry.get("source", "")))
    if not entries:
        return
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.executemany(
                "INSERT OR REPLACE INTO trash_index (trash_path, original_source) VALUES (?,?)",
                entries
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def rebuild_trash_index_from_logs(folder: Path):
    """Full rebuild from run logs (fallback if DB is empty)."""
    from utils import TRASH_DIR_NAME, LOG_DIR_NAME
    trash_root = str(folder / TRASH_DIR_NAME)
    log_dir = folder / LOG_DIR_NAME
    lookup = {}
    if not log_dir.exists():
        return lookup
    for log_path in log_dir.glob("run_*.json"):
        if log_path.suffix != ".json" or log_path.stem.startswith("."):
            continue
        try:
            entries = json.loads(log_path.read_text())
        except Exception:
            continue
        for entry in entries:
            dest = entry.get("destination", "")
            if dest.startswith(trash_root):
                lookup[dest] = entry.get("source")
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.executemany(
                "INSERT OR REPLACE INTO trash_index (trash_path, original_source) VALUES (?,?)",
                [(k, v) for k, v in lookup.items()]
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    return lookup


# ---------------------------------------------------------------------------
# History manifest (#9)
# ---------------------------------------------------------------------------
def get_history_manifest() -> list:
    """Return cached history entries [{log_path, timestamp, count, label, is_undone}]."""
    with _DB_LOCK:
        try:
            conn = _get_conn()
            rows = conn.execute(
                "SELECT log_path, timestamp, count, label, is_undone FROM history_manifest ORDER BY timestamp DESC"
            ).fetchall()
            conn.close()
            return [{"log_path": r[0], "timestamp": r[1], "count": r[2], "label": r[3], "is_undone": r[4]}
                    for r in rows]
        except Exception:
            return []


def update_history_manifest(log_path: str, timestamp: str, count: int, label: str):
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.execute(
                "INSERT OR REPLACE INTO history_manifest (log_path, timestamp, count, label, is_undone) VALUES (?,?,?,?,0)",
                (log_path, timestamp, count, label)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def mark_history_undone(log_path: str):
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.execute("UPDATE history_manifest SET is_undone=1 WHERE log_path=?", (log_path,))
            conn.commit()
            conn.close()
        except Exception:
            pass


def rebuild_history_manifest(folder: Path):
    """Full rebuild from run logs on disk."""
    from utils import LOG_DIR_NAME
    log_dir = folder / LOG_DIR_NAME
    if not log_dir.exists():
        return []
    entries = []
    for p in sorted(log_dir.glob("run_*.json"), reverse=True):
        if p.name.endswith(".undone.json"):
            continue
        try:
            data = json.loads(p.read_text())
        except Exception:
            data = []
        parts = p.stem.split("_")
        timestamp = None
        if len(parts) >= 3:
            try:
                timestamp = datetime.strptime(f"{parts[1]}_{parts[2]}", "%Y%m%d_%H%M%S")
            except ValueError:
                pass
        label = timestamp.strftime("%Y-%m-%d %H:%M:%S") if timestamp else p.stem
        entries.append((str(p), str(timestamp or ""), len(data), label))
    with _DB_LOCK:
        try:
            conn = _get_conn()
            conn.execute("DELETE FROM history_manifest")
            conn.executemany(
                "INSERT OR REPLACE INTO history_manifest (log_path, timestamp, count, label, is_undone) VALUES (?,?,?,?,0)",
                entries
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    return entries


def _file_key(path_str: str, mtime: float, size: int) -> str:
    """Composite key for cache lookups."""
    return f"{path_str}|{mtime}|{size}"
