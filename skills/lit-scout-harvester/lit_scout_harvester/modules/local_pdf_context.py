"""Local PDF library → bounded MinerU Markdown context for term extraction."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from agentkit.mineru import MinerUClient
from lit_scout_harvester.core.cache_manager import CacheManager


_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _bounded_int(value: Any, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(parsed, upper))


def _safe_identifier(record: Dict[str, Any]) -> str:
    value = str(record.get("DI") or record.get("UT") or "").strip()
    return _SAFE_ID_RE.sub("_", value).strip("._")


class LocalPDFContextProvider:
    """Resolve root-contained PDFs and cache Markdown by file fingerprint."""

    def __init__(self, client: MinerUClient, cache: CacheManager):
        self.client = client
        self.cache = cache

    def context_for(self, record: Dict[str, Any], rules: Dict[str, Any]) -> str:
        path = self._resolve_path(record, rules)
        if path is None:
            return ""
        stat = path.stat()
        mode = str(rules.get("mode") or "auto")
        cache_key = self._cache_key(path, stat.st_size, stat.st_mtime_ns, mode)
        markdown = self._read_cache(cache_key)
        if markdown is None:
            doc = self.client.parse_file(str(path), mode=mode)
            markdown = doc.markdown
            self.cache.set(cache_key, json.dumps({"markdown": markdown}, ensure_ascii=False))
        max_chars = _bounded_int(rules.get("max_context_chars"), 12000, 1, 100000)
        return markdown[:max_chars]

    def _resolve_path(self, record: Dict[str, Any], rules: Dict[str, Any]) -> Optional[Path]:
        root_value = str(rules.get("root") or "").strip()
        if not root_value:
            return None
        root = Path(root_value).expanduser().resolve()
        path_field = str(rules.get("path_field") or "PDF")
        relative = str(record.get(path_field) or "").strip()
        if not relative:
            safe_id = _safe_identifier(record)
            if not safe_id:
                return None
            pattern = str(rules.get("filename_pattern") or "{safe_id}.pdf")
            relative = pattern.format(safe_id=safe_id)
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            return None
        if not candidate.is_file() or candidate.suffix.lower() != ".pdf":
            return None
        return candidate

    @staticmethod
    def _cache_key(path: Path, size: int, mtime_ns: int, mode: str) -> str:
        payload = json.dumps(
            {"path": str(path), "size": size, "mtime_ns": mtime_ns, "mode": mode},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(f"local-pdf::{payload}".encode("utf-8")).hexdigest()

    def _read_cache(self, key: str) -> Optional[str]:
        cached = self.cache.get(key)
        if cached is None:
            return None
        try:
            payload = json.loads(cached)
        except json.JSONDecodeError:
            return None
        markdown = payload.get("markdown") if isinstance(payload, dict) else None
        return markdown if isinstance(markdown, str) else None


__all__ = ["LocalPDFContextProvider"]
