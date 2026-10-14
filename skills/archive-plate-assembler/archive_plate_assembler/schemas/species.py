"""对外物种档案 Schema（全链路输出结构的单一事实来源）。

最终交付给使用方的 JSON 结构由本模块的 pydantic v2 模型定义，
各管道节点在产出对外结果时必须以本模块为准。
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, Field, ValidationError


class ImageType(str, Enum):
    """物种配图类型。"""

    ECO = "生态照"
    DETAIL = "细节图"
    HAND_DRAWN = "手绘图"
    LAB = "实验室照"
    OTHER = "其他"


class Taxonomy(BaseModel):
    """分类阶元信息，各阶元均可缺省（图鉴可能不全）。"""

    kingdom: Optional[str] = None
    phylum: Optional[str] = None
    class_: Optional[str] = Field(default=None, alias="class")
    order: Optional[str] = None
    family: Optional[str] = None
    genus: Optional[str] = None

    model_config = {"populate_by_name": True}


class SpeciesImage(BaseModel):
    """物种配图条目。"""

    data: str  # base64 编码的图像数据
    type: ImageType = ImageType.OTHER
    page: Optional[int] = None  # 图像所在页码


class SpeciesTable(BaseModel):
    """物种相关表格（如量度表）。"""

    caption: Optional[str] = None
    data: list[list[str]] = Field(default_factory=list)


class SpeciesRecord(BaseModel):
    """对外物种档案记录。"""

    chinese_name: str
    scientific_name: str
    page: int
    author: Optional[str] = None
    synonyms: list[str] = Field(default_factory=list)
    taxonomy: Taxonomy = Field(default_factory=Taxonomy)
    description: Optional[str] = None
    images: list[SpeciesImage] = Field(default_factory=list)
    tables: list[SpeciesTable] = Field(default_factory=list)
    source_book: Optional[str] = None


def validate_species_list(
    data: list[dict],
) -> tuple[list[SpeciesRecord], list[str]]:
    """逐条校验物种记录，返回 (合法记录, 错误信息列表)。

    单条记录校验失败不会中断整体流程，仅收集错误信息，
    用于"输出结构合规率"指标统计。
    """
    records: list[SpeciesRecord] = []
    errors: list[str] = []
    for i, item in enumerate(data):
        try:
            records.append(SpeciesRecord.model_validate(item))
        except ValidationError as exc:
            errors.append(f"记录 #{i}: {exc}")
    return records, errors


def export_json(records: list[SpeciesRecord], path: Union[str, Path]) -> None:
    """按对外 Schema 将记录写出为 JSON 文件（不含内部字段）。"""
    payload = [
        r.model_dump(mode="json", by_alias=True, exclude_none=True) for r in records
    ]
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
