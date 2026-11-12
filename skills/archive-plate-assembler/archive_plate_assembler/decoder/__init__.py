"""文档解码器 —— APA × MinerU 对接层（方案见 dev-docs 2026-11-12 条目）。

把 MinerU 三档解析（flash 免登录 / token 精准 / local 本地引擎）接入图鉴
管道：节点 4 抽取时把高保真 OCR 文本注入 prompt（表现提升），可选对纯文本
页跳过视觉调用（降本，实验性）。平台不可用时整体回落既有 render+视觉
路径，管道产出与未接入时一致。

开关（环境变量）：
- ``APA_DECODER``：``auto``（默认，flash→token→local 依次尝试）/
  ``local``（完全禁用 MinerU，零请求零行为变化）/
  ``flash`` / ``token`` / ``mineru-local``（强制单档，调试用）。
- ``APA_DECODER_TEXT_ONLY``：``1`` 时对"解码出文本且无图片块"的页用纯文本
  调用角色 A（不附图像，省视觉 token）；默认关（保守）。
"""

from .core import (
    ENV_DECODER,
    ENV_TEXT_ONLY,
    DecodedDoc,
    decode_documents,
    decoder_mode,
    text_only_enabled,
)

__all__ = [
    "ENV_DECODER",
    "ENV_TEXT_ONLY",
    "DecodedDoc",
    "decode_documents",
    "decoder_mode",
    "text_only_enabled",
]
