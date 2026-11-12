"""文档解码器核心：MinerU 三档链 + 页文本映射（fail-soft）。

经 :class:`agentkit.mineru.MinerUClient` 解析每个源 PDF；任一档失败自动落
下一档，全部失败该文件返回 ``None``（调用方照常走视觉路径）。页文本映射
依赖 token 档 zip 里的 ``content_list.json``（块级 ``page_idx``）；flash /
local 档无页码信息，其 OCR 文本不进入逐页 prompt（整本注入会污染单页抽取）。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from agentkit.mineru import MinerUClient, MinerUDoc, MinerUError

from archive_plate_assembler.preprocessor import infer_page_start

logger = logging.getLogger(__name__)

ENV_DECODER = "APA_DECODER"
ENV_TEXT_ONLY = "APA_DECODER_TEXT_ONLY"

_MODE_MAP = {
    "auto": "auto",
    "flash": "flash",
    "token": "token",
    "mineru-local": "local",
}


def decoder_mode() -> str:
    """当前解码模式（env APA_DECODER，缺省 auto；非法值回落 auto）。

    合法值：auto / local（禁用 MinerU）/ flash / token / mineru-local。
    """
    raw = os.environ.get(ENV_DECODER, "auto").strip().lower()
    if raw == "local":
        return "local"
    return raw if raw in _MODE_MAP else "auto"


def mineru_tier_for(mode: str) -> str:
    """APA 模式名 → MinerUClient 档位名。"""
    return _MODE_MAP.get(mode, "auto")


def text_only_enabled() -> bool:
    return os.environ.get(ENV_TEXT_ONLY, "").strip() == "1"


@dataclass
class DecodedDoc:
    """单源 PDF 的解码产物（MinerUDoc 的 APA 视图）。"""

    source_pdf: str
    mode: str
    markdown: str
    task_id: str = ""
    page_start: int = 0
    content_list: Optional[List[Dict]] = None  # token 档：块级结构（含 page_idx）
    meta: Dict = field(default_factory=dict)

    def page_texts(self) -> Dict[int, str]:
        """物理页 → OCR 文本（join 文本/表格/标题块，跳过图片块）。

        返回 {} 表示本解码产物没有页码信息（flash/local 档）。
        """
        if not self.content_list:
            return {}
        by_page: Dict[int, List[str]] = {}
        for block in self.content_list:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "image":
                continue
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            page_idx = block.get("page_idx")
            if not isinstance(page_idx, int) or page_idx < 0:
                continue
            by_page.setdefault(page_idx, []).append(text)
        return {idx: "\n".join(parts) for idx, parts in by_page.items()}

    def page_has_images(self, page_idx: int) -> bool:
        """content_list 中该物理页是否含图片块（text_only 判据）。"""
        if not self.content_list:
            return True  # 无信息时保守视为有图
        return any(
            isinstance(b, dict) and b.get("type") == "image" and b.get("page_idx") == page_idx
            for b in self.content_list
        )

    def book_page_map(self) -> Dict[int, str]:
        """书页码 → OCR 文本（page_start 偏移已应用）；无页码信息时 {}。"""
        return {
            self.page_start + idx: text
            for idx, text in self.page_texts().items()
        }


def _to_decoded(source_pdf: str, doc: MinerUDoc, page_start: int) -> DecodedDoc:
    content_list: Optional[List[Dict]] = None
    if doc.content_list_json:
        try:
            parsed = json.loads(doc.content_list_json)
            if isinstance(parsed, list):
                content_list = [b for b in parsed if isinstance(b, dict)]
        except json.JSONDecodeError:
            logger.warning("decoder: content_list.json 解析失败，页映射不可用: %s", source_pdf)
    return DecodedDoc(
        source_pdf=source_pdf,
        mode=doc.mode,
        markdown=doc.markdown,
        task_id=doc.task_id,
        page_start=page_start,
        content_list=content_list,
        meta=dict(doc.meta),
    )


def decode_documents(
    pdf_paths: List[str],
    mode: Optional[str] = None,
    client: Optional[MinerUClient] = None,
) -> Dict[str, DecodedDoc]:
    """按管道同款排序解码全部源 PDF，返回 {source_pdf: DecodedDoc}。

    ``mode`` 缺省读 env（local → 直接返回 {}，零请求）。单个文件失败仅
    WARN 并跳过（该文件照常走视觉路径）。
    """
    mode = mode if mode is not None else decoder_mode()
    if mode == "local":
        return {}
    client = client or MinerUClient()
    decoded: Dict[str, DecodedDoc] = {}
    for pdf in sorted(pdf_paths, key=lambda p: infer_page_start(p) or 0):
        try:
            doc = client.parse_file(pdf, mode=mineru_tier_for(mode))
        except MinerUError as exc:
            logger.warning("decoder: MinerU 全档失败 %s（%s），回落视觉路径", pdf, exc)
            continue
        decoded[pdf] = _to_decoded(pdf, doc, infer_page_start(pdf) or 0)
        logger.info("decoder: %s 经 MinerU %s 档解码（task=%s）", pdf, doc.mode, doc.task_id)
    return decoded


__all__ = [
    "ENV_DECODER",
    "ENV_TEXT_ONLY",
    "DecodedDoc",
    "decode_documents",
    "decoder_mode",
    "mineru_tier_for",
    "text_only_enabled",
]
