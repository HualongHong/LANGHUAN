# 多模型供应商配置指南

Lit-Scout-Harvester v0.3.0 支持多家 LLM 供应商，并允许按模块指定不同模型。

## 支持的供应商

| 供应商 | 配置名 | 需要 base_url | 备注 |
|--------|--------|---------------|------|
| DeepSeek | `deepseek` | 可选 | 默认供应商，OpenAI 兼容 |
| OpenAI / ChatGPT | `openai` | 可选 | 原生 OpenAI 兼容 |
| Kimi (Moonshot) | `kimi` | 可选 | OpenAI 兼容 |
| 通义千问 (Qwen) | `qwen` | 可选 | OpenAI 兼容 |
| MiniMax | `minimax` | 是 | 自有 API 格式 |
| Gemini (Google) | `gemini` | 否 | REST API |
| Claude (Anthropic) | `claude` / `anthropic` | 否 | Messages API |

## 环境变量

在 `.env` 中配置需要使用的 key：

```bash
# 默认供应商
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# 其他供应商（按需填写）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1

KIMI_API_KEY=sk-xxx
KIMI_BASE_URL=https://api.moonshot.cn/v1

QWEN_API_KEY=sk-xxx
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

MINIMAX_API_KEY=xxx
MINIMAX_GROUP_ID=xxx

GEMINI_API_KEY=xxx

ANTHROPIC_API_KEY=sk-ant-api-xxx
```

## 基础配置

```yaml
llm:
  provider: "deepseek"
  model: "deepseek-chat"
  api_key: "${DEEPSEEK_API_KEY}"
  base_url: "${DEEPSEEK_BASE_URL:https://api.deepseek.com/v1}"
  temperature: 0.0
  max_retries: 3

  # 主供应商失败时按顺序回退
  fallback_providers: ["qwen", "kimi"]
```

## 命名模型 profile

在 `llm.models` 中定义多个 profile，供不同模块引用：

```yaml
llm:
  models:
    extraction:
      provider: "deepseek"
      model: "deepseek-chat"
      api_key: "${DEEPSEEK_API_KEY}"
      base_url: "${DEEPSEEK_BASE_URL}"
      request_timeout: 120

    translation:
      provider: "kimi"
      model: "moonshot-v1-8k"
      api_key: "${KIMI_API_KEY}"
      base_url: "${KIMI_BASE_URL}"
      request_timeout: 120

    fallback:
      provider: "qwen"
      model: "qwen-turbo"
      api_key: "${QWEN_API_KEY}"
      base_url: "${QWEN_BASE_URL}"
```

## 按模块指定模型

在 `modules` 中使用 `model_profile`：

```yaml
modules:
  term_extractor:
    enabled: true
    schema_path: "lit_scout_harvester/schemas/sample_rst.json"
    model_profile: "extraction"

  sci_edu_extractor:
    enabled: true
    schema_path: "lit_scout_harvester/schemas/sci_edu.json"
    model_profile: "extraction"
```

## 回退策略

`fallback_providers` 中的名称对应 `llm.models` 中的 profile 名：

```yaml
llm:
  provider: "deepseek"
  fallback_providers: ["fallback"]

  models:
    fallback:
      provider: "qwen"
      model: "qwen-turbo"
```

当主供应商连续失败达到 `max_retries` 时，会自动切换到下一个 fallback profile。

## 验证

运行单元测试确认 provider 注册与回退逻辑正常：

```bash
python -m pytest tests/unit/test_llm_providers.py -q
```

## 注意事项

- 不需要的供应商 SDK 不必安装； core 实现基于 aiohttp/requests，无额外强制依赖。
- Gemini / Claude 的 `response_format={"type": "json_object"}` 可能不被支持， provider 内部会 fallback 到普通 text 并尝试解析 JSON。
- 各供应商的 rate limit 不同，大批量任务建议先小批量测试。
