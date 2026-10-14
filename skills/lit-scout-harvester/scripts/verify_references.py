"""Verify a reference list against OpenAlex single-record endpoints.

For each row of an input reference table (RIS export or CSV with a DOI
column) the DOI is resolved through the **free** OpenAlex single-record
endpoint (``works/doi:{doi}`` — 0 credits, no list rate limit), and the
returned work is compared with the input on every dimension the input
provides (title / authors / journal, lenient matching).

Outputs in ``--output-dir``:

- ``references_checked.csv`` — per-reference verdict list;
- ``summary.json`` — aggregate counts;
- ``CITATION.md`` — provenance stub whose extra source records the check
  (via ``utils.citation``'s existing ``extra_sources`` mechanism).

The fetch backend is injectable (tests fake it); production uses
``agentkit.fetch`` with its 429/403 long-backoff handling. Online checks
are strictly sequential — no concurrency, no forged UA, no proxy bypass
(see ``agentkit.quota`` disciplines).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
try:
    import agentkit  # noqa: F401
except ImportError:  # monorepo layout: agentkit lives in the suite-level libs/
    _libs = PROJECT_ROOT.parents[1] / "libs"
    if (_libs / "agentkit").is_dir() and str(_libs) not in sys.path:
        sys.path.insert(0, str(_libs))

from lit_scout_harvester.parsers.openalex import (
    ENV_MAILTO,
    Fetcher,
    OpenAlexError,
    OpenAlexFetchError,
    fetch_work_by_doi,
    resolve_mailto,
)
from lit_scout_harvester.utils.citation import TOOL_VERSION, generate_citation

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_UNRESOLVED = "doi_unresolved"
STATUS_TITLE_MISMATCH = "title_mismatch"
STATUS_AUTHORS_MISMATCH = "authors_mismatch"
STATUS_JOURNAL_MISMATCH = "journal_mismatch"
STATUS_ERROR = "error"

REPORT_CSV = "references_checked.csv"
SUMMARY_JSON = "summary.json"
CITATION_MD = "CITATION.md"


# ---------------------------------------------------------------------------
# input parsing
# ---------------------------------------------------------------------------

def parse_ris(path: Path) -> List[Dict[str, Any]]:
    """Parse a RIS file into reference entries (doi/title/authors/journal/year)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    entries: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {}
    last_field: Optional[str] = None

    def _flush() -> None:
        nonlocal current
        if current:
            entries.append(current)
        current = {}

    def _set(field: str, value: str) -> None:
        nonlocal last_field
        if field == "authors":
            current.setdefault("authors", []).append(value)
        else:
            current[field] = value
        last_field = field

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        if line.startswith((" ", "\t")):
            # RIS continuation line: append to the previous tag's value.
            if last_field == "authors" and current.get("authors"):
                current["authors"][-1] += " " + line.strip()
            elif last_field and last_field in current:
                current[last_field] += " " + line.strip()
            continue
        if len(line) < 6 or line[2:6] != "  - ":
            continue
        tag = line[:2].upper()
        value = line[6:].strip()
        if tag == "TY":
            _flush()
            current = {"authors": []}
            last_field = None
            continue
        if tag == "ER":
            _flush()
            last_field = None
            continue
        if not current:
            continue
        if tag in ("TI", "T1"):
            _set("title", value)
        elif tag in ("AU", "A1"):
            _set("authors", value)
        elif tag in ("JO", "JF", "T2", "JA", "BT"):
            _set("journal", value)
        elif tag in ("PY", "Y1"):
            _set("year", value.split("/")[0].strip())
        elif tag == "DO":
            _set("doi", value)
        else:
            last_field = None
    _flush()
    return entries


_CSV_DOI_COLUMNS = ("doi", "DOI", "Doi", "di")
_CSV_TITLE_COLUMNS = ("title", "TI", "Title", "article_title")
_CSV_AUTHOR_COLUMNS = ("authors", "author", "AU", "Authors")
_CSV_JOURNAL_COLUMNS = ("journal", "source", "SO", "JO", "publication", "journal_title")
_CSV_YEAR_COLUMNS = ("year", "PY", "Year", "publication_year")


def _first_column(fieldnames: Optional[Sequence[str]], candidates: Sequence[str]) -> Optional[str]:
    if not fieldnames:
        return None
    lowered = {name.lower(): name for name in fieldnames}
    for cand in candidates:
        if cand in fieldnames:
            return cand
        if cand.lower() in lowered:
            return lowered[cand.lower()]
    return None


def parse_reference_csv(path: Path) -> List[Dict[str, Any]]:
    """Parse a CSV reference table; columns are matched leniently."""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        doi_col = _first_column(fields, _CSV_DOI_COLUMNS)
        if doi_col is None:
            raise ValueError(f"{path}: no DOI column found (looked for {_CSV_DOI_COLUMNS})")
        cols = {
            "doi": doi_col,
            "title": _first_column(fields, _CSV_TITLE_COLUMNS),
            "authors": _first_column(fields, _CSV_AUTHOR_COLUMNS),
            "journal": _first_column(fields, _CSV_JOURNAL_COLUMNS),
            "year": _first_column(fields, _CSV_YEAR_COLUMNS),
        }
        entries = []
        for row in reader:
            entry: Dict[str, Any] = {"doi": (row.get(cols["doi"]) or "").strip()}
            if cols["title"]:
                entry["title"] = (row.get(cols["title"]) or "").strip()
            if cols["authors"]:
                raw = row.get(cols["authors"]) or ""
                # pipeline CSV exports join authors with "; " — split there
                # first; a bare "Doe, Jane" stays one author.
                entry["authors"] = (
                    [a.strip() for a in raw.split(";") if a.strip()]
                    if ";" in raw
                    else ([raw.strip()] if raw.strip() else [])
                )
            if cols["journal"]:
                entry["journal"] = (row.get(cols["journal"]) or "").strip()
            if cols["year"]:
                entry["year"] = (row.get(cols["year"]) or "").strip()
            if entry["doi"]:
                entries.append(entry)
    return entries


def load_references(path: Path) -> List[Dict[str, Any]]:
    """Dispatch on file type: ``.csv`` → CSV, anything else → RIS."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        return parse_reference_csv(path)
    return parse_ris(path)


# ---------------------------------------------------------------------------
# lenient matching
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", (text or "").lower())).strip()


def titles_match(input_title: str, work_title: str) -> bool:
    """Lenient title match: equality, containment, or >= 80% token overlap."""
    a = normalize_title(input_title)
    b = normalize_title(work_title)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return False
    overlap = len(tokens_a & tokens_b) / len(tokens_a)
    return overlap >= 0.8


def author_family(author: str) -> str:
    """Family name from ``Smith, John`` or ``John Smith`` (last token)."""
    name = (author or "").strip()
    if not name:
        return ""
    if "," in name:
        return name.split(",", 1)[0].strip().lower()
    tokens = name.split()
    return tokens[-1].strip().lower() if tokens else ""


def authors_match(input_authors: Sequence[str], work: Mapping[str, Any]) -> bool:
    """Lenient author check: any input family name appears among the work's."""
    families = {author_family(a) for a in input_authors} - {""}
    if not families:
        return True  # nothing to check
    work_families = set()
    for authorship in work.get("authorships") or []:
        display = (authorship.get("author") or {}).get("display_name") or ""
        tokens = display.split()
        if tokens:
            work_families.add(tokens[-1].strip().lower())
    return bool(families & work_families)


def journal_matches(input_journal: str, work: Mapping[str, Any]) -> bool:
    """Lenient journal check: normalized equality/containment either way."""
    a = normalize_title(input_journal)
    source = (work.get("primary_location") or {}).get("source") or work.get("host_venue") or {}
    b = normalize_title(str(source.get("display_name") or ""))
    if not a or not b:
        return False
    return a == b or a in b or b in a


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify_reference(
    entry: Mapping[str, Any],
    fetcher: Fetcher,
    mailto: Optional[str] = None,
) -> Dict[str, Any]:
    """Check one reference entry against the OpenAlex single-record endpoint."""
    doi = str(entry.get("doi") or "").strip()
    row: Dict[str, Any] = {
        "doi": doi,
        "status": STATUS_OK,
        "detail": "",
        "input_title": entry.get("title", ""),
        "openalex_title": "",
        "input_journal": entry.get("journal", ""),
        "openalex_journal": "",
    }
    if not doi:
        row["status"] = STATUS_UNRESOLVED
        row["detail"] = "no DOI in input row"
        return row
    try:
        work = fetch_work_by_doi(doi, fetcher=fetcher, mailto=mailto)
    except OpenAlexFetchError as exc:
        if exc.status == 404:
            work = None
        else:
            row["status"] = STATUS_ERROR
            row["detail"] = f"HTTP {exc.status}"
            return row
    except OpenAlexError as exc:
        row["status"] = STATUS_ERROR
        row["detail"] = f"{type(exc).__name__}: {exc}"
        return row
    if work is None:
        row["status"] = STATUS_UNRESOLVED
        row["detail"] = "DOI does not resolve in OpenAlex"
        return row

    row["openalex_title"] = work.get("display_name") or ""
    source = (work.get("primary_location") or {}).get("source") or work.get("host_venue") or {}
    row["openalex_journal"] = source.get("display_name") or ""

    input_title = str(entry.get("title") or "").strip()
    if input_title and not titles_match(input_title, row["openalex_title"]):
        row["status"] = STATUS_TITLE_MISMATCH
        row["detail"] = "title mismatch"
        return row
    if not authors_match(entry.get("authors") or [], work):
        row["status"] = STATUS_AUTHORS_MISMATCH
        row["detail"] = "no input author family found among work authors"
        return row
    input_journal = str(entry.get("journal") or "").strip()
    if input_journal and not journal_matches(input_journal, work):
        row["status"] = STATUS_JOURNAL_MISMATCH
        row["detail"] = "journal mismatch"
        return row
    return row


def verify_references(
    entries: Sequence[Mapping[str, Any]],
    fetcher: Optional[Fetcher] = None,
    mailto: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Verify all entries sequentially; returns (rows, summary)."""
    if fetcher is None:
        from lit_scout_harvester.parsers.openalex import _default_fetcher

        fetcher = _default_fetcher(None)
    mailto = resolve_mailto(mailto)
    rows = [verify_reference(entry, fetcher, mailto=mailto) for entry in entries]
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    summary = {
        "total": len(rows),
        "ok": counts.get(STATUS_OK, 0),
        "problems": len(rows) - counts.get(STATUS_OK, 0),
        "status_counts": counts,
        "mailto": mailto,
        "tool_version": TOOL_VERSION,
    }
    return rows, summary


def write_report(
    rows: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    output_dir: Path,
) -> Dict[str, str]:
    """Write the CSV list, JSON summary, and CITATION.md provenance stub."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / REPORT_CSV
    fieldnames = [
        "doi",
        "status",
        "detail",
        "input_title",
        "openalex_title",
        "input_journal",
        "openalex_journal",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    summary_path = output_dir / SUMMARY_JSON
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    checked = summary.get("total", 0)
    ok = summary.get("ok", 0)
    problems = summary.get("problems", 0)
    citation_path = generate_citation(
        output_dir / CITATION_MD,
        extra_sources=[
            {
                "name": "Reference Verification: OpenAlex single-record API",
                "details": f"{checked} DOIs checked via works/doi endpoint (free): {ok} ok, {problems} problem(s) "
                f"({', '.join(f'{k}={v}' for k, v in sorted(summary.get('status_counts', {}).items()))})",
            }
        ],
    )
    return {"csv": str(csv_path), "summary": str(summary_path), "citation": str(citation_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_references.py",
        description="Verify a RIS/CSV reference list against OpenAlex single-record endpoints (free).",
    )
    parser.add_argument("--input", type=Path, required=True, help="RIS export or CSV with a DOI column")
    parser.add_argument("--output-dir", type=Path, default=Path("reference_check"), help="Report directory")
    parser.add_argument("--mailto", default=None, help=f"OpenAlex polite-pool email (else ${ENV_MAILTO})")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    args = build_parser().parse_args(argv)
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 2
    try:
        entries = load_references(input_path)
    except (ValueError, OSError) as exc:
        print(f"error: cannot parse {input_path}: {exc}", file=sys.stderr)
        return 2
    rows, summary = verify_references(entries, mailto=args.mailto)
    paths = write_report(rows, summary, Path(args.output_dir))
    summary["outputs"] = paths
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
