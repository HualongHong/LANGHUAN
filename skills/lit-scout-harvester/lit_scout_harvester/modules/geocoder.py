"""Geocoding module for author addresses / study locations.

Two modes (``config['mode']``):

- ``legacy``（默认，向后兼容）：Nominatim (OpenStreetMap) 城市坐标地理编码，
  结果按内容哈希缓存。
- ``two_stage``（C10 抽取 ≠ 选择）：地名→国家两阶段映射。
  阶段 1 地名抽取：gazetteer 别名词典扫描 + 可选注入式 LLM 抽取器
  （``config['llm_extractor']``，同步/异步 callable，一律 fake 注入不触网）；
  阶段 2 国家映射：:class:`Gazetteer` 确定性 lookup。
  每条记录输出三列 ``place_raw`` / ``country_mapped``（ISO3，未命中为空）/
  ``mapping_method``（gazetteer_hit / llm_extracted / unmatched）；未命中实例
  进入「无法定位」清单（模块级 ``unmatched_report()``），记录级零命中挂
  ``geo_unlocated`` flag——不静默丢弃。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from geopy.geocoders import Nominatim
from geopy.location import Location

from lit_scout_harvester.core.cache_manager import CacheManager
from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.external.gazetteer import (
    Gazetteer,
    GazetteerEntry,
    normalize_place_name,
)
from lit_scout_harvester.modules.base import ExtractionModule


logger = logging.getLogger(__name__)


_TWO_STAGE_DEFAULT_TEXT_FIELDS = ("AB", "C1")
_PROCESSED_TEXT_KEYS = ("AB_zh", "abstract_zh", "segmented_abstract")
_ADDRESS_TEXT_KEYS = ("city", "country", "institution")


def _geocode_cache_key(city: str, country: Optional[str]) -> str:
    payload = f"geocode::{country or ''}::{city}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _has_latin(text: str) -> bool:
    return any("A" <= ch <= "z" for ch in text)


def _find_dictionary_hits(
    text: str, gazetteer: Gazetteer
) -> List[Tuple[str, GazetteerEntry]]:
    """阶段 1 词典扫描：在文本中找 gazetteer 别名实例（有序、去重叠、去重）。

    返回 ``(别名原文, 条目)`` 列表，按文中出现顺序排列；同实例只保留首次。
    拉丁别名用词边界匹配（``US`` 不会命中 ``use``），CJK 别名用子串匹配。
    重叠实例按「起始位置升序、长度降序」贪心取舍，结果逐字节确定。
    """
    normalized = normalize_place_name(text)
    if not normalized:
        return []
    raw_hits: List[Tuple[int, int, str, GazetteerEntry]] = []
    for alias_norm, alias_raw, entry in gazetteer.iter_aliases():
        if _has_latin(alias_norm):
            pattern = re.compile(r"(?<!\w)" + re.escape(alias_norm) + r"(?!\w)")
            matches = pattern.finditer(normalized)
        else:
            matches = re.finditer(re.escape(alias_norm), normalized)
        for m in matches:
            raw_hits.append((m.start(), m.end(), alias_raw, entry))
    raw_hits.sort(key=lambda h: (h[0], -(h[1] - h[0]), h[2]))
    taken: List[Tuple[int, int, str, GazetteerEntry]] = []
    occupied: List[Tuple[int, int]] = []
    for hit in raw_hits:
        start, end = hit[0], hit[1]
        if any(start < oe and end > os for os, oe in occupied):
            continue
        occupied.append((start, end))
        taken.append(hit)
    taken.sort(key=lambda h: h[0])
    seen = set()
    results: List[Tuple[str, GazetteerEntry]] = []
    for _, _, alias_raw, entry in taken:
        key = alias_raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append((alias_raw, entry))
    return results


class GeocoderModule(ExtractionModule):
    """Geocode city/country pairs to coordinates, or map places to countries."""

    name = "geocoder"
    version = "0.2.0"

    def __init__(
        self,
        cache: Optional[CacheManager] = None,
        gazetteer: Optional[Gazetteer] = None,
    ):
        self.cache = cache
        self._geolocator: Optional[Nominatim] = None
        self._rate_limit_lock = asyncio.Lock()
        self._gazetteer = gazetteer
        # 两阶段模式：「无法定位」清单（C10：不静默丢弃）。
        self.unmatched_manifest: List[Dict[str, str]] = []
        self.unlocated_records: List[str] = []

    def _get_gazetteer(self, config: Dict[str, Any]) -> Gazetteer:
        if self._gazetteer is None:
            self._gazetteer = Gazetteer(config.get("gazetteer_path"))
        return self._gazetteer

    def unmatched_report(self) -> Dict[str, Any]:
        """两阶段模式累计的「无法定位」清单与计数。"""
        return {
            "unmatched_places": list(self.unmatched_manifest),
            "unmatched_count": len(self.unmatched_manifest),
            "unlocated_records": list(self.unlocated_records),
            "unlocated_count": len(self.unlocated_records),
        }

    def _get_cache(self, config: Dict[str, Any]) -> CacheManager:
        if self.cache is None:
            db_path = config.get("cache_db", "data/cache/geocode_cache.db")
            self.cache = CacheManager(db_path)
        return self.cache

    def _get_geolocator(self) -> Nominatim:
        if self._geolocator is None:
            self._geolocator = Nominatim(user_agent="lit-scout-harvester/0.1.0")
        return self._geolocator

    async def _geocode_one(
        self, city: str, country: Optional[str], include_coords: bool
    ) -> Dict[str, Any]:
        if not city or not city.strip():
            return {
                "city": city,
                "country": country,
                "latitude": None,
                "longitude": None,
                "display_name": None,
                "confidence": 0.0,
                "note": "missing_city",
            }

        cache = self.cache
        cache_key = _geocode_cache_key(city, country)
        cached = cache.get(cache_key)
        if cached is not None:
            return dict(json.loads(cached))

        query = f"{city}, {country}" if country else city
        try:
            async with self._rate_limit_lock:
                loop = asyncio.get_event_loop()
                location: Optional[Location] = await loop.run_in_executor(
                    None,
                    lambda: self._get_geolocator().geocode(
                        query, exactly_one=True, language="en"
                    ),
                )
                # Be polite to the free Nominatim service.
                await asyncio.sleep(1.0)
        except Exception as exc:
            logger.warning("Geocoding failed for '%s': %s", query, exc)
            location = None

        if location is None:
            result = {
                "city": city,
                "country": country,
                "latitude": None,
                "longitude": None,
                "display_name": None,
                "confidence": 0.0,
                "note": "not_found",
            }
        else:
            # Confidence heuristic based on location type.
            raw_type = (location.raw.get("type") or "").lower()
            confidence = 0.9 if raw_type in {"city", "town", "village"} else 0.7
            result = {
                "city": city,
                "country": country,
                "latitude": location.latitude if include_coords else None,
                "longitude": location.longitude if include_coords else None,
                "display_name": location.address,
                "confidence": confidence,
                "note": "ok",
            }

        cache.set(cache_key, json.dumps(result, ensure_ascii=False))
        return result

    async def process(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        mode = config.get("mode", "legacy")
        if mode == "two_stage":
            return await self._process_two_stage(ctx, config)
        if mode != "legacy":
            logger.warning("Unknown geocoder mode %r; falling back to legacy", mode)
        self._get_cache(config)
        include_coords = config.get("include_coordinates", True)
        addresses = ctx.processed.get("author_addresses", [])

        seen: set = set()
        results: List[Dict[str, Any]] = []
        for addr in addresses:
            city = addr.get("city")
            country = addr.get("country")
            key = (city, country)
            if not city or key in seen:
                continue
            seen.add(key)
            results.append(await self._geocode_one(city, country, include_coords))

        return {"geocoded_locations": results}

    # ------------------------------------------------------------------
    # C10 two-stage: place extraction (stage 1) -> country mapping (stage 2)
    # ------------------------------------------------------------------

    def _collect_text(self, ctx: PipelineContext, config: Dict[str, Any]) -> str:
        """汇集阶段 1 的扫描文本：配置的 raw 字段 + 译文/切分摘要 + 地址字段。"""
        segments: List[str] = []
        for field in config.get("text_fields") or _TWO_STAGE_DEFAULT_TEXT_FIELDS:
            value = ctx.raw.get(field)
            if isinstance(value, str) and value.strip():
                segments.append(value)
            elif isinstance(value, list):
                segments.extend(str(v) for v in value if v and str(v).strip())
        for key in _PROCESSED_TEXT_KEYS:
            value = ctx.processed.get(key)
            if isinstance(value, str) and value.strip():
                segments.append(value)
        for addr in ctx.processed.get("author_addresses") or []:
            if isinstance(addr, dict):
                part = " ".join(
                    str(addr.get(k)) for k in _ADDRESS_TEXT_KEYS if addr.get(k)
                )
                if part.strip():
                    segments.append(part)
        return "\n".join(segments)

    async def _extract_places_llm(
        self, text: str, config: Dict[str, Any]
    ) -> List[str]:
        """可选阶段 1 LLM 抽取：callable 一律由 config 注入，本模块不触网。"""
        extractor = config.get("llm_extractor")
        if extractor is None or not config.get("use_llm", True) or not text.strip():
            return []
        try:
            result = extractor(text)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            logger.warning("LLM place extraction failed: %s", exc)
            return []
        places = []
        for item in result or []:
            place = str(item).strip()
            if place:
                places.append(place)
        return places

    async def _process_two_stage(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        gazetteer = self._get_gazetteer(config)
        text = self._collect_text(ctx, config)

        rows: List[Dict[str, str]] = []
        seen_places = set()

        # 阶段 1a：gazetteer 别名词典扫描（确定性抽取）。
        for alias_raw, entry in _find_dictionary_hits(text, gazetteer):
            key = alias_raw.casefold()
            if key in seen_places:
                continue
            seen_places.add(key)
            rows.append(
                {
                    "place_raw": alias_raw,
                    "country_mapped": entry.country_code,
                    "mapping_method": "gazetteer_hit",
                }
            )

        # 阶段 1b：可选 LLM 抽取（注入式），词典已命中的实例不重复。
        for place in await self._extract_places_llm(text, config):
            key = place.casefold()
            if key in seen_places:
                continue
            seen_places.add(key)
            hit = gazetteer.lookup(place)
            if hit is not None:
                rows.append(
                    {
                        "place_raw": place,
                        "country_mapped": hit.country_code,
                        "mapping_method": "llm_extracted",
                    }
                )
            else:
                rows.append(
                    {
                        "place_raw": place,
                        "country_mapped": "",
                        "mapping_method": "unmatched",
                    }
                )
                self.unmatched_manifest.append(
                    {"record_id": ctx.record_id, "place_raw": place}
                )

        unmatched = [r["place_raw"] for r in rows if r["mapping_method"] == "unmatched"]
        if not any(r["country_mapped"] for r in rows):
            # 记录级零命中：flag 标记，进入下游「无法定位」处置，不静默丢弃。
            ctx.add_flag("geo_unlocated")
            self.unlocated_records.append(ctx.record_id)

        return {
            "place_raw": [r["place_raw"] for r in rows],
            "country_mapped": [r["country_mapped"] for r in rows],
            "mapping_method": [r["mapping_method"] for r in rows],
            "geo_unmatched": unmatched,
            "geo_unmatched_count": len(unmatched),
        }
