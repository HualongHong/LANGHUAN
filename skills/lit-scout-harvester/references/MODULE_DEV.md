# 模块开发指南

本文件说明如何为 **Lit-Scout-Harvester** 添加新的解析器、可插拔模块和领域 schema。

---

## 1. 代码规范

- 使用 Python 3.8+ 兼容语法；必要时使用 `from __future__ import annotations`。
- 所有异步模块接口继承 `src.modules.base.ExtractionModule`。
- 模块内部不得吞掉未处理异常；建议捕获后向 `ctx.add_flag(...)` 添加标记。
- LLM 调用统一通过 `src.core.llm_client.LLMClient`，自动获得请求缓存与重试。
- 配置项通过 Pydantic 在 `src.core.config_loader` 中校验；模块配置使用 `ModuleConfig(extra="allow")`，可直接读取任意额外字段。

---

## 2. 添加可插拔提取模块

最小示例：

```python
from src.core.context import PipelineContext
from src.modules.base import ExtractionModule

class MyModule(ExtractionModule):
    name = "my_module"
    version = "0.1.0"

    async def preprocess(self, ctx: PipelineContext, config: dict) -> bool:
        # 返回 False 可跳过该模块，避免昂贵调用
        return ctx.raw.get("AB") is not None

    async def process(self, ctx: PipelineContext, config: dict) -> dict:
        # 读取骨架结果
        addresses = ctx.processed.get("author_addresses", [])
        # 执行提取
        result = {"my_field": {"value": "example", "confidence": 0.9}}
        return result
```

在 `config/default.yaml` 中注册：

```yaml
modules:
  my_module:
    enabled: true
    my_param: 42
```

---

## 3. 添加领域 Schema

1. 在 `src/schemas/` 下新建 JSON 文件，参考 `sci_edu.json` / `paleoclimate.json`。
2. 字段类型支持 `str` 与 `list[str]`。
3. 在配置中启用：

```yaml
modules:
  term_extractor:
    enabled: true
    schema_path: "src/schemas/my_domain.json"
```

或使用预置子类：

```yaml
modules:
  sci_edu_extractor:
    enabled: true
    prescreen_classifier: "is_sci_edu"
```

---

## 4. 作者-地址恢复扩展

`src.parsers.author_address.restore_author_address` 支持通过 `options` 控制解析行为：

```python
restore_author_address(
    c1_field=c1,
    au_list=au,
    af_list=af,
    options={"use_inst_city_map": True},
    inst_city_map=InstitutionCityMap(),
)
```

如需新增 C1 格式模式，修改 `_iter_blocks` 与 `_parse_address_block`。

---

## 5. 测试

- 单元测试放在 `tests/unit/test_<module>.py`。
- 使用 `pytest` + `pytest-asyncio`。
- 涉及 LLM 的测试必须 mock `LLMClient.chat`。
- 涉及外部 HTTP 的测试必须 mock 对应客户端。

运行全部测试：

```bash
python -m pytest tests/unit -v
```

---

## 6. 提交前检查

- `python -m pytest tests/unit` 通过
- 新增/修改的配置文件可用 `load_config()` 加载
- 环境变量不硬编码，使用 `.env` 或配置插值
