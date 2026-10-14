"""Configuration loader for Lit-Scout-Harvester.

Thin shim over :mod:`agentkit.config` (extracted in LangHuan Phase 2 P2).

Supports:
- YAML configuration files with Pydantic validation
- Configuration inheritance via ``extends``
- Environment variable interpolation via ``${VAR}`` or ``${VAR:default}``
- Automatic loading of a ``.env`` file from the project root

行为保持说明（设计红线的有意豁免）：LSH 的历史行为是 **import 本模块即
加载项目根 .env（override=True）**。agentkit.config 本身无任何 import 期
副作用；该副作用被明确保留在本技能侧 shim 中（见下方 ``load_env`` 调用），
以保证 LSH 全部调用方与 137 个单元测试零修改通过。新技能不应模仿此模式，
而应显式调用 ``agentkit.config.load_env``。

The YAML machinery (extends / deep merge / ``${VAR:default}`` interpolation)
lives in agentkit; the Pydantic section models below are LSH-specific and stay
here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field, ValidationError, model_validator
from pydantic.config import ConfigDict

from agentkit.config import ConfigError
from agentkit.config import deep_merge as _deep_merge
from agentkit.config import interpolate as _interpolate
from agentkit.config import load_env, load_yaml

# 见模块 docstring：保留 LSH 遗留的 import 期 .env 自动加载行为（项目根
# .env，override=True），副作用仅存在于技能侧 shim。
load_env(Path(__file__).resolve().parents[2] / ".env", override=True)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class GlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_concurrency: int = 10
    batch_size: int = 50
    cache_db: str = "data/cache/content_cache.db"
    output_dir: str = "./output"
    generate_citation: bool = True
    log_level: str = "INFO"
    progress_report_interval: int = 100


class LLMProfileConfig(BaseModel):
    """A named provider profile (e.g. primary, fallback, extraction, translation)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    api_key: str
    base_url: str
    temperature: float = 0.0
    request_timeout: int = 120


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    temperature: float = 0.0
    max_retries: int = 3
    retry_delay: float = 2.0
    request_timeout: int = 120
    max_tokens_per_request: int = 4096
    max_connections: int = 20
    max_threads: int = 4
    connection_keepalive: bool = True
    fallback_providers: List[str] = Field(default_factory=list)
    models: Dict[str, LLMProfileConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_api_key_or_models(self):
        if not self.api_key and not self.models:
            raise ValueError("Either llm.api_key or at least one llm.models profile is required")
        return self


class AddressParserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extract_country: bool = True
    extract_city: bool = True
    extract_institution: bool = True
    extract_postal_code: bool = True
    use_inst_city_map: bool = True
    fallback_to_llm: bool = False


class BibliometrixConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rscript_path: str = "Rscript"
    auto_install_bibliometrix: bool = False
    output_fields: Optional[List[str]] = None


class CNKIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_language: bool = True
    default_language: str = "zh"  # zh | en | bilingual | raw
    export_format: str = "auto"  # auto | refworks | endnote | custom


class SkeletonConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_data_source: str = "native_parser"  # native_parser | bibliometrix
    datasource: str = "wos"  # wos | cnki | openalex | scopus | pubmed | auto_detect
    output_language: Optional[str] = None  # zh | en | bilingual | raw | null=auto
    bibliometrix: BibliometrixConfig = Field(default_factory=BibliometrixConfig)
    cnki: CNKIConfig = Field(default_factory=CNKIConfig)
    wos_fields: List[str] = Field(
        default_factory=lambda: [
            "PT",
            "AU",
            "AF",
            "TI",
            "AB",
            "SO",
            "DT",
            "DE",
            "ID",
            "C1",
            "RP",
            "PY",
            "DI",
            "LA",
            "SC",
            "UT",
        ]
    )
    language_detection: bool = True
    language_model_path: str = "data/cache/lid.176.ftz"
    translate_to_chinese: bool = True
    segment_abstract: bool = True
    restore_author_address: bool = True
    address_parser: AddressParserConfig = Field(default_factory=AddressParserConfig)

    @model_validator(mode="after")
    def check_output_language(self):
        valid = {None, "zh", "en", "bilingual", "raw"}
        if self.output_language not in valid:
            raise ValueError(
                f"skeleton.output_language must be one of {valid}, got {self.output_language!r}"
            )
        return self


class ModuleConfig(BaseModel):
    """Generic module configuration; allows module-specific extra keys."""

    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    model_profile: Optional[str] = None  # name of an llm.models profile


class PostprocessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_confidence: float = 0.3
    deduplicate_terms: bool = True
    filter_generic_terms: bool = True
    generic_term_threshold: float = 0.5
    term_style: str = "concise_academic"  # concise_academic | descriptive | raw
    max_words_per_term: int = 4
    truncate_long_terms: bool = True


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    queue_db: str = "data/review_queue.db"
    auto_flag_low_confidence: bool = True
    auto_flag_gdp_discrepancy: float = 0.3
    auto_flag_parser_errors: bool = True
    max_queue_size: int = 10000


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = "csv"
    csv_encoding: str = "utf-8-sig"
    csv_separator: str = ","
    include_raw_fields: bool = False
    include_confidence: bool = True
    include_flags: bool = True
    legacy_mode: bool = False
    list_separator: str = "; "
    flatten_author_addresses: bool = True
    dict_serialization: str = "json"  # json | flatten | omit


class LoopEngineeringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    auto_collect_feedback: bool = True
    auto_plan: bool = True
    auto_approve: bool = False
    auto_execute: bool = False
    auto_evaluate: bool = True
    feedback_retention_days: int = 90
    max_pending_plans: int = 10
    men_issues_path: str = "data/Men_issues.md"
    feedback_dir: str = "feedback"
    update_plan_dir: str = "update_plan"


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0"
    global_: GlobalConfig = Field(default_factory=GlobalConfig, alias="global")
    llm: LLMConfig
    skeleton: SkeletonConfig = Field(default_factory=SkeletonConfig)
    modules: Dict[str, ModuleConfig]
    postprocess: PostprocessConfig = Field(default_factory=PostprocessConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    loop_engineering: LoopEngineeringConfig = Field(default_factory=LoopEngineeringConfig)


# ---------------------------------------------------------------------------
# Caliber registry (C12 single source of truth for口径/thresholds)
# ---------------------------------------------------------------------------


class DedupRule(BaseModel):
    """How duplicate record ids are handled across the pipeline."""

    model_config = ConfigDict(extra="allow")

    distinct_key: str = "record_id"
    strategy: str = "raise"  # raise | keep_first
    keep: str = "first"  # first | last (only when strategy = keep_first)


class CountingRules(BaseModel):
    """Country/affiliation counting modes; fractional is the primary口径."""

    model_config = ConfigDict(extra="allow")

    default_mode: str = "fractional"  # fractional | whole_count | first_author
    modes: List[str] = Field(
        default_factory=lambda: ["fractional", "whole_count", "first_author"]
    )


class MultiLabelRules(BaseModel):
    """Multi-label protocol defaults (C9): fixed N*, never model-decided."""

    model_config = ConfigDict(extra="allow")

    default_n_star: int = 1
    max_n_star: int = 10


class AcceptanceThresholds(BaseModel):
    """Per-field acceptance gates (C8), in percentage points."""

    model_config = ConfigDict(extra="allow")

    net_gain_threshold_pt: float = 15.0  # >= : field may use the model route
    weak_net_gain_threshold_pt: float = 5.0  # < : give up model route (rules/manual)
    majority_share_threshold_pct: float = 60.0  # high mode share -> rule route viable
    sample_rate: float = 0.05  # manual spot-check fraction of production output


class AuditRules(BaseModel):
    """模型外验收与标签诊断口径（C5/C6）。

    抽检兜底必须独立于模型自评：固定比例随机抽检（种子固定可复现）+
    难点字段全检；置信度仅作排序参考，不作闸门（实证：答错时置信度
    均值仍 0.727，校准差仅 0.173）。
    """

    model_config = ConfigDict(extra="allow")

    audit_rate: float = 0.05  # 固定比例随机抽检比例
    default_seed: int = 20261001  # 抽检/诊断默认种子（固定可复现）
    hard_fields: List[str] = Field(default_factory=list)  # 难点字段：检出即全检入队
    residual_share_min: float = 0.30  # 残差桶占比下限（C6）
    residual_cooc_min: float = 0.3  # 残差桶平均共现率下限（C6）
    residual_cooc_max: float = 0.6  # 残差桶平均共现率上限（C6）


class LexiconSpec(BaseModel):
    """某字段的确定性词典标注配置（3B）。

    ``terms`` 为内联词表（标签 → 命中词列表），``path`` 指向 JSON 词表文件
    （同样为 标签 → 命中词列表 的映射）；二者可并存并集。``source_fields``
    指定在记录的哪些原始字段里做匹配（默认题录/摘要/关键词）。
    """

    model_config = ConfigDict(extra="allow")

    terms: Dict[str, List[str]] = Field(default_factory=dict)
    path: Optional[str] = None
    source_fields: List[str] = Field(
        default_factory=lambda: ["TI", "AB", "DE", "ID"]
    )


class FieldStrategyRule(BaseModel):
    """字段级有序策略（3B）：``order`` 列出引擎执行顺序。

    例：``["lexicon", "decision"]`` 表示词典先标注，未命中的记录才进入
    decision_select；``["lexicon", "llm"]`` 表示未命中交给抽取式 LLM
    （term_extractor）。``lexicon`` 为本字段的词典配置；``candidates.k``
    为候选收窄（3C）的默认候选数。
    """

    model_config = ConfigDict(extra="allow")

    order: List[str] = Field(default_factory=lambda: ["lexicon", "decision"])
    lexicon: LexiconSpec = Field(default_factory=LexiconSpec)
    candidates: Dict[str, Any] = Field(default_factory=dict)


class StrategyRules(BaseModel):
    """有序策略登记表：字段 → 字段级策略规则。"""

    model_config = ConfigDict(extra="allow")

    fields: Dict[str, FieldStrategyRule] = Field(default_factory=dict)


class ReviewEnqueueRules(BaseModel):
    """复核入队方式开关（阶段 2B 收尾）。

    ``batch_enqueue=True``（默认）：pipeline 收尾时用全部 contexts 调
    ``ReviewQueue.enqueue_contexts()``（三路入队：固定种子随机抽检 +
    难点字段全检 + 可选低置信度），需要全局视野。``False`` 回退为旧的
    逐 ctx flag 入队。
    """

    model_config = ConfigDict(extra="allow")

    batch_enqueue: bool = True


class CaliberConfig(BaseModel):
    """口径唯一化登记簿：ID 字段、去重规则、计数口径、多标签 N*、验收判定线。

    所有阈值/口径集中于此（config/caliber.json），脚本只读取不硬编码。
    未知键保留（extra="allow"）以便向前扩展。
    """

    model_config = ConfigDict(extra="allow")

    version: str = "1.0"
    id_fields: List[str] = Field(default_factory=lambda: ["UT", "DI", "TI"])
    record_id_field: str = "record_id"
    dedup: DedupRule = Field(default_factory=DedupRule)
    counting: CountingRules = Field(default_factory=CountingRules)
    multilabel: MultiLabelRules = Field(default_factory=MultiLabelRules)
    acceptance: AcceptanceThresholds = Field(default_factory=AcceptanceThresholds)
    audit: AuditRules = Field(default_factory=AuditRules)
    strategy: StrategyRules = Field(default_factory=StrategyRules)
    review: ReviewEnqueueRules = Field(default_factory=ReviewEnqueueRules)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ConfigPath = Union[str, Path]


def load_config(path: ConfigPath) -> PipelineConfig:
    """Load and validate a pipeline configuration file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A validated ``PipelineConfig`` instance.

    Raises:
        ConfigError: If the file cannot be read or merged.
        ValidationError: If the content fails Pydantic validation.
    """
    path = Path(path)
    interpolated = load_yaml(path)

    try:
        return PipelineConfig(**interpolated)
    except ValidationError as exc:
        # Re-raise with a more helpful prefix containing the file path.
        raise ConfigError(f"Invalid configuration in {path}: {exc}") from exc


def get_config_dict(config: PipelineConfig) -> Dict[str, Any]:
    """Return the serialized dictionary form of a validated config."""
    return config.model_dump(by_alias=True)


def default_caliber_path() -> Path:
    """Default location of the caliber registry: ``config/caliber.json``."""
    return Path(__file__).resolve().parents[2] / "config" / "caliber.json"


def load_caliber(path: Optional[ConfigPath] = None) -> CaliberConfig:
    """Load the caliber registry (single source of truth for口径/thresholds).

    Args:
        path: Path to ``caliber.json``; defaults to ``config/caliber.json``
            next to the skill root.

    Returns:
        A validated ``CaliberConfig``. When the file is missing, built-in
        defaults are returned — a missing file never raises. Keys missing
        from the file fall back to defaults; unknown keys are preserved.

    Raises:
        ConfigError: If the file exists but is not a valid JSON object.
    """
    path = Path(path) if path is not None else default_caliber_path()
    if not path.exists():
        return CaliberConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Invalid caliber file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Invalid caliber file {path}: top level must be a JSON object")
    return CaliberConfig(**data)
