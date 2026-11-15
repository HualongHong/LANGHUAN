# 黄金测试集

构成：3 本不同版式图鉴 × 20 个物种页面，生物学专业人员标注全部 JSON 字段 + 参考抠图。

必须覆盖：多物种同图、跨页条目、含命名人学名、含异名条目。

标注格式与《初始设定书》§2.2 输出 Schema 对齐，供 `evaluation` 模块自动比对。

> M1 期间并行启动构建。实际扫描件与标注文件不纳入版本管理（见 .gitignore）。

LabelU 矩形标注与 review_ui 字段复核的交接见 [LABELU_WORKFLOW.md](LABELU_WORKFLOW.md)，桥接入口为 `scripts/labelu_bridge.py`。MinerU 解码器降本 A/B 实测见 [DECODER_BENCHMARK.md](DECODER_BENCHMARK.md)。

## 字段级评估

```bash
python -m archive_plate_assembler.evaluation.golden \
  --gold corrected_species.json \
  --predicted species.json \
  --output evaluation.json
```

按 page + 规范化 scientific_name 一对一匹配，重复键或非法黄金记录报错。报告含 Schema 合规率、未匹配清单、overall 与各字段 TP/FP/FN、precision/recall/F1；覆盖文本、异名、阶元、表格、图片数量和图片字节 SHA-256。未匹配黄金计 FN、未匹配预测计 FP，非法预测单列且不进入字段指标。
