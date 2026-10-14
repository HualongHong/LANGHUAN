"""CNKI plaintext parser.

Supports two common CNKI export formats:
- **Refworks** tag-based format (RT / A1 / T1 / JF / YR / K1 / AB / ...).
- **Custom** Chinese-label format (``【题名】``, ``【作者】``, ...).

Records are yielded as dictionaries keyed by the same internal field codes used
for WOS records (``TI``, ``AU``, ``AB``, ``SO``, ``PY``, ...). CNKI-specific
fields such as fund and Chinese Library Classification are preserved with a
``raw_`` prefix.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union


class CNKIParseError(Exception):
    """Raised when a CNKI plaintext file cannot be parsed."""


# ---------------------------------------------------------------------------
# Refworks format
# ---------------------------------------------------------------------------

_REFWORKS_TAG_RE = re.compile(r"^([A-Z][A-Z0-9]{1,2}) (.*)$")

_REFWORKS_TAG_MAP = {
    "RT": "PT",  # reference type, mapped to publication type
    "A1": "AU",  # author
    "AD": "AF",  # affiliation
    "T1": "TI",  # title
    "JF": "SO",  # source
    "YR": "PY",  # year
    "FD": "PD",  # full date
    "K1": "DE",  # keywords
    "AB": "AB",  # abstract
    "LA": "LA",  # language
    "SN": "SN",  # ISSN
    "CN": "cn",  # CN domestic serial number (output layer will prefix raw_)
    "DO": "DI",  # DOI
    "CF": "raw_fund",  # funding
    "CLC": "raw_clc_class",  # Chinese Library Classification
}

_REFWORKS_LIST_TAGS = {"AU", "AF"}


# ---------------------------------------------------------------------------
# Custom Chinese-label format
# ---------------------------------------------------------------------------

_CUSTOM_LABEL_PATTERNS = [
    (re.compile(r"^[\s]*[【\[]?题名[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "TI"),
    (re.compile(r"^[\s]*[【\[]?标题[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "TI"),
    (re.compile(r"^[\s]*[【\[]?作者[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "AU"),
    (re.compile(r"^[\s]*[【\[]?机构[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "AF"),
    (re.compile(r"^[\s]*[【\[]?单位[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "AF"),
    (re.compile(r"^[\s]*[【\[]?来源[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "SO"),
    (re.compile(r"^[\s]*[【\[]?刊名[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "SO"),
    (re.compile(r"^[\s]*[【\[]?年[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "PY"),
    (re.compile(r"^[\s]*[【\[]?年份[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "PY"),
    (re.compile(r"^[\s]*[【\[]?关键词[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "DE"),
    (re.compile(r"^[\s]*[【\[]?摘要[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "AB"),
    (re.compile(r"^[\s]*[【\[]?语言[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "LA"),
    (re.compile(r"^[\s]*[【\[]?基金[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "raw_fund"),
    (re.compile(r"^[\s]*[【\[]?中图分类号[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "raw_clc_class"),
    (re.compile(r"^[\s]*[【\[]?DOI[】\]]?[\s]*(?:[:：]\s*)?(.*)$", re.UNICODE), "DI"),
]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _split_authors(value: str) -> List[str]:
    """Split a Chinese author string into individual names."""
    value = value.strip()
    if not value:
        return []
    # CNKI commonly uses semicolons or commas.
    if ";" in value:
        return [v.strip() for v in value.split(";") if v.strip()]
    if "，" in value:
        return [v.strip() for v in value.split("，") if v.strip()]
    if "," in value:
        return [v.strip() for v in value.split(",") if v.strip()]
    # Single author.
    return [value]


def _split_keywords(value: str) -> List[str]:
    """Split a keyword string into a list."""
    value = value.strip()
    if not value:
        return []
    # CNKI keywords are typically separated by semicolons or Chinese semicolons.
    separators = (";", "；", "，", ",")
    for sep in separators:
        if sep in value:
            return [v.strip() for v in value.split(sep) if v.strip()]
    return [value]


def _post_process(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize parsed CNKI values."""
    # Authors: ensure list.
    if "AU" in record and isinstance(record["AU"], str):
        record["AU"] = _split_authors(record["AU"])
    # Affiliations: ensure list.
    if "AF" in record and isinstance(record["AF"], str):
        record["AF"] = _split_authors(record["AF"])
    # Keywords: ensure list.
    if "DE" in record and isinstance(record["DE"], str):
        record["DE"] = _split_keywords(record["DE"])
    # Language normalization.
    if "LA" in record and isinstance(record["LA"], str):
        la = record["LA"].strip().lower()
        if "中" in la or la in ("chinese", "zh", "chi"):
            record["LA"] = "zh"
        elif la in ("english", "en", "eng"):
            record["LA"] = "en"
    return record


# ---------------------------------------------------------------------------
# Refworks parser
# ---------------------------------------------------------------------------

def _detect_format(lines: List[str]) -> str:
    """Detect whether the file is refworks or custom format."""
    for line in lines[:50]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("RT ") or stripped.startswith("SR "):
            return "refworks"
        if stripped.startswith("【") or stripped.startswith("["):
            return "custom"
    return "custom"


def _parse_refworks(lines: List[str]) -> Iterator[Dict[str, Any]]:
    """Parse a CNKI Refworks export."""
    record: Dict[str, Any] = {}
    current_tag: Optional[str] = None

    for raw_line in lines:
        line = raw_line.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue

        if not line[0].isspace():
            match = _REFWORKS_TAG_RE.match(line)
            if not match:
                continue
            tag, value = match.groups()
            if tag == "RT":
                # Start of a new record. Yield the previous one first.
                if record:
                    yield _post_process(record)
                    record = {}

            current_tag = tag
            internal = _REFWORKS_TAG_MAP.get(tag)
            if internal is None:
                continue
            if internal in _REFWORKS_LIST_TAGS:
                record.setdefault(internal, []).append(value)
            else:
                record[internal] = value
        else:
            # Continuation line for the current tag.
            if current_tag is None:
                continue
            internal = _REFWORKS_TAG_MAP.get(current_tag)
            if internal is None:
                continue
            continuation = line.strip()
            if internal in _REFWORKS_LIST_TAGS:
                record[internal][-1] = record[internal][-1] + " " + continuation
            else:
                record[internal] = record.get(internal, "") + " " + continuation

    if record:
        yield _post_process(record)


# ---------------------------------------------------------------------------
# Custom parser
# ---------------------------------------------------------------------------

def _parse_custom(lines: List[str]) -> Iterator[Dict[str, Any]]:
    """Parse CNKI custom export with Chinese or English labels."""
    record: Dict[str, Any] = {}
    current_field: Optional[str] = None

    def flush():
        nonlocal record, current_field
        if current_field is not None and current_field in record:
            value = record[current_field]
            if isinstance(value, list):
                value = " ".join(value)
            if current_field in ("AU", "AF"):
                record[current_field] = _split_authors(value)
            elif current_field == "DE":
                record[current_field] = _split_keywords(value)
            else:
                record[current_field] = value
        current_field = None

    for raw_line in lines:
        line = raw_line.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue

        matched = False
        for pattern, field in _CUSTOM_LABEL_PATTERNS:
            m = pattern.match(line)
            if m:
                flush()
                # If we already have this field, start a new record.
                if field in record:
                    yield _post_process(record)
                    record = {}
                current_field = field
                record[field] = [m.group(1).strip()]
                matched = True
                break

        if matched:
            continue

        # Blank-line-separated records heuristic: a line that looks like a new
        # record title without a label starts a new record.
        if current_field is None and line.strip():
            # If we already have a title, assume a new record begins.
            if "TI" in record:
                yield _post_process(record)
                record = {}
            current_field = "TI"
            record["TI"] = [line.strip()]
            continue

        if current_field is not None:
            record[current_field].append(line.strip())

    flush()
    if record:
        yield _post_process(record)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_cnki_plaintext(
    file_path: Union[str, Path],
    encoding: str = "utf-8",
) -> Iterator[Dict[str, Any]]:
    """Stream-parse a CNKI plaintext export.

    Args:
        file_path: Path to the exported ``.txt`` file.
        encoding: File encoding. CNKI custom exports are often GBK/GB2312;
            the parser falls back to ``gbk`` if UTF-8 decoding fails.

    Yields:
        One dictionary per record, keyed by internal field codes.
    """
    path = Path(file_path)
    if not path.exists():
        raise CNKIParseError(f"File not found: {path}")

    try:
        text = path.read_text(encoding=encoding, errors="replace")
    except UnicodeDecodeError:
        text = path.read_text(encoding="gbk", errors="replace")

    lines = text.splitlines()
    fmt = _detect_format(lines)
    if fmt == "refworks":
        yield from _parse_refworks(lines)
    else:
        yield from _parse_custom(lines)


def parse_cnki_plaintext_to_list(
    file_path: Union[str, Path],
    encoding: str = "utf-8",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Parse a CNKI plaintext file and return records as a list."""
    records = []
    for idx, record in enumerate(parse_cnki_plaintext(file_path, encoding)):
        if limit is not None and idx >= limit:
            break
        records.append(record)
    return records
