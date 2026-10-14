"""Human review queue backed by SQLite.

Records flagged by the pipeline (parse errors, hard fields, etc.) plus a
fixed-rate random audit sample are placed into this queue for manual review.
Each item stores the raw record, extracted results, flags, per-field
confidence, and review status.

Enqueue policy (C5): three independent routes —

1. fixed-rate random audit sampling (``utils.audit_sampling``, seed stored
   in ``queue_meta`` so the batch is reproducible byte-for-byte);
2. full check of known hard fields (``caliber.audit.hard_fields``);
3. optional low-confidence mode (off by default).

``field_confidence`` semantics: per-field confidence is ONLY a queue-ordering
hint. It must not be used as an accept/reject gate — empirical calibration
from real annotation projects shows wrong answers still average 0.727
confidence (0.900 when right; gap a mere 0.173), and model self-assessment
front-questions score AUC 0.498 (random).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from lit_scout_harvester.core.config_loader import load_caliber
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.utils.audit_sampling import resolve_audit_rules, select_audit_sample

FLAG_AUDIT_SAMPLE = "audit_sample"
FLAG_LOW_CONFIDENCE = "low_confidence"

DEFAULT_CONFIDENCE_THRESHOLD = 0.3


class ReviewQueue:
    """Manage a queue of pipeline outputs pending human review.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str = "data/review_queue.db"):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._ensure_db()

    def _ensure_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS review_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT NOT NULL,
                    raw_record TEXT NOT NULL,
                    processed TEXT,
                    extracted TEXT,
                    flags TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewer_note TEXT,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    field_confidence TEXT,
                    min_confidence REAL
                )
                """
            )
            self._add_column_if_missing(conn, "review_queue", "field_confidence", "TEXT")
            self._add_column_if_missing(conn, "review_queue", "min_confidence", "REAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_record ON review_queue(record_id)"
            )
            conn.commit()

    @staticmethod
    def _add_column_if_missing(
        conn: sqlite3.Connection, table: str, column: str, decl: str
    ) -> None:
        cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Enqueue
    # ------------------------------------------------------------------

    def enqueue(
        self,
        ctx: PipelineContext,
        max_size: Optional[int] = None,
    ) -> Optional[int]:
        """Add a pipeline context to the review queue.

        Args:
            ctx: Pipeline context to enqueue.
            max_size: Optional maximum queue size. If set and the queue already
                contains ``max_size`` pending items, the oldest item is dropped.

        Returns:
            The new item id, or ``None`` if the record is already pending.
        """
        with self._lock, self._connect() as conn:
            return self._insert_ctx(conn, ctx, list(ctx.flags), max_size)

    def enqueue_contexts(
        self,
        contexts: Iterable[PipelineContext],
        *,
        audit_rate: Optional[float] = None,
        seed: Optional[int] = None,
        hard_fields: Optional[List[str]] = None,
        confidence_mode: bool = False,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        max_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Enqueue a batch of contexts via the three-route policy (C5).

        Routes:
            1. fixed-rate random audit sampling — reproducible via ``seed``,
               which is written into ``queue_meta``;
            2. full check of ``hard_fields`` (a record carrying any of them is
               enqueued); defaults to ``caliber.audit.hard_fields`` ([]);
            3. low-confidence mode — only when ``confidence_mode=True``
               (kept for backward compatibility, off by default). Confidence
               otherwise serves ONLY as an in-queue ordering hint via
               ``field_confidence`` / ``min_confidence``.

        A record already pending is skipped (first matching route wins for
        grouping; flags of all matched routes are unioned).

        Returns:
            ``{"audit_sample": [item_id], "hard_field": [...],
            "low_confidence": [...], "skipped": [record_id],
            "seed": seed, "rate": rate}``.
        """
        contexts = list(contexts)
        rate, seed = resolve_audit_rules(audit_rate, seed)
        if hard_fields is None:
            hard_fields = list(load_caliber().audit.hard_fields)

        sampled = set(select_audit_sample([c.record_id for c in contexts], rate, seed))
        hard_fields = [f for f in hard_fields if f]

        result: Dict[str, Any] = {
            "audit_sample": [],
            "hard_field": [],
            "low_confidence": [],
            "skipped": [],
            "seed": seed,
            "rate": rate,
        }

        with self._lock, self._connect() as conn:
            for ctx in contexts:
                flags = list(ctx.flags)
                primary: Optional[str] = None
                if ctx.record_id in sampled:
                    flags.append(FLAG_AUDIT_SAMPLE)
                    primary = "audit_sample"
                matched_hard = [f for f in hard_fields if f in ctx.extracted]
                if matched_hard:
                    flags.extend(f"hard_field:{f}" for f in matched_hard)
                    if primary is None:
                        primary = "hard_field"
                if confidence_mode and ctx.confidence:
                    if min(ctx.confidence.values()) < confidence_threshold:
                        flags.append(FLAG_LOW_CONFIDENCE)
                        if primary is None:
                            primary = "low_confidence"
                if primary is None:
                    continue
                if self._is_pending(conn, ctx.record_id):
                    result["skipped"].append(ctx.record_id)
                    continue
                item_id = self._insert_ctx(conn, ctx, flags, max_size)
                if item_id is not None:
                    result[primary].append(item_id)

            self._set_meta(conn, "audit_seed", str(seed))
            self._set_meta(conn, "audit_rate", str(rate))
            conn.commit()
        return result

    def _is_pending(self, conn: sqlite3.Connection, record_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM review_queue WHERE record_id = ? AND status = 'pending'",
            (record_id,),
        ).fetchone()
        return row is not None

    def _insert_ctx(
        self,
        conn: sqlite3.Connection,
        ctx: PipelineContext,
        flags: List[str],
        max_size: Optional[int],
    ) -> Optional[int]:
        if self._is_pending(conn, ctx.record_id):
            return None

        if max_size is not None:
            count = conn.execute(
                "SELECT COUNT(*) FROM review_queue WHERE status = 'pending'"
            ).fetchone()[0]
            if count >= max_size:
                oldest = conn.execute(
                    "SELECT id FROM review_queue WHERE status = 'pending' "
                    "ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if oldest:
                    conn.execute("DELETE FROM review_queue WHERE id = ?", (oldest[0],))

        now = datetime.now(timezone.utc).isoformat()
        field_confidence = json.dumps(ctx.confidence, ensure_ascii=False)
        min_confidence = min(ctx.confidence.values()) if ctx.confidence else None
        cursor = conn.execute(
            """
            INSERT INTO review_queue(
                record_id, raw_record, processed, extracted, flags, status,
                created_at, field_confidence, min_confidence
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                ctx.record_id,
                json.dumps(ctx.raw, ensure_ascii=False),
                json.dumps(ctx.processed, ensure_ascii=False),
                json.dumps(ctx.extracted, ensure_ascii=False),
                "; ".join(flags),
                now,
                field_confidence,
                min_confidence,
            ),
        )
        return cursor.lastrowid

    # ------------------------------------------------------------------
    # Queue metadata
    # ------------------------------------------------------------------

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO queue_meta(key, value) VALUES (?, ?)", (key, value)
        )

    def get_meta(self, key: str) -> Optional[str]:
        """Return a queue metadata value (e.g. ``audit_seed``), or None."""
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT value FROM queue_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # Read / update
    # ------------------------------------------------------------------

    def get(self, item_id: int) -> Optional[Dict[str, Any]]:
        """Return a single review item by id."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM review_queue WHERE id = ?", (item_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(
        self,
        status: Optional[str] = "pending",
        limit: int = 50,
        offset: int = 0,
        order_by_confidence: bool = False,
    ) -> List[Dict[str, Any]]:
        """List review items filtered by status.

        Args:
            order_by_confidence: sort by ``min_confidence`` ascending (NULLs
                last). Confidence is an ordering hint only, never a gate.
        """
        query = "SELECT * FROM review_queue"
        params: List[Any] = []
        if status is not None:
            query += " WHERE status = ?"
            params.append(status)
        if order_by_confidence:
            query += " ORDER BY (min_confidence IS NULL), min_confidence ASC, created_at DESC"
        else:
            query += " ORDER BY created_at DESC"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def update_status(
        self,
        item_id: int,
        status: str,
        reviewer_note: Optional[str] = None,
    ) -> bool:
        """Update the status of a review item."""
        if status not in {"pending", "approved", "rejected"}:
            raise ValueError(f"Invalid status: {status}")
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE review_queue
                SET status = ?, reviewer_note = ?, reviewed_at = ?
                WHERE id = ?
                """,
                (status, reviewer_note, now, item_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def delete(self, item_id: int) -> bool:
        """Remove a review item permanently."""
        with self._lock, self._connect() as conn:
            cursor = conn.execute("DELETE FROM review_queue WHERE id = ?", (item_id,))
            conn.commit()
            return cursor.rowcount > 0

    def stats(self) -> Dict[str, int]:
        """Return counts by status."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM review_queue GROUP BY status"
            ).fetchall()
        result = {"pending": 0, "approved": 0, "rejected": 0, "total": 0}
        for status, count in rows:
            result[status] = count
            result["total"] += count
        return result

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "record_id": row["record_id"],
            "raw_record": json.loads(row["raw_record"]),
            "processed": json.loads(row["processed"]) if row["processed"] else {},
            "extracted": json.loads(row["extracted"]) if row["extracted"] else {},
            "flags": row["flags"].split("; ") if row["flags"] else [],
            "status": row["status"],
            "reviewer_note": row["reviewer_note"],
            "created_at": row["created_at"],
            "reviewed_at": row["reviewed_at"],
            "field_confidence": json.loads(row["field_confidence"])
            if row["field_confidence"]
            else {},
            "min_confidence": row["min_confidence"],
        }
