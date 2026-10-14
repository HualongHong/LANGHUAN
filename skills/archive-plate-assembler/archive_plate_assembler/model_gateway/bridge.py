"""APA 宿主 Agent 工单桥（agent_bridge）：领域 kind 注册 + 队列默认值。

agentkit.bridge 只内置通用 ``chat`` 兜底，领域 kind 由技能侧注册。APA 按
各环节 prompt 期望的 JSON 结构注册四种 kind（注册顺序即分类优先级）：

- ``layout``   版式发现（模型 A）：期望 ``{"templates": [...], "non_species_pages_guess": [...]}``
- ``verify``   双模型校验（模型 B）：期望 ``{"checks": [...]}``
- ``relocate`` zoom-in 重定位（模型 A）：期望 ``{"found": bool, "bbox": [x0,y0,x1,y1]}``
- ``extract``  多模态抽取（模型 A）：期望 ``{"entries": [...], "page_has_species": bool}``

占位符一律取"安全空结果"，让 collect 遍沿 happy path 跑完、发现全部
工单，且不向管道注入误导数据：空 templates 会给一个最小占位模板（否则
auto_layout 的 confirm(choice=0) 会因 candidates 为空抛错）、空 entries、
空 checks、found=false（回退粗 bbox）。

队列目录默认 ``.apa_llm``（进程 cwd 下）；agent_run 的 collect/apply 模式
会显式把 AGENTKIT_LLM_DIR 指到 <workdir>/.apa_llm。
"""

from __future__ import annotations

import json
from typing import Optional

from agentkit.bridge import (
    ANSWERS_JSON,
    ANSWERS_JSONL,
    REQUESTS_FILE,
    load_answers,
    queue_stats,
    read_requests,
    register_kind,
)
from agentkit.llm.bridge_provider import AgentBridgeProvider as _GenericAgentBridgeProvider

DEFAULT_QUEUE_DIR = ".apa_llm"


def _layout_placeholder(prompt: str) -> Optional[str]:
    """版式发现工单：一个最小候选模板，保证 auto_layout 的 confirm(choice=0) 不抛错。"""
    if '"templates"' not in prompt:
        return None
    return json.dumps({
        "templates": [{
            "name": "placeholder",
            "description": "宿主待答占位版式（collect 遍占位，apply 遍以 answers.jsonl 答案为准）",
            "entry_boundary_rules": [],
            "sample_pages": [],
        }],
        "non_species_pages_guess": [],
    }, ensure_ascii=False)


def _verify_placeholder(prompt: str) -> Optional[str]:
    """校验工单：空 checks——无确认也无分歧，复核界面照常可人工核查。"""
    if '"checks"' not in prompt:
        return None
    return json.dumps({"checks": []}, ensure_ascii=False)


def _relocate_placeholder(prompt: str) -> Optional[str]:
    """重定位工单：found=false——zoom_in_relocate 返回 None，回退原粗 bbox。"""
    if '"found"' not in prompt:
        return None
    return json.dumps({"found": False, "bbox": None}, ensure_ascii=False)


def _extract_placeholder(prompt: str) -> Optional[str]:
    """抽取工单：空条目页——extract_page 返回空列表，管道继续收集后续工单。"""
    if '"entries"' not in prompt and "信息抽取助手" not in prompt:
        return None
    return json.dumps({"entries": [], "page_has_species": False}, ensure_ascii=False)


def register_apa_kinds() -> None:
    """向 agentkit.bridge 注册 APA 的领域 kind（同名覆盖，可重复调用）。

    匹配器只看各 prompt 独有的 JSON 键/角色自述，避免串扰：版式 prompt 含
    ``"templates"``；校验 prompt 含 ``"checks"``；重定位 prompt 含 ``"found"``；
    抽取 prompt 含 ``"entries"`` 与"信息抽取助手"（注意抽取 prompt 里也有
    "版式模板"字样，故 layout 匹配器只能用 ``"templates"`` 键）。
    """
    register_kind("layout", match=lambda text: '"templates"' in text,
                  placeholder=_layout_placeholder)
    register_kind("verify", match=lambda text: '"checks"' in text,
                  placeholder=_verify_placeholder)
    register_kind("relocate", match=lambda text: '"found"' in text and '"bbox"' in text,
                  placeholder=_relocate_placeholder)
    register_kind("extract", match=lambda text: '"entries"' in text or "信息抽取助手" in text,
                  placeholder=_extract_placeholder)


register_apa_kinds()


class AgentBridgeProvider(_GenericAgentBridgeProvider):
    """APA agent-bridge provider：通用队列引擎 + APA 默认队列目录 .apa_llm。"""

    default_queue_dir = DEFAULT_QUEUE_DIR


__all__ = [
    "ANSWERS_JSON",
    "ANSWERS_JSONL",
    "DEFAULT_QUEUE_DIR",
    "REQUESTS_FILE",
    "AgentBridgeProvider",
    "load_answers",
    "queue_stats",
    "read_requests",
    "register_apa_kinds",
]
