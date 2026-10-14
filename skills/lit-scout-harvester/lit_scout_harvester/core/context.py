"""Pipeline context: per-record data bus."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from lit_scout_harvester.core.cache_manager import CacheManager


def _join_list(value: List[Any], separator: str) -> str:
    """Join a list of scalar values into a CSV-safe string."""
    parts = [str(v).strip() for v in value if v is not None and str(v).strip()]
    return separator.join(parts)


def _flatten_author_addresses(
    addresses: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Flatten a list of author-address dicts into CSV-friendly columns.

    Input dicts are expected to contain keys such as ``author``, ``full_name``,
    ``country``, ``city``, ``institution``.
    """
    flat: Dict[str, Any] = {
        "authors": "",
        "countries": "",
        "cities": "",
        "institutions": "",
        "first_author_country": "",
        "first_author_city": "",
        "first_author_institution": "",
    }
    if not addresses:
        return flat

    authors = []
    countries = []
    cities = []
    institutions = []

    for addr in addresses:
        if not isinstance(addr, dict):
            continue
        name = addr.get("author") or addr.get("full_name")
        if name:
            authors.append(str(name))
        country = addr.get("country")
        if country:
            countries.append(str(country))
        city = addr.get("city")
        if city:
            cities.append(str(city))
        institution = addr.get("institution")
        if institution:
            institutions.append(str(institution))

    def unique(items: List[str]) -> List[str]:
        seen = set()
        result = []
        for item in items:
            key = item.lower()
            if key not in seen:
                seen.add(key)
                result.append(item)
        return result

    flat["authors"] = "; ".join(authors)
    flat["countries"] = "; ".join(unique(countries))
    flat["cities"] = "; ".join(unique(cities))
    flat["institutions"] = "; ".join(unique(institutions))

    first = addresses[0] if isinstance(addresses[0], dict) else {}
    flat["first_author_country"] = str(first.get("country", ""))
    flat["first_author_city"] = str(first.get("city", ""))
    flat["first_author_institution"] = str(first.get("institution", ""))
    return flat


def _serialize_value(value: Any, dict_serialization: str) -> Any:
    """Serialize a non-list value for tabular output."""
    if isinstance(value, dict):
        if dict_serialization == "json":
            return json.dumps(value, ensure_ascii=False)
        if dict_serialization == "flatten":
            return _flatten_dict(value)
        if dict_serialization == "omit":
            return None
    return value


def _flatten_dict(value: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten a nested dict using dot notation."""
    flat: Dict[str, Any] = {}
    for key, val in value.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(val, dict):
            flat.update(_flatten_dict(val, full_key))
        else:
            flat[full_key] = val
    return flat


class RecordCountInvariantError(RuntimeError):
    """记录数守恒不变量被违反（C12）：某阶段输出行数 != 输入 distinct ID 数。"""


def find_duplicate_ids(record_ids: Any) -> Dict[str, int]:
    """Return ``{id_value: occurrence_count}`` for ids appearing more than once."""
    counts: Dict[str, int] = {}
    for rid in record_ids:
        key = str(rid)
        counts[key] = counts.get(key, 0) + 1
    return {rid: n for rid, n in counts.items() if n > 1}


def assert_record_invariant(record_ids: Any, *, stage: str) -> None:
    """Assert ``rows_out == distinct input ids`` for a stage, else raise (C12).

    Compares the row count against the number of distinct record ids and, on
    violation, names the duplicated id values together with before/after
    dedup counts so the offending records can be located immediately.
    """
    ids = [str(rid) for rid in record_ids]
    duplicates = find_duplicate_ids(ids)
    if duplicates:
        distinct = len(ids) - sum(n - 1 for n in duplicates.values())
        detail = ", ".join(f"{rid!r} x{n}" for rid, n in sorted(duplicates.items()))
        raise RecordCountInvariantError(
            f"[{stage}] record conservation violated: "
            f"rows_in={len(ids)}, distinct_ids_after_dedup={distinct} "
            f"(output rows must equal input distinct ids); "
            f"duplicated record_id values: {detail}"
        )


class PipelineContext:
    """Data bus for a single literature record moving through the pipeline.

    Attributes:
        record_id: Unique identifier (UT, DOI, or title hash).
        raw: Original parsed WOS fields.
        processed: Skeleton processing results (translations, language, etc.).
        extracted: Module extraction results.
        confidence: Field-level confidence scores.
        flags: Review flags (e.g. ``needs_review``).
    """

    def __init__(
        self,
        record_id: str,
        raw_record: Dict[str, Any],
        cache: CacheManager,
    ):
        self.record_id = record_id
        self.raw = raw_record
        self.processed: Dict[str, Any] = {}
        self.extracted: Dict[str, Any] = {}
        self.confidence: Dict[str, float] = {}
        self.flags: List[str] = []
        self._cache = cache

    def cache_get(self, content_hash: str) -> Optional[str]:
        """Query the SQLite cache for a content hash."""
        return self._cache.get(content_hash)

    def cache_set(self, content_hash: str, result: str) -> None:
        """Write a result to the SQLite cache keyed by content hash."""
        self._cache.set(content_hash, result)

    def set_processed(self, key: str, value: Any, confidence: Optional[float] = None) -> None:
        """Append a processed field and optionally its confidence."""
        self.processed[key] = value
        if confidence is not None:
            self.confidence[key] = confidence

    def set_extracted(self, key: str, value: Any, confidence: Optional[float] = None) -> None:
        """Append an extracted field and optionally its confidence."""
        self.extracted[key] = value
        if confidence is not None:
            self.confidence[key] = confidence

    def add_flag(self, flag: str) -> None:
        """Add a review flag if it is not already present."""
        if flag not in self.flags:
            self.flags.append(flag)

    def to_flat_dict(
        self,
        include_raw: bool = False,
        include_confidence: bool = True,
        include_flags: bool = True,
        list_separator: str = "; ",
        flatten_author_addresses: bool = True,
        dict_serialization: str = "json",
        legacy_mode: bool = False,
    ) -> Dict[str, Any]:
        """Flatten the context to a dictionary suitable for tabular output."""
        row: Dict[str, Any] = {"record_id": self.record_id}

        if legacy_mode:
            if include_raw:
                for key, value in self.raw.items():
                    row[f"raw_{key}"] = value
            row.update(self.processed)
            row.update(self.extracted)
            if include_confidence:
                for key, score in self.confidence.items():
                    row[f"{key}_confidence"] = score
            if include_flags:
                row["flags"] = "; ".join(self.flags)
            return row

        if include_raw:
            for key, value in self.raw.items():
                raw_key = f"raw_{key}"
                if isinstance(value, list):
                    row[raw_key] = _join_list(value, list_separator)
                elif isinstance(value, dict):
                    row[raw_key] = _serialize_value(value, dict_serialization)
                else:
                    row[raw_key] = value

        def add_field(key: str, value: Any) -> None:
            if key == "author_addresses" and flatten_author_addresses and isinstance(value, list):
                flat = _flatten_author_addresses(value)
                row.update(flat)
                # Also keep a serialized raw version if raw fields are requested.
                if include_raw:
                    row["raw_author_addresses"] = _serialize_value(value, dict_serialization)
                return

            if isinstance(value, list):
                row[key] = _join_list(value, list_separator)
            elif isinstance(value, dict):
                if dict_serialization == "flatten":
                    row.update(_flatten_dict(value, prefix=key))
                elif dict_serialization == "json":
                    row[key] = json.dumps(value, ensure_ascii=False)
                # "omit" drops the field.
            else:
                row[key] = value

        for key, value in self.processed.items():
            add_field(key, value)
        for key, value in self.extracted.items():
            add_field(key, value)

        if include_confidence:
            for key, score in self.confidence.items():
                row[f"{key}_confidence"] = score
        if include_flags:
            row["flags"] = "; ".join(self.flags)
        return row


class ContextFactory:
    """Create PipelineContext instances from parsed WOS records."""

    def __init__(self, cache: CacheManager):
        self.cache = cache

    @staticmethod
    def _make_record_id(raw_record: Dict[str, Any]) -> str:
        """Create a stable record id from UT, DOI, or title hash."""
        if raw_record.get("UT"):
            return str(raw_record["UT"])
        if raw_record.get("DI"):
            return str(raw_record["DI"])
        title = raw_record.get("TI", "")
        return hashlib.sha256(str(title).encode("utf-8")).hexdigest()[:16]

    def create(self, raw_record: Dict[str, Any]) -> PipelineContext:
        return PipelineContext(
            record_id=self._make_record_id(raw_record),
            raw_record=raw_record,
            cache=self.cache,
        )
