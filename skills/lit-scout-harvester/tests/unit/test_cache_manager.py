"""Tests for src.core.cache_manager."""

from __future__ import annotations

import tempfile
from pathlib import Path

from lit_scout_harvester.core.cache_manager import CacheManager


def test_get_set_and_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "cache.db"
        cm = CacheManager(str(db))
        assert cm.get("missing") is None
        cm.set("hash1", "value1")
        assert cm.get("hash1") == "value1"
        cm.set("hash1", "value2")
        assert cm.get("hash1") == "value2"


def test_contains_and_delete():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "cache.db"
        cm = CacheManager(str(db))
        cm.set("h", "v")
        assert cm.contains("h") is True
        assert cm.delete("h") is True
        assert cm.contains("h") is False
        assert cm.delete("h") is False


def test_clear_and_stats():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "cache.db"
        cm = CacheManager(str(db))
        for i in range(5):
            cm.set(f"hash{i}", f"value{i}")
        stats = cm.stats()
        assert stats["entries"] == 5
        assert stats["total_value_bytes"] > 0
        deleted = cm.clear()
        assert deleted == 5
        assert cm.stats()["entries"] == 0


def test_parent_directory_created():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "nested" / "dir" / "cache.db"
        cm = CacheManager(str(db))
        cm.set("x", "y")
        assert db.exists()
