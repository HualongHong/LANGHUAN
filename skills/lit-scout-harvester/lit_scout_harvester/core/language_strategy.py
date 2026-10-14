"""Global output-language strategy.

Resolves the effective ``output_language`` from:
- explicit configuration
- datasource (WOS defaults to English, CNKI defaults to Chinese)
- interactive CLI confirmation for CNKI
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from lit_scout_harvester.core.config_loader import PipelineConfig
from lit_scout_harvester.parsers.data_source import detect_datasource

logger = logging.getLogger(__name__)


CNKI_LANGUAGE_OPTIONS = {
    "1": ("zh", "中文输出"),
    "2": ("en", "英文输出"),
    "3": ("bilingual", "中英双语"),
    "4": ("raw", "保持原始"),
}


def _prompt_cnki_language(default: str = "zh") -> str:
    """Interactively ask the user for a CNKI output language strategy."""
    print("检测到CNKI中文数据源。请确认输出语言策略：")
    print("[1] 中文输出（提取中文术语，适合中文论文写作）")
    print("[2] 英文输出（提取英文术语并翻译，适合国际发表）")
    print("[3] 中英双语（同时输出中文和英文术语）")
    print("[4] 保持原始（不对语言做处理，直接提取）")
    default_key = "1"
    for key, (code, _) in CNKI_LANGUAGE_OPTIONS.items():
        if code == default:
            default_key = key
            break
    try:
        choice = input(f"请输入选项（1-4，默认{default_key}）: ").strip()
    except EOFError:
        choice = ""
    if not choice:
        choice = default_key
    if choice in CNKI_LANGUAGE_OPTIONS:
        return CNKI_LANGUAGE_OPTIONS[choice][0]
    logger.warning("无效选项 %r，使用默认语言 %s", choice, default)
    return default


def resolve_output_language(
    config: PipelineConfig,
    input_path: str,
    interactive: Optional[bool] = None,
) -> str:
    """Return the effective output language code.

    Args:
        config: Loaded pipeline configuration.
        input_path: Path to the input plaintext file.
        interactive: Whether to prompt the user. Defaults to ``sys.stdin.isatty()``.

    Returns:
        One of ``zh``, ``en``, ``bilingual``, ``raw``.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()

    explicit = config.skeleton.output_language
    if explicit is not None:
        return explicit

    datasource = config.skeleton.datasource
    if datasource == "auto_detect":
        datasource = detect_datasource(input_path)
        logger.info("Language resolution detected datasource: %s", datasource)

    if datasource == "cnki":
        cnki_cfg = config.skeleton.cnki
        if interactive and cnki_cfg.confirm_language:
            return _prompt_cnki_language(cnki_cfg.default_language)
        return cnki_cfg.default_language

    if datasource == "wos":
        return "en"

    return "raw"
