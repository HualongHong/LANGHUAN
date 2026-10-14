# Lit-Scout-Harvester 性能基准

> 环境：Python 3.8.10，Windows，`.venv` 虚拟环境  
> 数据源：`tests/fixtures/sample.wos`（432 条 WOS 记录）  
> 时间：2026-06-22

## 离线骨架（无 LLM 调用）

```bash
python scripts/benchmark.py --config config/default.yaml --limit 500 --output output/benchmark_skeleton_500.json
```

| 指标 | 数值 |
|------|------|
| 处理记录数 | 432 |
| 解析时间 | 0.058 s |
| 流水线时间 | 4.440 s |
| 端到端时间 | 4.498 s |
| **吞吐** | **~96 records/sec** |

## 真实 LLM（sample_rst schema）

```bash
python scripts/benchmark.py --config config/sample_rst.yaml --limit 20 --with-llm --output output/benchmark_sample_rst_20.json
```

| 指标 | 数值 |
|------|------|
| 处理记录数 | 20 |
| 解析时间 | 0.054 s |
| 流水线时间 | 13.935 s |
| 端到端时间 | 13.990 s |
| **吞吐** | **~1.43 records/sec** |
| 启用模块 | `term_extractor` |
| Provider | deepseek |

## 说明

- 离线吞吐主要取决于解析与字符串处理，96 records/sec 与历史值（~92 records/sec）基本持平。
- 含 LLM 的吞吐受 API 延迟与并发限制主导；当前 batch_size=10、max_concurrency=10 时约 1.4 records/sec。
- 如需提升 LLM 吞吐，可增大 `global.batch_size` 与 `llm.max_concurrency`，或启用多模型并行。
