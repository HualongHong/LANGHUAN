"""Author-address recovery from WOS C1 fields.

The WOS ``C1`` field contains address blocks that may be shared by multiple
authors. This module restores a one-to-one mapping between each author and
their most likely address, and performs lightweight parsing of the address
into country / city / institution / postal code.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from lit_scout_harvester.external.inst_city_map import InstitutionCityMap


# Regex for bracketed address blocks. The address group stops at the next '['
# so that semicolons inside the author list are not mistaken for block separators.
_BRACKETED_BLOCK_RE = re.compile(r"\[(?P<authors>[^\]]+)\]\s*(?P<address>[^\[]*)")

# Loose postal-code patterns used to extract a postcode from the city segment.
_POSTAL_CODE_RE = re.compile(
    r"\b([A-Z]{1,2}\d[\dA-Z]?\s?\d?[A-Z]{0,2}|\d{4,6}|"
    r"[A-Z]{2}-\d{4,6}|[A-Z]\d[A-Z]\s?\d[A-Z]\d)\b",
    re.IGNORECASE,
)


def _normalize_name(name: str) -> str:
    """Normalize a name for matching.

    Removes spaces, periods and commas, and lower-cases the result.
    ``Jordaan, L. J.`` and ``Jordaan, LJ`` both become ``jordaanlj``.
    """
    return re.sub(r"[\.\s,]", "", name).lower()


def _build_author_index(
    au_list: List[str], af_list: Optional[List[str]] = None
) -> Dict[str, int]:
    """Build a normalized-name -> author index map.

    The short-name list (``AU``) is preferred; the full-name list (``AF``) is
    used as a fallback.
    """
    index: Dict[str, int] = {}
    for idx, name in enumerate(au_list):
        index[_normalize_name(name)] = idx
    if af_list:
        for idx, name in enumerate(af_list):
            norm = _normalize_name(name)
            if norm not in index:
                index[norm] = idx
    return index


def _iter_blocks(c1_field: str) -> List[Tuple[Optional[List[str]], str]]:
    """Split a C1 field into address blocks while respecting brackets.

    Returns a list of ``(authors, address)`` tuples. ``authors`` is ``None``
    for unbracketed (shared) blocks.
    """
    blocks: List[Tuple[Optional[List[str]], str]] = []
    last_end = 0

    for match in _BRACKETED_BLOCK_RE.finditer(c1_field):
        if match.start() > last_end:
            text = c1_field[last_end : match.start()].strip(" ;")
            if text:
                blocks.append((None, text))

        author_part = match.group("authors")
        authors = [a.strip() for a in author_part.split(";") if a.strip()]
        address = match.group("address").strip(" ;")
        blocks.append((authors, address))
        last_end = match.end()

    if last_end < len(c1_field):
        text = c1_field[last_end:].strip(" ;")
        if text:
            blocks.append((None, text))

    return blocks


def _parse_address_block(block: str) -> Dict[str, Any]:
    """Parse a single address string into structured components.

    This is a heuristic parser. It works best on addresses that end with
    ``..., City [Postcode], Country``.
    """
    address = block.strip().rstrip(".")
    parts = [p.strip() for p in address.split(",") if p.strip()]

    country: Optional[str] = None
    city: Optional[str] = None
    postal_code: Optional[str] = None
    institution: Optional[str] = None

    if not parts:
        return {
            "address": address,
            "country": country,
            "city": city,
            "institution": institution,
            "postal_code": postal_code,
        }

    # Country is usually the last comma-separated segment.
    country = parts[-1].strip()

    # City is usually the segment before the country.
    if len(parts) >= 2:
        city_part = parts[-2]
        postal_match = _POSTAL_CODE_RE.search(city_part)
        if postal_match:
            postal_code = postal_match.group(1)
            # Remove the postal code and any surrounding '-' / whitespace.
            city = (
                (city_part[: postal_match.start()] + city_part[postal_match.end() :])
                .strip("- ")
                .strip()
            )
        else:
            city = city_part

    # Institution is usually the first segment(s) before street/PO box info.
    if parts:
        institution = parts[0]

    return {
        "address": address,
        "country": country,
        "city": city,
        "institution": institution,
        "postal_code": postal_code,
    }


def restore_author_address(
    c1_field: str,
    au_list: List[str],
    af_list: Optional[List[str]] = None,
    options: Optional[Dict[str, Any]] = None,
    inst_city_map: Optional[InstitutionCityMap] = None,
) -> List[Dict[str, Any]]:
    """Restore one-to-one author-address mapping from a WOS ``C1`` field.

    Args:
        c1_field: Raw ``C1`` value (address blocks separated by ``; ``).
        au_list: Short author names from the ``AU`` field.
        af_list: Optional full author names from the ``AF`` field.
        options: Optional dictionary of parsing flags:
            - ``extract_country`` (default True)
            - ``extract_city`` (default True)
            - ``extract_institution`` (default True)
            - ``extract_postal_code`` (default True)
            - ``use_inst_city_map`` (default False)
        inst_city_map: Optional lookup table used when city/country parsing fails.

    Returns:
        A list of dictionaries, one per author, containing the author name and
        the recovered address information with a ``confidence`` score.
    """
    opts = {
        "extract_country": True,
        "extract_city": True,
        "extract_institution": True,
        "extract_postal_code": True,
        "use_inst_city_map": False,
    }
    if options:
        opts.update(options)

    if not c1_field or not c1_field.strip():
        return [
            {
                "author": name,
                "full_name": (af_list or au_list)[idx] if af_list else name,
                "address": None,
                "country": None,
                "city": None,
                "institution": None,
                "postal_code": None,
                "confidence": 0.0,
            }
            for idx, name in enumerate(au_list)
        ]

    author_index = _build_author_index(au_list, af_list)
    assignments: Dict[int, Dict[str, Any]] = {}

    for authors, address in _iter_blocks(c1_field):
        parsed = _parse_address_block(address)
        if authors:
            for a_name in authors:
                idx = author_index.get(_normalize_name(a_name))
                if idx is None:
                    continue
                if idx not in assignments:
                    assignments[idx] = {
                        **parsed,
                        "confidence": 1.0,
                    }
        else:
            # Unbracketed block: treat as shared address for unassigned authors.
            for idx in range(len(au_list)):
                if idx not in assignments:
                    assignments[idx] = {
                        **parsed,
                        "confidence": 0.6,
                    }

    results = []
    display_names = af_list if af_list else au_list
    for idx, name in enumerate(au_list):
        assigned = assignments.get(idx)
        if assigned is None:
            result = {
                "address": None,
                "country": None,
                "city": None,
                "institution": None,
                "postal_code": None,
                "confidence": 0.0,
            }
        else:
            result = dict(assigned)
            if not opts["extract_country"]:
                result["country"] = None
            if not opts["extract_city"]:
                result["city"] = None
            if not opts["extract_institution"]:
                result["institution"] = None
            if not opts["extract_postal_code"]:
                result["postal_code"] = None

            # Fallback to institution-city map when parsing yields no city/country.
            if (
                opts.get("use_inst_city_map")
                and inst_city_map is not None
                and result.get("institution")
            ):
                mapped = inst_city_map.get(result["institution"])
                if mapped:
                    city = result.get("city")
                    institution = result.get("institution")
                    if opts["extract_city"] and (not city or city == institution):
                        result["city"] = mapped["city"]
                        result["confidence"] = min(result.get("confidence", 1.0), 0.7)
                    if opts["extract_country"] and not result.get("country"):
                        result["country"] = mapped["country"]
                        result["confidence"] = min(result.get("confidence", 1.0), 0.7)

        result["author"] = name
        result["full_name"] = display_names[idx]
        results.append(result)

    return results
