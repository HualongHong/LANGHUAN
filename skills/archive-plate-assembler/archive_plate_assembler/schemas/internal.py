"""管道内部中间态模型。

各处理节点间传递的数据结构；所有模型允许额外字段（extra="allow"），
便于后续演进时附加临时信息而不破坏既有节点。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from .species import SpeciesRecord


class PageKind(str, Enum):
    """页面类型。"""

    IMAGE_ONLY = "纯图页"
    MIXED = "图文混合页"
    UNKNOWN = "未知"


class FieldStatus(str, Enum):
    """字段核对结论。"""

    CONFIRMED = "确认"
    CORRECTED = "修正"
    UNCERTAIN = "存疑"


class PageAsset(BaseModel):
    """预处理产出的单页资产。"""

    model_config = ConfigDict(extra="allow")

    page_number: int
    image_path: str
    kind: PageKind = PageKind.UNKNOWN
    source_pdf: str


class LayoutRules(BaseModel):
    """版式规则（版式分析节点产出，需用户确认）。"""

    model_config = ConfigDict(extra="allow")

    template_description: str  # 版式模板的自然语言描述
    entry_boundary_rules: list[str] = Field(default_factory=list)  # 条目边界判定规则
    excluded_pages: list[int] = Field(default_factory=list)  # 前言等非物种排除页码
    confirmed: bool = False  # 用户是否已确认


class ImageRegion(BaseModel):
    """页面内的图像区域（模型输出，坐标可缺省）。"""

    model_config = ConfigDict(extra="allow")

    page: int
    bbox: Optional[list[float]] = None  # 相对坐标 0-1；模型可能只给自然语言位置描述
    description: str  # 模型对图像内容的描述
    image_type: str = "其他"  # 生态照/细节图/手绘图/实验室照/其他
    multi_species: bool = False


class EntryDraft(BaseModel):
    """条目组装阶段的草稿记录。"""

    model_config = ConfigDict(extra="allow")

    entry_id: str
    pages: list[int] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)  # 模型 A 按页输出的原始结构化结果
    record: Optional[SpeciesRecord] = None  # 组装后的对外记录
    image_regions: list[ImageRegion] = Field(default_factory=list)
    merged: bool = False  # 是否为跨页合并产物
    extraction_error: Optional[str] = None


class FieldCheck(BaseModel):
    """模型 B 对单个字段的核对结果。"""

    model_config = ConfigDict(extra="allow")

    field: str
    status: FieldStatus
    value_b: Optional[Any] = None  # 模型 B 的修正值
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str = ""


class VerificationReport(BaseModel):
    """双模型核对报告。"""

    model_config = ConfigDict(extra="allow")

    entry_id: str
    checks: list[FieldCheck] = Field(default_factory=list)
    disagreements: list[str] = Field(default_factory=list)  # 有分歧的字段名
    snapshot_a: str = ""  # 快照文件路径
    snapshot_b: str = ""
