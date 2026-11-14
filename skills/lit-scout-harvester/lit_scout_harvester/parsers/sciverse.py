"""Sciverse 在线数据源解析器 + 题录补全（fail-soft，方案见 dev-docs 2026-11-12）。

两类入口：

- **数据源模式**（``datasource: sciverse``）：job 文件 JSON 两种形态——
  ``{"mode": "doi_list", "dois": ["10.1/x", ...]}``（已知 DOI 批量取题录）或
  ``{"mode": "search", "query": "...", "filters": [...], "max_records": 500}``
  （结构化检索发现）。命中映射为 WOS 字段码（TI/AB/AU/SO/PY/DI），pipeline
  其余环节与数据源无感。
- **补全模式**（:func:`maybe_enrich_records`）：WOS/CNKI 解析后的记录常缺
  摘要（CNKI 题录尤甚）或 DOI，按 DOI→题名顺序经 Sciverse 元数据检索补缺，
  **只补缺、不覆盖**；补不到的记录保持原样。

纪律（与 openalex.py 同一哲学，平台是增强层不是依赖层）：未配置 key /
超时 / 429 / 5xx / 解析失败一律记 WARN 并跳过该条（search 首请求失败
返回空列表），绝不抛穿 pipeline；请求严格串行；响应经
:class:`agentkit.sciverse.SciverseClient` 的 7 天缓存去重。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentkit.sciverse import SciverseClient

logger = logging.getLogger(__name__)

JOB_MODE_DOI_LIST = "doi_list"
JOB_MODE_SEARCH = "search"

#: 补全可填的字段（WOS 字段码 → Sciverse PaperMetadata 字段）。
#: 只补缺不覆盖；AD（作者地址）不在其中——Sciverse 作者对象只有 name/orcid。
_ENRICHABLE = {
    "AB": "abstract",
    "DI": "doi",
    "SO": "publication_venue_name_unified",
    "PY": "publication_published_year",
}

_WS_RE = re.compile(r"\s+")


def _norm_title(value: str) -> str:
    return _WS_RE.sub(" ", (value or "")).strip().lower()


def record_from_paper(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Sciverse PaperMetadata → WOS 字段码 dict（与 wos/cnki 解析器同构）。"""
    record: Dict[str, Any] = {}
    title = hit.get("title")
    if title:
        record["TI"] = str(title)
    abstract = hit.get("abstract")
    if abstract:
        record["AB"] = str(abstract)
    authors = hit.get("author") or []
    names = [a.get("name") for a in authors if isinstance(a, dict) and a.get("name")]
    if names:
        record["AU"] = [str(n) for n in names]
    venue = hit.get("publication_venue_name_unified")
    if venue:
        record["SO"] = str(venue)
    year = hit.get("publication_published_year")
    if year:
        try:
            record["PY"] = int(year)
        except (TypeError, ValueError):
            logger.warning("sciverse: 忽略非法出版年份 %r", year)
    doi = hit.get("doi")
    if doi:
        record["DI"] = str(doi)
    return record


def load_sciverse_records(
    input_path: str,
    client: Optional[SciverseClient] = None,
) -> List[Dict[str, Any]]:
    """Sciverse 数据源入口：读 job 文件，返回 WOS 字段码记录列表。"""
    client = client or SciverseClient()
    job = json.loads(Path(input_path).read_text(encoding="utf-8"))
    mode = job.get("mode", JOB_MODE_DOI_LIST)
    if mode == JOB_MODE_SEARCH:
        return _load_search_mode(job, client)
    return _load_doi_list_mode(job, client)


def _load_doi_list_mode(job: Dict[str, Any], client: SciverseClient) -> List[Dict[str, Any]]:
    dois = job.get("dois") or []
    records: List[Dict[str, Any]] = []
    for doi in dois:
        out = client.search_metadata(
            filters=[{"field": "doi", "operator": "FILTER_OP_EQ", "value": str(doi)}],
            page_size=1,
        )
        if not out:
            logger.warning("sciverse datasource: DOI %s 查询失败（无 key/限频/网络？），跳过", doi)
            continue
        results = out.get("results") or []
        if not results:
            continue
        records.append(record_from_paper(results[0]))
    return records


def _load_search_mode(job: Dict[str, Any], client: SciverseClient) -> List[Dict[str, Any]]:
    max_records = int(job.get("max_records") or 500)
    page_size = min(int(job.get("page_size") or 25), 50)  # 服务端上限 50
    records: List[Dict[str, Any]] = []
    page = 1
    cursor = None
    while len(records) < max_records:
        out = client.search_metadata(
            query=job.get("query"),
            filters=job.get("filters"),
            page=page,
            page_size=page_size,
            cursor=cursor,
        )
        if not out:
            logger.warning("sciverse datasource: search 第 %d 页失败，返回已拿到的 %d 条", page, len(records))
            break
        results = out.get("results") or []
        if not results:
            break
        for hit in results:
            if len(records) >= max_records:
                break
            records.append(record_from_paper(hit))
        total_pages = out.get("total_pages")
        if isinstance(total_pages, int) and page >= total_pages:
            break
        next_cursor = out.get("next_cursor")
        if not isinstance(total_pages, int) and not next_cursor and len(results) < page_size:
            break
        if next_cursor:
            # 游标响应不能只递增 page：否则会反复请求第一页并重复计数。
            cursor = str(next_cursor)
        page += 1
    return records


@dataclass
class EnrichStats:
    """补全统计（记入运行摘要，便于审计"平台贡献了多少"）。"""

    candidates: int = 0  # 缺目标字段、进入查询的记录数
    enriched: int = 0  # 成功补缺至少一个字段的记录数
    skipped: int = 0  # 字段已齐无需查询的记录数
    failed: int = 0  # 查询失败/无命中而未补的记录数
    fields_filled: Dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "candidates": self.candidates,
            "enriched": self.enriched,
            "skipped": self.skipped,
            "failed": self.failed,
            "fields_filled": dict(self.fields_filled),
        }


def enrich_records(
    records: List[Dict[str, Any]],
    client: Optional[SciverseClient] = None,
    fields: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> EnrichStats:
    """按 DOI→题名顺序补缺指定字段（默认 AB/DI/SO/PY），只补缺不覆盖。"""
    targets = [f for f in (fields or ["AB", "DI"]) if f in _ENRICHABLE]
    client = client or SciverseClient()
    stats = EnrichStats()
    attempted = 0
    for record in records:
        missing = [f for f in targets if not str(record.get(f) or "").strip()]
        if not missing:
            stats.skipped += 1
            continue
        if limit is not None and attempted >= limit:
            stats.skipped += 1
            continue
        stats.candidates += 1
        attempted += 1
        hit = _find_paper(record, client)
        if hit is None:
            stats.failed += 1
            continue
        filled_any = False
        for f in missing:
            value = hit.get(_ENRICHABLE[f])
            if value and not str(record.get(f) or "").strip():
                record[f] = value
                stats.fields_filled[f] = stats.fields_filled.get(f, 0) + 1
                filled_any = True
        if filled_any:
            stats.enriched += 1
        else:
            stats.failed += 1
    return stats


def _find_paper(record: Dict[str, Any], client: SciverseClient) -> Optional[Dict[str, Any]]:
    """DOI 精确优先；否则题名 MATCH_PHRASE + 归一化相等双重确认（防误补）。"""
    doi = str(record.get("DI") or "").strip()
    if doi:
        out = client.search_metadata(
            filters=[{"field": "doi", "operator": "FILTER_OP_EQ", "value": doi}],
            page_size=1,
        )
        if out and out.get("results"):
            return out["results"][0]
        if out is None:
            return None
        return None
    title = str(record.get("TI") or "").strip()
    if not title:
        return None
    out = client.search_metadata(
        query=None,
        filters=[{"field": "title", "operator": "FILTER_OP_MATCH_PHRASE", "value": title}],
        page_size=3,
    )
    if not out:
        return None
    for hit in out.get("results") or []:
        if _norm_title(str(hit.get("title") or "")) == _norm_title(title):
            return hit
    return None


def maybe_enrich_records(
    records: List[Dict[str, Any]],
    config: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """入口钩子：按 caliber 配置决定是否补全（默认关），返回原列表（原地补齐）。

    供 run_pipeline / agent_run 在 load_records 之后调用；未启用或无凭据时
    零副作用零请求。
    """
    from lit_scout_harvester.core.config_loader import load_caliber

    caliber = load_caliber()
    rules = getattr(caliber, "sciverse", None)
    if rules is None or not getattr(rules, "enabled", False):
        return records
    client = SciverseClient()
    if not client.configured:
        logger.warning("sciverse.enrich.enabled=true 但无 SCIVERSE_API_KEY，跳过补全")
        return records
    stats = enrich_records(
        records,
        client=client,
        fields=list(getattr(rules, "fields", None) or ["AB", "DI"]),
        limit=getattr(rules, "limit", None),
    )
    logger.info("sciverse 题录补全: %s", stats.as_dict())
    return records


__all__ = [
    "EnrichStats",
    "JOB_MODE_DOI_LIST",
    "JOB_MODE_SEARCH",
    "enrich_records",
    "load_sciverse_records",
    "maybe_enrich_records",
    "record_from_paper",
]
