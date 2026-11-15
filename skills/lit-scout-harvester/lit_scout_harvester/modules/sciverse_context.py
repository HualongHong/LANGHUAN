"""Sciverse semantic chunks and accessible full text for term extraction."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agentkit.sciverse import SciverseClient


_WS_RE = re.compile(r"\s+")


def _bounded_int(value: Any, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(parsed, upper))


def _norm_doi(value: Any) -> str:
    doi = str(value or "").strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            return doi[len(prefix):].strip()
    return doi


def _norm_title(value: Any) -> str:
    return _WS_RE.sub(" ", str(value or "")).strip().casefold()


def _same_publication(record: Dict[str, Any], hit: Dict[str, Any]) -> bool:
    record_doi = _norm_doi(record.get("DI"))
    hit_doi = _norm_doi(hit.get("doi"))
    if record_doi and hit_doi:
        return record_doi == hit_doi
    record_title = _norm_title(record.get("TI"))
    hit_title = _norm_title(hit.get("title"))
    return bool(record_title and hit_title and record_title == hit_title)


class SciverseContextProvider:
    """Build bounded full-text context without mixing evidence across papers."""

    def __init__(self, client: SciverseClient):
        self.client = client

    def context_for(self, record: Dict[str, Any], rules: Dict[str, Any]) -> str:
        title = str(record.get("TI") or "").strip()
        abstract = str(record.get("AB") or "").strip()
        if not title and not record.get("DI"):
            return ""
        query_chars = _bounded_int(rules.get("query_chars"), 1200, 1, 10000)
        query = "\n".join(part for part in (title, abstract) if part)[:query_chars]
        if not query:
            return ""

        response = self.client.semantic_search(
            query=query,
            top_k=_bounded_int(rules.get("top_k"), 5, 1, 50),
            mode=str(rules.get("mode") or "balanced"),
            filters=rules.get("filters"),
        )
        if not response:
            return ""
        hits = response.get("hits") or []
        if not isinstance(hits, list):
            return ""

        max_chunks = _bounded_int(rules.get("max_chunks"), 2, 1, 10)
        max_chunk_chars = _bounded_int(rules.get("max_chunk_chars"), 2000, 1, 10000)
        max_context_chars = _bounded_int(rules.get("max_context_chars"), 4000, 1, 30000)
        expand = rules.get("expand_context", True)
        overlap_chars = _bounded_int(rules.get("overlap_chars"), 300, 0, 5000)
        chunks: List[str] = []
        seen = set()
        total_chars = 0
        for hit in hits:
            if not isinstance(hit, dict) or not _same_publication(record, hit):
                continue
            doc_id = str(hit.get("doc_id") or "").strip()
            offset = hit.get("offset") if isinstance(hit.get("offset"), int) else 0
            identity = (doc_id, offset)
            if identity in seen:
                continue
            seen.add(identity)
            chunk = str(hit.get("chunk") or hit.get("text") or "").strip()
            text = self._expanded_text(
                doc_id, offset, chunk, expand, overlap_chars, max_chunk_chars
            )
            if not text:
                continue
            remaining = max_context_chars - total_chars
            if remaining <= 0:
                break
            text = text[:remaining]
            chunks.append(text)
            total_chars += len(text)
            if len(chunks) >= max_chunks:
                break
        return "\n\n".join(chunks)

    def fulltext_for(self, record: Dict[str, Any], rules: Dict[str, Any]) -> str:
        """Read accessible full text with bounded, forward-only pagination."""
        hit = self._find_document(record)
        if hit is None or hit.get("is_content_accessible") is not True:
            return ""
        doc_id = str(hit.get("doc_id") or hit.get("unique_id") or "").strip()
        if not doc_id:
            return ""

        page_chars = _bounded_int(rules.get("page_chars"), 4096, 1, 20000)
        max_pages = _bounded_int(rules.get("max_pages"), 3, 1, 20)
        max_chars = _bounded_int(rules.get("max_context_chars"), 12000, 1, 100000)
        pages: List[str] = []
        offset = 0
        for _ in range(max_pages):
            response = self.client.read_content(doc_id, offset=offset, limit=page_chars)
            if not response:
                break
            text = str(response.get("text") or "")
            if text:
                pages.append(text)
            next_offset = response.get("next_offset")
            if not response.get("more"):
                break
            if not isinstance(next_offset, int) or isinstance(next_offset, bool) or next_offset <= offset:
                break
            offset = next_offset
        return "\n".join(pages)[:max_chars]

    def _find_document(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        doi = _norm_doi(record.get("DI"))
        title = str(record.get("TI") or "").strip()
        if doi:
            response = self.client.search_metadata(
                filters=[{"field": "doi", "operator": "FILTER_OP_EQ", "value": doi}],
                page_size=3,
            )
        elif title:
            response = self.client.search_metadata(
                query=None,
                filters=[{"field": "title", "operator": "FILTER_OP_MATCH_PHRASE", "value": title}],
                page_size=3,
            )
        else:
            return None
        if not response:
            return None
        for hit in response.get("results") or []:
            if isinstance(hit, dict) and _same_publication(record, hit):
                return hit
        return None

    def _expanded_text(
        self,
        doc_id: str,
        offset: int,
        chunk: str,
        expand: bool,
        overlap_chars: int,
        max_chunk_chars: int,
    ) -> str:
        if expand and doc_id:
            start = max(0, offset - overlap_chars)
            limit = min(max_chunk_chars, max(1, len(chunk) + 2 * overlap_chars))
            response = self.client.read_content(doc_id, offset=start, limit=limit)
            if response:
                expanded = str(response.get("text") or "").strip()
                if expanded:
                    return expanded
        return chunk[:max_chunk_chars]


__all__ = ["SciverseContextProvider"]
