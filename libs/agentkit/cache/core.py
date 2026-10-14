"""SQLite-backed content hash cache (skill-agnostic).

Extracted from Lit-Scout-Harvester ``core/cache_manager.py`` in LangHuan
Phase 2 P1a. The cache stores arbitrary string results keyed by a SHA256
content hash. It is thread-safe and Windows-handle-safe (WAL journal,
``contextlib.closing`` around a fresh connection per operation).

Pure stdlib.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union


class CacheManager:
    """Manage a SQLite content cache keyed by content hash.

    Args:
        db_path: Path to the SQLite database file. Parent directories are created
            automatically.
        table: Name of the cache table. Defaults to ``content_cache``.
    """

    def __init__(self, db_path: Union[str, Path], table: str = "content_cache"):
        self.db_path = Path(db_path)
        self.table = table
        self._lock = threading.RLock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create parent directory, open connection, and initialise schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # ``closing`` is required: sqlite3.Connection used as a context manager
        # only commits, it never closes, which leaks handles and keeps the file
        # locked on Windows.
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    content_hash TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS idx_{self.table}_hash
                ON {self.table}(content_hash)
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        """Return a new SQLite connection."""
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def get(self, content_hash: str) -> Optional[str]:
        """Return the cached value for ``content_hash`` or ``None``."""
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT value FROM {self.table} WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            return row[0] if row else None

    def set(self, content_hash: str, value: str) -> None:
        """Store ``value`` under ``content_hash``."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table}(content_hash, value, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(content_hash) DO UPDATE SET
                    value = excluded.value,
                    created_at = excluded.created_at
                """,
                (content_hash, value, now),
            )
            conn.commit()

    def delete(self, content_hash: str) -> bool:
        """Remove a single cache entry. Returns ``True`` if it existed."""
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(
                f"DELETE FROM {self.table} WHERE content_hash = ?",
                (content_hash,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def clear(self) -> int:
        """Remove all entries from the cache table. Returns number of rows deleted."""
        with self._lock, closing(self._connect()) as conn:
            cursor = conn.execute(f"DELETE FROM {self.table}")
            conn.commit()
            return cursor.rowcount

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics.

        Includes:
        - ``entries``: number of cached rows
        - ``total_value_bytes``: approximate total size of cached values
        - ``db_size_bytes``: size of the SQLite database file on disk
        """
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*), COALESCE(SUM(LENGTH(value)), 0)
                FROM {self.table}
                """
            ).fetchone()

        entries = row[0] if row else 0
        total_value_bytes = row[1] if row else 0
        db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "entries": entries,
            "total_value_bytes": total_value_bytes,
            "db_size_bytes": db_size_bytes,
        }

    def contains(self, content_hash: str) -> bool:
        """Return ``True`` if ``content_hash`` is present in the cache."""
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                f"SELECT 1 FROM {self.table} WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            return row is not None
