"""节点 4 抽取 prompt 构造。

将版式规则（LayoutRules）注入中文 prompt，约束模型 A（MiniMax）
按固定 JSON Schema 输出当前页的物种条目。
"""

from __future__ import annotations

from archive_plate_assembler.schemas.internal import LayoutRules

_JSON_SCHEMA_EXAMPLE = """{
  "entries": [
    {
      "chinese_name": "赤链蛇",
      "scientific_name": "Lycodon rufozonatus",
      "author": "Cantor, 1842",
      "synonyms": ["Dinodon rufozonatum"],
      "taxonomy": {"kingdom": "动物界", "phylum": "脊索动物门", "class": "爬行纲",
                    "order": "有鳞目", "family": "游蛇科", "genus": "链蛇属"},
      "description": "已修复语病、无 OCR 错别字的通顺纯文本……",
      "tables": [{"caption": "量度表", "data": [["全长", "约 1 米"], ["尾长", "约 20 厘米"]]}],
      "images": [{"description": "成体背面生态照", "image_type": "生态照",
                  "multi_species": false, "bbox": [0.05, 0.10, 0.45, 0.40]}],
      "continued_from_previous_page": false,
      "continues_to_next_page": false
    }
  ],
  "page_has_species": true
}"""


def build_extract_prompt(
    rules: LayoutRules, page_number: int, taxon_hint: str | None = None
) -> str:
    """构造单页信息抽取 prompt。

    - 注入 rules.template_description 与 rules.entry_boundary_rules；
    - 约束输出为固定 JSON 结构（见 _JSON_SCHEMA_EXAMPLE）；
    - page_number 用于告诉模型当前抽取的是扫描 PDF 的第几页；
    - taxon_hint 非空时在末尾追加目标类群提示（提升该类群条目召回，
      并约束 bbox 紧贴单个目标主体）；为 None 时 prompt 与原状完全一致。
    """
    boundary_rules = "\n".join(f"  {i + 1}. {rule}" for i, rule in enumerate(rules.entry_boundary_rules))
    if not boundary_rules:
        boundary_rules = "  （无额外边界规则）"

    prompt = f"""你是中文动物图鉴信息抽取助手。请阅读所附扫描页图像（全书第 {page_number} 页），抽取其中所有物种条目。

【版式模板描述】
{rules.template_description}

【条目边界判定规则】
{boundary_rules}

【抽取要求】
1. 斜体或字体明显不同的外文是学名（拉丁名）。scientific_name 只保留属名+种加词两个词；命名人及年份（如 "Cantor, 1842"）务必拆出放到 author 字段，不要混进 scientific_name；查无命名人时 author 用空串 ""。
2. description 为通顺的简体中文纯文本：同步修复原文语病、去除 OCR 错别字与乱码，不保留任何 Markdown 或 HTML 标记。
3. 页面中的表格逐张抽取为 tables：caption 为表题（无表题用空串），data 为 [[键, 值], ...] 的键值对数组。
4. 页面中属于该条目的插图/照片逐张列入 images：image_type 只能从 "生态照/细节图/手绘图/实验室照/其他" 中选一个；一张图含多个物种时 multi_species 为 true；bbox 为图像在页面中的相对坐标 [x0, y0, x1, y1]（取值 0-1），无法定位时填 null。
5. 条目文字从上一页延续而来时 continued_from_previous_page 为 true；条目文字未结束、延续到下一页时 continues_to_next_page 为 true。
6. 前言、目录、索引、版权页等非物种内容不产出任何条目，此时 entries 为空数组且 page_has_species 为 false。

【输出格式】只输出一个 JSON 对象，结构如下（字段名与层级必须完全一致）：
{_JSON_SCHEMA_EXAMPLE}"""
    if taxon_hint:
        prompt += (
            f"\n\n【目标类群提示】本书目标类群为：{taxon_hint}。"
            "凡属于该类群的物种条目与其标本/生态图像都必须抽取，不要遗漏；"
            "图像区域的 bbox 应紧贴单个目标主体，不得混入相邻标本或页眉页脚。"
        )
    return prompt
