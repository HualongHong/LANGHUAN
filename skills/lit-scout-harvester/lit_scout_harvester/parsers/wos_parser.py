"""WOS plaintext parser.

The parser reads Web of Science plain-text export files in a streaming fashion,
so it can handle files with tens of thousands of records without loading the
whole file into memory.

Record format:
- Records start with a ``PT`` field and end with an ``ER`` field.
- Each field line begins with a two-character field code followed by a space.
- Continuation lines begin with leading whitespace and belong to the previous
  field.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union


# Field codes are exactly two characters: letters and/or digits.
_FIELD_LINE_RE = re.compile(r"^([A-Z][A-Z0-9]) (.*)$")

# Fields whose values are naturally lists (one item per line in the export).
# AU/AF are kept as lists; C1/CR are joined into a single string.
_MULTI_LINE_LIST_FIELDS = {"AU", "AF", "C1", "CR"}
_JOINED_MULTI_LINE_FIELDS = {"C1", "CR"}

# Fields whose single-line value contains semicolon-separated items.
_SEMICOLON_LIST_FIELDS = {"DE", "ID", "SC", "WC"}


class WosParseError(Exception):
    """Raised when a WOS plaintext file cannot be parsed."""


def _is_field_line(line: str) -> bool:
    """Return True if the line starts a new field (not a continuation line)."""
    return bool(line) and not line[0].isspace()


def _post_process_record(
    record: Dict[str, Any], fields: Optional[List[str]]
) -> Dict[str, Any]:
    """Normalize a parsed record and optionally filter fields."""
    # C1 and CR are stored as a single '; '-joined string because each line is
    # a distinct address/reference block. AU/AF remain as lists.
    for code in _JOINED_MULTI_LINE_FIELDS:
        if code in record and isinstance(record[code], list):
            record[code] = "; ".join(record[code])

    # Normalize semicolon-separated fields to a clean list.
    for code in _SEMICOLON_LIST_FIELDS:
        if code in record and isinstance(record[code], str):
            parts = [p.strip() for p in record[code].split(";") if p.strip()]
            record[code] = parts

    if fields:
        return {k: v for k, v in record.items() if k in fields}
    return record


def parse_wos_plaintext(
    file_path: Union[str, Path],
    fields: Optional[List[str]] = None,
    encoding: str = "utf-8",
) -> Iterator[Dict[str, Any]]:
    """Stream-parse a WOS plaintext file.

    Args:
        file_path: Path to the ``.wos`` file.
        fields: If provided, only these field codes are retained in the output
            records. If ``None``, all parsed fields are returned.
        encoding: File encoding. Defaults to UTF-8.

    Yields:
        One dictionary per record, keyed by WOS field code.

    Raises:
        WosParseError: If the file cannot be read.
    """
    path = Path(file_path)
    if not path.exists():
        raise WosParseError(f"File not found: {path}")

    record: Dict[str, Any] = {}
    current_code: Optional[str] = None

    try:
        with path.open("r", encoding=encoding, errors="replace") as f:
            for raw_line in f:
                line = raw_line.rstrip("\n").rstrip("\r")
                if not line.strip():
                    continue

                if _is_field_line(line):
                    match = _FIELD_LINE_RE.match(line)
                    if not match:
                        # Lines such as the file header (FN, VR) do not match
                        # the record field pattern; skip them.
                        continue

                    code, value = match.groups()
                    value = value.strip()

                    if code == "PT":
                        # Start of a new record. Yield the previous one first,
                        # discarding any header lines (FN, VR, ...) that appear
                        # before the first PT.
                        if record and record.get("PT"):
                            yield _post_process_record(record, fields)
                        record = {code: value}
                        current_code = code
                    elif code == "ER":
                        # End of the current record.
                        if record:
                            yield _post_process_record(record, fields)
                        record = {}
                        current_code = None
                    else:
                        current_code = code
                        if code in _MULTI_LINE_LIST_FIELDS:
                            record.setdefault(code, []).append(value)
                        else:
                            record[code] = value
                else:
                    # Continuation line: append to the current field.
                    if current_code is None:
                        continue
                    continuation = line.strip()
                    if current_code in _MULTI_LINE_LIST_FIELDS:
                        # For repeated-value fields (AU, AF, C1, CR) each
                        # indented line is normally a new value/item.
                        record[current_code].append(continuation)
                    else:
                        record[current_code] = (
                            record.get(current_code, "") + " " + continuation
                        )

        # Handle files that do not end with an explicit ER line.
        if record:
            yield _post_process_record(record, fields)

    except UnicodeDecodeError as exc:
        raise WosParseError(f"Could not decode {path} with {encoding}: {exc}") from exc
    except OSError as exc:
        raise WosParseError(f"Could not read {path}: {exc}") from exc


def count_records(file_path: Union[str, Path], encoding: str = "utf-8") -> int:
    """Count the number of records in a WOS plaintext file."""
    return sum(1 for _ in parse_wos_plaintext(file_path, encoding=encoding))


def parse_wos_plaintext_to_list(
    file_path: Union[str, Path],
    fields: Optional[List[str]] = None,
    encoding: str = "utf-8",
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Parse a WOS plaintext file and return records as a list.

    This is a convenience wrapper around :func:`parse_wos_plaintext`. For large
    files, prefer the streaming iterator.
    """
    records = []
    for idx, record in enumerate(parse_wos_plaintext(file_path, fields, encoding)):
        if limit is not None and idx >= limit:
            break
        records.append(record)
    return records
