"""点石（Dianshi）文献检索数据源 + 题录补全（fail-soft，2026-11-12）。

两类入口：

- **数据源模式**（``datasource: dianshi``）：job 文件 JSON——
  ``{"platform": "dianshi", "mode": "search", "query": "...",
  "max_records": 50}`` 或 ``{"platform": "dianshi", "mode": "id_list",
  "ids": [reference_id, ...]}``。``"platform": "dianshi"`` 是与 sciverse
  job 的判别键（两者 mode 词表重叠，detect_datasource 靠它区分）。
  命中映射为 WOS 字段码（TI/AB/AU/SO/PY/DI），pipeline 其余环节与
  数据源无感。
- **补全模式**（:func:`maybe_enrich_records`）：按题名经
  ``reference_search`` 找候选、归一化题名双重确认后补缺，**只补缺、不
  覆盖**；补不到的记录保持原样。

**字段映射注意**：点石 reference 对象的确切键名未经实测（SSO token 审批
中，需 tools/list + 真实响应核对）。现映射按常见命名做多键候选兜底
（``_FIELD_CANDIDATES``），解析时把未识别键记 INFO 日志；token 到位后
按日志一次性校正本表。纪律与 sciverse.py 相同：平台是增强层不是依赖层。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from agentkit.dianshi import DianshiClient

logger = logging.getLogger(__name__)

JOB_PLATFORM = "dianshi"
JOB_MODE_SEARCH = "search"
JOB_MODE_ID_LIST = "id_list"

#: 补全可填的字段（WOS 字段码 → 点石 reference 字段候选键，实测后校正）。
#: 只补缺不覆盖；AD（作者地址）不在其中——点石作者对象结构待实测。
_ENRICHABLE = {
    "AB": ("abstract", "abstract_preview", "abstract_text", "summary"),
    "DI": ("doi",),
    "SO": ("venue", "journal", "source", "publication", "container_title"),
    "PY": ("year", "publish_year", "publication_year", "pub_year"),
}

#: reference 对象 → WOS 字段码的候选键（防御性多键映射，未经实测）。
_FIELD_CANDIDATES = {
    "TI": ("title", "reference_title", "doc_title", "name"),
    "AB": ("abstract", "abstract_preview", "abstract_text", "summary"),
    "DI": ("doi",),
    "SO": ("venue", "journal", "source", "publication", "container_title"),
    "PY": ("year", "publish_year", "publication_year", "pub_year"),
    "AU": ("authors", "author_names", "author_list"),
    "ID": ("reference_id", "id"),
}

_ALL_KNOWN_KEYS = {key for keys in _FIELD_CANDIDATES.values() for key in keys} | {
    key for keys in _ENRICHABLE.values() for key in keys
}
_shape_logged = False

_WS_RE = re.compile(r"\s+")


def _norm_title(value: str) -> str:
    return _WS_RE.sub(" ", (value or "")).strip().lower()


def _pick(hit: Dict[str, Any], candidates: tuple) -> Any:
    for key in candidates:
        value = hit.get(key)
        if value not in (None, ""):
            return value
    return None


def _log_unknown_keys(hit: Dict[str, Any]) -> None:
    """把未识别的键记一次 INFO，供 token 到位后校正 _FIELD_CANDIDATES。"""
    global _shape_logged
    if _shape_logged:
        return
    unknown = sorted(set(hit) - _ALL_KNOWN_KEYS)
    if unknown:
        logger.info("dianshi reference 未识别键（待校正映射）: %s；已知键: %s",
                    unknown, sorted(set(hit) & _ALL_KNOWN_KEYS))
    else:
        logger.info("dianshi reference 键全部命中候选映射: %s", sorted(hit))
    _shape_logged = True


def record_from_reference(hit: Dict[str, Any]) -> Dict[str, Any]:
    """点石 reference 对象 → WOS 字段码 dict（与 wos/cnki/sciverse 解析器同构）。"""
    _log_unknown_keys(hit)
    record: Dict[str, Any] = {}
    title = _pick(hit, _FIELD_CANDIDATES["TI"])
    if title:
        record["TI"] = str(title)
    abstract = _pick(hit, _FIELD_CANDIDATES["AB"])
    if abstract:
        record["AB"] = str(abstract)
    authors = _pick(hit, _FIELD_CANDIDATES["AU"])
    if isinstance(authors, list) and authors:
        names = [
            str(a.get("name") or a.get("author_name") or "") if isinstance(a, dict) else str(a)
            for a in authors
        ]
        names = [n for n in names if n]
        if names:
            record["AU"] = names
    venue = _pick(hit, _FIELD_CANDIDATES["SO"])
    if venue:
        record["SO"] = str(venue)
    year = _pick(hit, _FIELD_CANDIDATES["PY"])
    if year:
        try:
            record["PY"] = int(str(year)[:4])
        except ValueError:
            pass
    doi = _pick(hit, _FIELD_CANDIDATES["DI"])
    if doi:
        record["DI"] = str(doi)
    return record


def load_dianshi_records(
    input_path: str,
    client: Optional[DianshiClient] = None,
) -> List[Dict[str, Any]]:
    """点石数据源入口：读 job 文件，返回 WOS 字段码记录列表。"""
    client = client or DianshiClient()
    job = json.loads(Path(input_path).read_text(encoding="utf-8"))
    mode = job.get("mode", JOB_MODE_SEARCH)
    if mode == JOB_MODE_ID_LIST:
        return _load_id_list_mode(job, client)
    return _load_search_mode(job, client)


def _load_search_mode(job: Dict[str, Any], client: DianshiClient) -> List[Dict[str, Any]]:
    max_records = int(job.get("max_records") or 50)
    limit = min(int(job.get("limit") or 10), max_records)  # 服务端钳制，官方建议 5-10 起步
    out = client.reference_search(query=str(job.get("query") or ""), limit=limit)
    if not out:
        logger.warning("dianshi datasource: search 失败（无 token/限频/网络？），返回空列表")
        return []
    hits = out.get("references") or out.get("results") or out.get("items") or []
    if not isinstance(hits, list):
        logger.warning("dianshi datasource: 响应无文献列表键（实际键 %s），返回空列表", sorted(out))
        return []
    if len(hits) > max_records:
        hits = hits[:max_records]
    if len(hits) == max_records:
        logger.info("dianshi datasource: 命中数达 max_records=%d，可能被服务端钳制截断", max_records)
    return [record_from_reference(h) for h in hits if isinstance(h, dict)]


def _load_id_list_mode(job: Dict[str, Any], client: DianshiClient) -> List[Dict[str, Any]]:
    ids = job.get("ids") or []
    records: List[Dict[str, Any]] = []
    for rid in ids:
        out = client.reference_get_by_id(rid)
        if not out:
            logger.warning("dianshi datasource: reference_id %s 查询失败，跳过", rid)
            continue
        records.append(record_from_reference(out))
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
    client: Optional[DianshiClient] = None,
    fields: Optional[List[str]] = None,
    limit: Optional[int] = None,
) -> EnrichStats:
    """按题名检索补缺指定字段（默认 AB/DI），只补缺不覆盖。

    点石无按 DOI 精确查入口（reference_get_by_id 吃内部 id），故统一走
    题名检索 + 归一化相等双重确认（防误补）。
    """
    targets = [f for f in (fields or ["AB", "DI"]) if f in _ENRICHABLE]
    client = client or DianshiClient()
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
        hit = _find_reference(record, client)
        if hit is None:
            stats.failed += 1
            continue
        filled_any = False
        for f in missing:
            value = _pick(hit, _ENRICHABLE[f])
            if value and not str(record.get(f) or "").strip():
                record[f] = value
                stats.fields_filled[f] = stats.fields_filled.get(f, 0) + 1
                filled_any = True
        if filled_any:
            stats.enriched += 1
        else:
            stats.failed += 1
    return stats


def _find_reference(record: Dict[str, Any], client: DianshiClient) -> Optional[Dict[str, Any]]:
    """题名检索 + 归一化相等双重确认（防误补）；title 缺失则跳过。"""
    title = str(record.get("TI") or "").strip()
    if not title:
        return None
    out = client.reference_search(query=title, limit=3)
    if not out:
        return None
    hits = out.get("references") or out.get("results") or out.get("items") or []
    if not isinstance(hits, list):
        return None
    for hit in hits:
        if isinstance(hit, dict) and _norm_title(str(_pick(hit, _FIELD_CANDIDATES["TI"]) or "")) == _norm_title(title):
            return hit
    return None


def maybe_enrich_records(
    records: List[Dict[str, Any]],
    config: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """入口钩子：按 caliber 配置决定是否补全（默认关），返回原列表（原地补齐）。

    供 agent_run 在 sciverse 补全之后调用；未启用、无凭据或平台不可用时
    零副作用零请求。
    """
    from lit_scout_harvester.core.config_loader import load_caliber

    caliber = load_caliber()
    rules = getattr(caliber, "dianshi", None)
    if rules is None or not getattr(rules, "enabled", False):
        return records
    client = DianshiClient()
    if not client.configured:
        logger.warning("dianshi.enrich.enabled=true 但无 DIANSHI_SSO_TOKEN，跳过补全")
        return records
    stats = enrich_records(
        records,
        client=client,
        fields=list(getattr(rules, "fields", None) or ["AB", "DI"]),
        limit=getattr(rules, "limit", None),
    )
    logger.info("dianshi 题录补全: %s", stats.as_dict())
    return records


__all__ = [
    "EnrichStats",
    "JOB_MODE_ID_LIST",
    "JOB_MODE_SEARCH",
    "JOB_PLATFORM",
    "enrich_records",
    "load_dianshi_records",
    "maybe_enrich_records",
    "record_from_reference",
]
