"""节点 6 提示词：模型 B 对照原始页面图像逐字段核查的 prompt 构造。"""

from __future__ import annotations

import json

from archive_plate_assembler.schemas.internal import EntryDraft

_VERIFY_INSTRUCTIONS = """\
你是图鉴数据核查员（模型 B）。下面是模型 A 从扫描版图鉴页面提取的物种记录草稿（JSON）。
请对照随附的原始页面图像，逐字段核查该草稿是否忠实于原文。

核查要求：
1. 逐字段核对：chinese_name（中文名）、scientific_name（学名）、author（定名人）、
   synonyms（异名）、taxonomy.*（分类阶元，如 taxonomy.family、taxonomy.genus）、
   description（描述）、tables（表格）等草稿中出现的字段。
2. 仅在有明确原文依据时才给出修正值（value_b）；没有依据不得改动。
3. 图像模糊、原文缺失或无法判定时，status 标为"存疑"，并在 note 中说明原因。
4. 严禁臆造原文中不存在的内容。

输出格式：只输出一个 JSON 对象，不要输出任何其他文字：
{"checks": [
  {"field": "字段名", "status": "确认" | "修正" | "存疑",
   "value_b": 修正后的值（status 为"修正"时必填，否则为 null）,
   "confidence": 0 到 1 之间的置信度,
   "note": "核查依据说明"}
]}
"""


def build_verify_prompt(draft: EntryDraft) -> str:
    """为模型 B 构造逐字段核查 prompt（中文）。

    草稿优先取组装后的 record（model_dump_json）；record 为 None 时退化为
    模型 A 的原始输出 raw。
    """
    if draft.record is not None:
        # 剔除图片 base64（核查以随附页面图像为准，文本中的图片数据只会浪费 token）
        dump = draft.record.model_dump(
            mode="json", exclude={"images": {"__all__": {"data"}}})
        draft_json = json.dumps(dump, ensure_ascii=False, indent=2)
    else:
        draft_json = json.dumps(draft.raw, ensure_ascii=False, indent=2)
    return (
        f"{_VERIFY_INSTRUCTIONS}\n"
        f"条目 ID：{draft.entry_id}\n"
        f"待核查草稿 JSON：\n{draft_json}\n"
    )
