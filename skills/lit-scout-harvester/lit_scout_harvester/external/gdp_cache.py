"""SQLite-backed cache for World Bank GDP data.

Stores both current-price GDP (NY.GDP.MKTP.CD) and constant-price GDP
(NY.GDP.MKTP.KD, base 2015 USD) per country and year.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# World Bank indicator codes
GDP_CURRENT_INDICATOR = "NY.GDP.MKTP.CD"
GDP_CONSTANT_INDICATOR = "NY.GDP.MKTP.KD"


class GDPCache:
    """Cache World Bank GDP data locally.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gdp (
                    country_code TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    gdp_current REAL,
                    gdp_constant REAL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (country_code, year)
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), check_same_thread=False)

    def get(
        self, country_code: str, year: int
    ) -> Optional[Dict[str, Any]]:
        """Return GDP data for ``country_code`` and ``year``.

        Returns a dict with ``gdp_current``, ``gdp_constant``, ``fetched_at``
        and ``year``, or ``None`` if not cached.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT gdp_current, gdp_constant, fetched_at FROM gdp "
                "WHERE country_code = ? AND year = ?",
                (country_code.upper(), year),
            ).fetchone()
        if row is None:
            return None
        return {
            "country_code": country_code.upper(),
            "year": year,
            "gdp_current": row[0],
            "gdp_constant": row[1],
            "fetched_at": row[2],
        }

    def set(
        self,
        country_code: str,
        year: int,
        gdp_current: Optional[float] = None,
        gdp_constant: Optional[float] = None,
    ) -> None:
        """Store GDP values for a country-year pair."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gdp(country_code, year, gdp_current, gdp_constant, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(country_code, year) DO UPDATE SET
                    gdp_current = excluded.gdp_current,
                    gdp_constant = excluded.gdp_constant,
                    fetched_at = excluded.fetched_at
                """,
                (country_code.upper(), year, gdp_current, gdp_constant, now),
            )
            conn.commit()

    def get_nearest(
        self,
        country_code: str,
        year: int,
        max_gap: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """Return the closest available GDP entry within ``max_gap`` years."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT year, gdp_current, gdp_constant, fetched_at,
                       ABS(year - ?) AS gap
                FROM gdp
                WHERE country_code = ? AND ABS(year - ?) <= ?
                ORDER BY gap ASC, year DESC
                LIMIT 1
                """,
                (year, country_code.upper(), year, max_gap),
            ).fetchone()
        if row is None:
            return None
        return {
            "country_code": country_code.upper(),
            "year": row[0],
            "gdp_current": row[1],
            "gdp_constant": row[2],
            "fetched_at": row[3],
            "year_gap": row[4],
        }

    def list_countries(self) -> List[str]:
        """Return all cached country codes."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT country_code FROM gdp ORDER BY country_code"
            ).fetchall()
        return [r[0] for r in rows]

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COUNT(gdp_current), COUNT(gdp_constant) FROM gdp"
            ).fetchone()
        return {
            "entries": row[0],
            "with_current": row[1],
            "with_constant": row[2],
        }
