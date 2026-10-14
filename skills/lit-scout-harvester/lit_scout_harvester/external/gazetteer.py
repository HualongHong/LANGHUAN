"""Offline gazetteer for two-stage geographic mapping (C10 stage 2).

GeoNames 级离线地名库的最小实现：加载地名条目（含中英文别名），建立归一化
别名索引，提供确定性的 ``lookup(place_name) -> 国家码/名`` 映射。纯离线、零网络；
同名别名先注册者优先（数据文件里先出现的条目胜出）。

数据文件格式（路径可配，JSON 或 CSV 按扩展名区分）：

JSON::

    {
      "version": "2026-10-13",
      "places": [
        {
          "name": "Beijing",
          "country_code": "CHN",
          "country_name": "China",
          "type": "city",
          "aliases": ["北京", "Peking"]
        }
      ]
    }

CSV（首行表头 ``name,country_code,country_name,type,aliases``，aliases 用 ``;`` 分隔）::

    name,country_code,country_name,type,aliases
    Beijing,CHN,China,city,北京;Peking

未显式提供路径时加载内置样例库 ``data/gazetteer/sample_gazetteer.json``
（覆盖常见国家/首都/地区别名，含中英文），供测试与开箱即用。生产使用请换成
GeoNames 全量导出并放在配置路径。
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple, Union

BUILTIN_DATA_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "gazetteer" / "sample_gazetteer.json"
)

_NON_ALNUM_RE = re.compile(r"[^\w]+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_place_name(name: str) -> str:
    """归一化地名：去重音符号、casefold、非字母数字转空格、压缩空白。

    中英文均适用（CJK 字符不受 casefold 影响，按原样保留）。
    """
    decomposed = unicodedata.normalize("NFKD", str(name))
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = _NON_ALNUM_RE.sub(" ", folded.casefold())
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


@dataclass(frozen=True)
class GazetteerEntry:
    """一条地名条目：规范名 + 所属国家 + 可选别名。"""

    name: str
    country_code: str
    country_name: str
    type: str = "place"
    aliases: Tuple[str, ...] = ()


@dataclass(frozen=True)
class GazetteerHit:
    """``lookup`` 的命中结果（国家码/名 + 命中详情，可审计）。"""

    place: str  # 条目规范名
    country_code: str  # ISO 3166-1 alpha-3
    country_name: str
    matched_alias: str  # 实际命中的别名原文
    entry_type: str = "place"


class Gazetteer:
    """离线地名 → 国家映射表（别名索引，大小写/标点/重音不敏感）。

    ``lookup`` 完全确定性：同输入恒同输出，逐字节一致。
    """

    def __init__(self, data_path: Optional[Union[str, Path]] = None):
        path = Path(data_path) if data_path is not None else BUILTIN_DATA_PATH
        self.data_path = path
        self.entries: List[GazetteerEntry] = []
        self._index: Dict[str, Tuple[str, GazetteerEntry]] = {}
        self._load(path)

    # ------------------------------------------------------------------ load

    def _load(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"gazetteer data file not found: {path}")
        if path.suffix.lower() == ".csv":
            entries = self._parse_csv(path)
        else:
            entries = self._parse_json(path)
        for entry in entries:
            self.entries.append(entry)
            for alias in (entry.name, *entry.aliases):
                key = normalize_place_name(alias)
                if key and key not in self._index:
                    self._index[key] = (alias, entry)

    @staticmethod
    def _entry_from_dict(raw: Dict[str, object]) -> GazetteerEntry:
        aliases = raw.get("aliases") or []
        return GazetteerEntry(
            name=str(raw.get("name", "")).strip(),
            country_code=str(raw.get("country_code", "")).strip().upper(),
            country_name=str(raw.get("country_name", "")).strip(),
            type=str(raw.get("type", "place")).strip() or "place",
            aliases=tuple(str(a).strip() for a in aliases if str(a).strip()),
        )

    @classmethod
    def _parse_json(cls, path: Path) -> List[GazetteerEntry]:
        data = json.loads(path.read_text(encoding="utf-8"))
        places = data.get("places", []) if isinstance(data, dict) else data
        if not isinstance(places, list):
            raise ValueError(f"invalid gazetteer JSON {path}: 'places' must be a list")
        return [cls._entry_from_dict(p) for p in places]

    @classmethod
    def _parse_csv(cls, path: Path) -> List[GazetteerEntry]:
        entries: List[GazetteerEntry] = []
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                aliases = [a.strip() for a in (row.get("aliases") or "").split(";") if a.strip()]
                entries.append(
                    cls._entry_from_dict({**row, "aliases": aliases})
                )
        return entries

    # ------------------------------------------------------------------ query

    def lookup(self, place_name: Optional[str]) -> Optional[GazetteerHit]:
        """地名 → 国家映射：命中返回 ``GazetteerHit``，未命中返回 ``None``。

        大小写不敏感；别名归一（去标点/多余空格/重音符号）后精确匹配。
        """
        if not place_name:
            return None
        key = normalize_place_name(str(place_name))
        if not key:
            return None
        hit = self._index.get(key)
        if hit is None:
            return None
        alias_raw, entry = hit
        return GazetteerHit(
            place=entry.name,
            country_code=entry.country_code,
            country_name=entry.country_name,
            matched_alias=alias_raw,
            entry_type=entry.type,
        )

    def iter_aliases(self) -> Iterator[Tuple[str, str, GazetteerEntry]]:
        """产出 ``(归一别名, 别名原文, 条目)``，供阶段 1 词典扫描使用。"""
        for key, (alias_raw, entry) in self._index.items():
            yield key, alias_raw, entry

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, place_name: str) -> bool:
        return self.lookup(place_name) is not None
