"""Institution-to-city mapping cache.

Maintains a manual mapping of institution names to cities and countries. This
is used as a fallback when address parsing fails to identify a city.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class InstitutionCityMap:
    """SQLite-backed institution-to-city lookup table."""

    def __init__(self, db_path: str = "data/inst_city_map.db"):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inst_city_map (
                    institution TEXT PRIMARY KEY,
                    city TEXT,
                    country TEXT,
                    source TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def get(self, institution: str) -> Optional[Dict[str, Optional[str]]]:
        """Return the mapped city/country for an institution, or None."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT city, country, source, updated_at FROM inst_city_map "
                "WHERE institution = ?",
                (institution,),
            ).fetchone()
        if row is None:
            return None
        return {
            "institution": institution,
            "city": row[0],
            "country": row[1],
            "source": row[2],
            "updated_at": row[3],
        }

    def set(
        self,
        institution: str,
        city: Optional[str] = None,
        country: Optional[str] = None,
        source: Optional[str] = None,
    ) -> None:
        """Add or update a mapping entry."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO inst_city_map(institution, city, country, source, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(institution) DO UPDATE SET
                    city = excluded.city,
                    country = excluded.country,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (institution, city, country, source, now),
            )
            conn.commit()

    def batch_update(self, entries: List[Dict[str, Any]]) -> int:
        """Bulk update mapping entries from a list of dicts."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            for entry in entries:
                conn.execute(
                    """
                    INSERT INTO inst_city_map(institution, city, country, source, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(institution) DO UPDATE SET
                        city = excluded.city,
                        country = excluded.country,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (
                        entry["institution"],
                        entry.get("city"),
                        entry.get("country"),
                        entry.get("source", "batch"),
                        now,
                    ),
                )
            conn.commit()
        return len(entries)

    def stats(self) -> Dict[str, Any]:
        """Return table statistics."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COUNT(city), COUNT(country) FROM inst_city_map"
            ).fetchone()
        return {
            "entries": row[0],
            "with_city": row[1],
            "with_country": row[2],
        }
